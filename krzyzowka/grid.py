"""Wyszukanie obrysu siatki krzyżówki i rektyfikacja perspektywy.

Metoda: maska cienkich ciemnych struktur -> długie odcinki poziome/pionowe -> największa
spójna kratownica -> cztery skrajne linie (dopasowane odpornie do obwiedni kratownicy)
-> czworokąt -> homografia.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import cv2

from .image import gray, dark_mask, resize_long


@dataclass
class GridQuad:
    corners: np.ndarray  # 4x2 float32, kolejność: LG, PG, PD, LD (piksele obrazu wejściowego)
    width: int           # szerokość prostokąta docelowego (w pikselach obrazu wejściowego)
    height: int
    ink_thresh: int = 40         # próg odpowiedzi black-hat dla tuszu linii, wyznaczony z koloru ramki
    frame_gray: float = 0.0      # jasność tuszu linii siatki (mediana wzdłuż wykrytej kratownicy)
    frame_rgb: tuple = (0, 0, 0) # kolor tuszu linii siatki (mediana RGB)
    page_gray: float = 255.0     # jasność tła w otoczeniu linii


def frame_ink(g: np.ndarray, rgb: np.ndarray, lattice: np.ndarray, line_px: int) -> tuple[int, float, tuple, float]:
    """Kalibracja progu tuszu z koloru samej kratownicy (ramka zewnętrzna i linie mają ten sam tusz):
    mediana odpowiedzi black-hat na pikselach kratownicy, próg = 0.45 tej wartości (zakres 15..70).
    Zwraca (próg, jasność tuszu, kolor RGB tuszu, jasność tła obok linii)."""
    k = cv2.getStructuringElement(cv2.MORPH_RECT, (6 * line_px + 1, 6 * line_px + 1))
    bh = cv2.morphologyEx(g, cv2.MORPH_BLACKHAT, k)
    m = lattice > 0
    if m.sum() < 100:
        return 40, float(np.median(g)), (0, 0, 0), float(np.median(g))
    contrast = float(np.median(bh[m]))
    thresh = int(np.clip(round(0.45 * contrast), 15, 70))
    ink_gray = float(np.median(g[m]))
    ink_rgb = tuple(int(v) for v in np.median(rgb[m].reshape(-1, 3), axis=0))
    ring = cv2.dilate(lattice, cv2.getStructuringElement(cv2.MORPH_RECT, (4 * line_px + 1, 4 * line_px + 1))) > 0
    ring &= ~m
    page_gray = float(np.median(g[ring])) if ring.any() else 255.0
    return thresh, ink_gray, ink_rgb, page_gray


def thin_dark(g: np.ndarray, line_px: int, thresh: int = 40) -> np.ndarray:
    """Maska cienkich ciemnych struktur (linie, tekst) przez transformatę black-hat:
    szerokie ciemne lub szare obszary (ilustracje, tła kratek z opisami) nie są wykrywane."""
    k = cv2.getStructuringElement(cv2.MORPH_RECT, (6 * line_px + 1, 6 * line_px + 1))
    bh = cv2.morphologyEx(g, cv2.MORPH_BLACKHAT, k)
    return (bh > thresh).astype(np.uint8) * 255


def line_masks(mask: np.ndarray, min_len: int, slack: int = 3) -> tuple[np.ndarray, np.ndarray]:
    """Długie odcinki poziome i pionowe (otwarcie morfologiczne).
    Przed otwarciem maska jest pogrubiana w poprzek, by lekko pochylone linie nie rwały się."""
    kh = cv2.getStructuringElement(cv2.MORPH_RECT, (min_len, 1))
    kv = cv2.getStructuringElement(cv2.MORPH_RECT, (1, min_len))
    mh = cv2.dilate(mask, cv2.getStructuringElement(cv2.MORPH_RECT, (1, slack)))
    mv = cv2.dilate(mask, cv2.getStructuringElement(cv2.MORPH_RECT, (slack, 1)))
    return cv2.morphologyEx(mh, cv2.MORPH_OPEN, kh), cv2.morphologyEx(mv, cv2.MORPH_OPEN, kv)


def lattice_component(lines: np.ndarray, gap: int) -> np.ndarray:
    """Największa (wg pola obrysu) spójna składowa maski linii po domknięciu przerw."""
    closed = cv2.dilate(lines, cv2.getStructuringElement(cv2.MORPH_RECT, (gap, gap)))
    n, lab, stats, _ = cv2.connectedComponentsWithStats(closed, connectivity=8)
    if n <= 1:
        raise RuntimeError("nie znaleziono linii siatki")
    areas = stats[1:, cv2.CC_STAT_WIDTH] * stats[1:, cv2.CC_STAT_HEIGHT]
    best = 1 + int(np.argmax(areas))
    return cv2.bitwise_and(lines, (lab == best).astype(np.uint8) * 255)


def _ransac_line(xs: np.ndarray, ys: np.ndarray, tol: float, iters: int = 400, rng=None):
    """Prosta y = a*x + b o największym poparciu. Zwraca (a, b, maska_inlierów)."""
    rng = rng or np.random.default_rng(0)
    n = len(xs)
    best = (0.0, float(np.median(ys)), np.zeros(n, bool))
    if n < 2:
        return best
    for _ in range(iters):
        i, j = rng.choice(n, 2, replace=False)
        if xs[i] == xs[j]:
            continue
        a = (ys[j] - ys[i]) / (xs[j] - xs[i])
        b = ys[i] - a * xs[i]
        inl = np.abs(ys - (a * xs + b)) < tol
        if inl.sum() > best[2].sum():
            best = (a, b, inl)
    a, b, inl = best
    if inl.sum() >= 2:
        A = np.stack([xs[inl], np.ones(inl.sum())], axis=1)
        a, b = np.linalg.lstsq(A, ys[inl], rcond=None)[0]
        inl = np.abs(ys - (a * xs + b)) < tol
    return a, b, inl


def _extreme_line(xs: np.ndarray, ys: np.ndarray, tol: float, min_support: int, pick_min: bool):
    """Wśród prostych znalezionych sekwencyjnie RANSAC-iem wybiera skrajną (najmniejsze/największe
    położenie w środku zakresu) o poparciu >= min_support. Zwraca (a, b, maska_inlierów)."""
    xs = xs.astype(np.float64); ys = ys.astype(np.float64)
    cands = []
    rem = np.ones(len(xs), bool)
    xc = xs.mean()
    for _ in range(6):
        if rem.sum() < min_support:
            break
        a, b, inl = _ransac_line(xs[rem], ys[rem], tol)
        if inl.sum() < min_support:
            break
        idx = np.nonzero(rem)[0][inl]
        full = np.zeros(len(xs), bool); full[idx] = True
        cands.append((a, b, full))
        rem[idx] = False
    if not cands:
        raise RuntimeError("nie udało się dopasować linii skrajnej")
    key = lambda c: c[0] * xc + c[1]
    return min(cands, key=key) if pick_min else max(cands, key=key)


def _local_fit(xs, ys, inl, lo, hi, fallback, tol, min_pts):
    """Dopasowanie prostej do inlierów z przedziału [lo, hi); gdy ich mało, zwraca `fallback`."""
    m = inl & (xs >= lo) & (xs < hi)
    if m.sum() < min_pts:
        return fallback
    A = np.stack([xs[m], np.ones(m.sum())], axis=1)
    a, b = np.linalg.lstsq(A, ys[m], rcond=None)[0]
    return a, b


def _side_lines(pos, val, tol, extent, pick_min):
    """Dla jednej krawędzi siatki: prosta globalna oraz dwie proste lokalne (początek/koniec zakresu),
    by narożniki trafiały w rzeczywisty przebieg lekko wygiętej krawędzi."""
    pos = pos.astype(np.float64); val = val.astype(np.float64)
    a, b, inl = _extreme_line(pos, val, tol, int(0.2 * extent), pick_min)
    lo, hi = pos[inl].min(), pos[inl].max()
    mid = (lo + hi) / 2
    first = _local_fit(pos, val, inl, lo, mid, (a, b), tol, int(0.1 * extent))
    last = _local_fit(pos, val, inl, mid, hi + 1, (a, b), tol, int(0.1 * extent))
    return first, last


def _envelope(mask: np.ndarray, axis: int, first: bool):
    """Dla każdej kolumny (axis=0) lub wiersza (axis=1) pierwszy/ostatni piksel maski."""
    m = mask > 0
    if axis == 1:
        m = m.T
    has = m.any(axis=0)
    idx = np.where(first, m.argmax(axis=0), m.shape[0] - 1 - m[::-1].argmax(axis=0))
    pos = np.nonzero(has)[0]
    return pos, idx[has]


def _intersect(l1, l2):
    """Przecięcie prostych w postaci (a, b, c): a*x + b*y + c = 0."""
    p = np.cross(l1, l2)
    return p[:2] / p[2]


def find_grid(img_rgb: np.ndarray, work_long: int = 1600) -> GridQuad:
    small, s = resize_long(img_rgb, work_long)
    g = cv2.GaussianBlur(gray(small), (3, 3), 0)
    line_px = max(2, int(round(work_long / 500)))  # grubość linii siatki ~3 px przy 1600 px
    thin = thin_dark(g, line_px)
    min_len = int(0.025 * max(small.shape[:2]))
    hor, ver = line_masks(thin, min_len)
    lines = cv2.bitwise_or(hor, ver)
    lat = lattice_component(lines, gap=3 * line_px)
    # kalibracja progu tuszu z koloru znalezionej kratownicy (ramka zewnętrzna i linie mają ten sam tusz);
    # próg służy dalszym etapom (śledzenie linii, krawędzie, pasek). Obrys zostaje z pierwszego przebiegu:
    # niższy próg łączy kratownicę z elementami obok (nagłówek, pasek) i psuje skrajne proste.
    ink_thresh, frame_gray, frame_rgb, page_gray = frame_ink(g, small, lat, line_px)
    ys_all, xs_all = np.nonzero(lat)
    x0, x1 = xs_all.min(), xs_all.max(); y0, y1 = ys_all.min(), ys_all.max()
    tol = 2.0 * line_px
    # proste krawędzi: y = a*x + b (góra/dół), x = a*y + b (lewo/prawo); po dwie lokalne na krawędź
    cx, ty = _envelope(lat, 0, True)
    cx2, by = _envelope(lat, 0, False)
    ry, lx = _envelope(lat, 1, True)
    ry2, rx = _envelope(lat, 1, False)
    top_l, top_r = _side_lines(cx, ty, tol, x1 - x0, True)
    bot_l, bot_r = _side_lines(cx2, by, tol, x1 - x0, False)
    left_t, left_b = _side_lines(ry, lx, tol, y1 - y0, True)
    right_t, right_b = _side_lines(ry2, rx, tol, y1 - y0, False)
    H_ = lambda ab: np.array([ab[0], -1.0, ab[1]])   # y = a x + b
    V_ = lambda ab: np.array([-1.0, ab[0], ab[1]])   # x = a y + b
    quad = np.array([
        _intersect(H_(top_l), V_(left_t)),
        _intersect(H_(top_r), V_(right_t)),
        _intersect(H_(bot_r), V_(right_b)),
        _intersect(H_(bot_l), V_(left_b)),
    ], dtype=np.float32)
    quad /= s
    wA = np.linalg.norm(quad[1] - quad[0]); wB = np.linalg.norm(quad[2] - quad[3])
    hA = np.linalg.norm(quad[3] - quad[0]); hB = np.linalg.norm(quad[2] - quad[1])
    return GridQuad(corners=quad, width=int(round((wA + wB) / 2)), height=int(round((hA + hB) / 2)),
                    ink_thresh=ink_thresh, frame_gray=frame_gray, frame_rgb=frame_rgb, page_gray=page_gray)


def warp(img_rgb: np.ndarray, q: GridQuad, margin: int = 0, scale: float = 1.0) -> tuple[np.ndarray, np.ndarray]:
    """Rektyfikuje obraz do prostokąta siatki (z marginesem w pikselach docelowych).
    Zwraca (obraz, homografia obraz_wejściowy -> obraz_wynikowy)."""
    W = int(round(q.width * scale)); H = int(round(q.height * scale))
    dst = np.array([[margin, margin], [W - 1 + margin, margin], [W - 1 + margin, H - 1 + margin], [margin, H - 1 + margin]], dtype=np.float32)
    Hm = cv2.getPerspectiveTransform(q.corners, dst)
    out = cv2.warpPerspective(img_rgb, Hm, (W + 2 * margin, H + 2 * margin), flags=cv2.INTER_AREA, borderMode=cv2.BORDER_REPLICATE)
    return out, Hm
