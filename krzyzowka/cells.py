"""Krawędzie między komórkami, cechy komórek i ich klasyfikacja (litera / opis / rysunek)."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import cv2

from .image import gray

LETTER, CLUE, PICTURE, OUTSIDE = "L", "O", "R", "X"


@dataclass
class Edges:
    """Obecność linii siatki. Wartości: 0 brak, 1 linia prosta, 2 linia falista (separator słów)."""
    hor: np.ndarray    # (rows+1, cols)      linia nad komórką j (między j-1 a j), dla kolumny k
    ver: np.ndarray    # (rows, cols+1, 2)   linia na lewo od komórki k, osobno dla górnej/dolnej połowy
    split: np.ndarray  # (rows, cols)        pozioma linia w połowie wysokości komórki (podział opisu)


def _longest_run(cover: np.ndarray, gap: int = 2) -> int:
    """Najdłuższy ciągły odcinek wartości True (z tolerancją przerw <= gap)."""
    best = cur = 0; holes = 0
    for v in cover:
        if v:
            cur += 1 + holes; holes = 0
        elif holes < gap and cur > 0:
            holes += 1
        else:
            cur = 0; holes = 0
        best = max(best, cur)
    return best


def _straight(mask: np.ndarray, frac: float = 0.6) -> bool:
    """Czy w pasie istnieje wąski (3 px) podpas z ciągłym odcinkiem tuszu o długości >= frac długości pasa.
    Wymóg ciągłości odróżnia linię od gęstego tekstu (który ma przerwy między wierszami/literami)."""
    h, L = mask.shape
    for c in range(1, h - 1):
        cover = (mask[c - 1:c + 2] > 0).any(axis=0)
        if cover.mean() >= frac and _longest_run(cover, gap=4) >= 0.6 * L:
            return True
    return False


def _wavy(mask: np.ndarray) -> bool:
    """Linia falista: tusz na niemal całej długości, a jego środek ciężkości wyraźnie oscyluje w poprzek."""
    m = mask > 0
    cover = m.any(axis=0)
    if cover.mean() < 0.8:
        return False
    h = mask.shape[0]
    ys = np.arange(h)[:, None]
    cen = (m * ys).sum(axis=0)[cover] / m.sum(axis=0)[cover]
    dev = np.abs(cen - np.median(cen))
    return (dev > 0.2 * h).mean() > 0.3


def _thickness(mask: np.ndarray) -> float:
    """Mediana grubości tuszu w poprzek pasa (po kolumnach wzdłuż krawędzi z tuszem)."""
    cnt = (mask > 0).sum(axis=0)
    cnt = cnt[cnt > 0]
    return float(np.median(cnt)) if len(cnt) else 0.0


def _zigzag(mask: np.ndarray, min_frac: float = 0.35, min_flips: int = 3) -> bool:
    """Sygnatura zygzaka: środek ciężkości tuszu w poprzek pasa oscyluje (wiele zmian znaku reszty)
    wokół gładkiej linii trendu. Lekko wygięta linia siatki ma po usunięciu trendu reszty ~0,
    a pojedynczy trójkąt strzałki dotykający linii daje jedno wybrzuszenie (<= 2 zmiany znaku)."""
    m = mask > 0
    cover = m.any(axis=0)
    xs = np.nonzero(cover)[0]
    if len(xs) < 12:
        return False
    ys = np.arange(mask.shape[0])[:, None]
    cen = (m * ys).sum(axis=0)[cover] / m.sum(axis=0)[cover]
    res = cen - np.polyval(np.polyfit(xs, cen, 2), xs)
    if (np.abs(res) > 1.5).mean() < min_frac:
        return False
    sgn = np.sign(res[np.abs(res) > 0.75])
    flips = int((np.diff(sgn) != 0).sum()) if len(sgn) > 1 else 0
    return flips >= min_flips


def _edge_present(narrow: np.ndarray, wide: np.ndarray, frac: float = 0.6, thick_px: float = 8.0) -> int:
    """0 brak, 1 linia prosta, 2 separator falisty (zygzak)."""
    if _straight(narrow, frac):
        return 2 if _zigzag(narrow) else 1
    return 2 if _wavy(wide) else 0


def line_mask(rect: np.ndarray, rect_thin: np.ndarray, cell: int) -> np.ndarray:
    """Maska tuszu do testów krawędzi: suma maski black-hat i progu adaptacyjnego
    (ten drugi łapie linie przylegające do ciemnych obszarów rysunku)."""
    g = gray(rect)
    dark = cv2.adaptiveThreshold(g, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, (cell // 2) | 1, 30)
    return cv2.bitwise_or(rect_thin, dark)


def detect_edges(rect_thin: np.ndarray, rows: int, cols: int, cell: int) -> Edges:
    hw = max(3, int(0.06 * cell))     # pół szerokości pasa (prosta)
    hw_w = max(hw, int(0.14 * cell))  # pół szerokości pasa (falista)
    inset = int(0.1 * cell)
    thick_px = 0.065 * cell           # od tej grubości tusz w pasie uznajemy za zygzak/pogrubienie
    hor = np.zeros((rows + 1, cols), np.uint8)
    ver = np.zeros((rows, cols + 1, 2), np.uint8)
    split = np.zeros((rows, cols), np.uint8)
    H, W = rect_thin.shape

    def band_h(y, x0, x1, half):
        y0 = max(0, y - half); y1 = min(H, y + half + 1)
        return rect_thin[y0:y1, x0:x1]

    def band_v(x, y0, y1, half):
        x0 = max(0, x - half); x1 = min(W, x + half + 1)
        return rect_thin[y0:y1, x0:x1].T

    for j in range(rows + 1):
        y = j * cell
        for k in range(cols):
            x0, x1 = k * cell + inset, (k + 1) * cell - inset
            hor[j, k] = 1 if j in (0, rows) else _edge_present(band_h(y, x0, x1, hw), band_h(y, x0, x1, hw_w), thick_px=thick_px)
    for j in range(rows):
        for k in range(cols + 1):
            x = k * cell
            if k in (0, cols):
                ver[j, k, :] = 1
                continue
            for h in range(2):
                y0 = j * cell + h * cell // 2 + inset // 2
                y1 = j * cell + (h + 1) * cell // 2 - inset // 2
                ver[j, k, h] = _edge_present(band_v(x, y0, y1, hw), band_v(x, y0, y1, hw_w), thick_px=thick_px)
    for j in range(rows):
        y = j * cell + cell // 2
        for k in range(cols):
            x0, x1 = k * cell + inset, (k + 1) * cell - inset
            split[j, k] = 1 if _straight(band_h(y, x0, x1, hw), 0.7) else 0
    return Edges(hor=hor, ver=ver, split=split)


@dataclass
class CellFeatures:
    ink: np.ndarray      # udział tuszu we wnętrzu komórki
    bg: np.ndarray       # (rows, cols, 3) kolor tła: mediana RGB pikseli bez tuszu
    spread: np.ndarray   # rozrzut koloru tła (średnie odchylenie bezwzględne od mediany, suma po kanałach)
    dark: np.ndarray     # maska tuszu (próg adaptacyjny) dla całego obrazu


def ink_mask(rect: np.ndarray, cell: int) -> np.ndarray:
    g = gray(rect)
    return cv2.adaptiveThreshold(g, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, cell | 1, 25)


def cell_features(rect: np.ndarray, rows: int, cols: int, cell: int) -> CellFeatures:
    m = int(0.12 * cell)
    dark = ink_mask(rect, cell)
    ink = np.zeros((rows, cols)); bg = np.zeros((rows, cols, 3)); spread = np.zeros((rows, cols))
    for j in range(rows):
        for k in range(cols):
            y0, y1, x0, x1 = j * cell + m, (j + 1) * cell - m, k * cell + m, (k + 1) * cell - m
            d = dark[y0:y1, x0:x1] > 0
            ink[j, k] = d.mean()
            px = rect[y0:y1, x0:x1].reshape(-1, 3).astype(np.float32)
            sel = px[~d.ravel()] if (~d).sum() > 20 else px
            med = np.median(sel, axis=0)
            bg[j, k] = med
            spread[j, k] = np.abs(sel - med).sum(axis=1).mean()
    return CellFeatures(ink=ink, bg=bg, spread=spread, dark=dark)


def _chroma(bg: np.ndarray) -> np.ndarray:
    """Barwa niezależna od jasności: udziały kanałów RGB (odporne na cienie na stronie)."""
    return bg / (bg.sum(axis=-1, keepdims=True) + 1e-6)


def _best_rectangle(v: np.ndarray, min_rows: int, min_cols: int, min_cells: int):
    """Prostokąt (y, x, h, w) o największej sumie wartości `v` (wszystkie pary wierszy, Kadane po kolumnach)."""
    R, C = v.shape
    best = None; best_sum = 0.0
    for y0 in range(R):
        acc = np.zeros(C)
        for y1 in range(y0, R):
            acc += v[y1]
            h = y1 - y0 + 1
            if h < min_rows:
                continue
            # najlepszy przedział kolumn o długości >= min_cols
            pref = np.concatenate([[0.0], np.cumsum(acc)])
            for x1 in range(min_cols, C + 1):
                x0 = int(np.argmin(pref[:x1 - min_cols + 1]))
                ssum = pref[x1] - pref[x0]
                w = x1 - x0
                if ssum > best_sum and h * w >= min_cells:
                    best_sum = ssum; best = (y0, x0, h, w)
    return best


def straight_edges(E: Edges) -> np.ndarray:
    """Liczba prostych krawędzi siatki wokół każdej komórki (0..4)."""
    return ((E.hor[:-1, :] == 1).astype(int) + (E.hor[1:, :] == 1)
            + (E.ver[:, :-1, :] == 1).all(axis=2) + (E.ver[:, 1:, :] == 1).all(axis=2))


def _grow_rectangle(rect, score: np.ndarray, E: Edges):
    """Rozszerza prostokąt rysunku o sąsiednie wiersze/kolumny, w których brakuje linii siatki między
    komórkami (dymki z tekstem na brzegu rysunku) albo wynik rysunkowości jest wysoki."""
    R, C = score.shape
    y, x, h, w = rect
    nstraight = straight_edges(E)

    def strip_ok(cells_score, missing_frac, cells_straight):
        few_edges = (cells_straight <= 2).mean() >= 0.7
        return (missing_frac >= 0.5 and cells_score.mean() >= 0.7 and few_edges) or cells_score.mean() >= 1.1

    changed = True
    while changed:
        changed = False
        if y > 0:  # wiersz nad
            sc = score[y - 1, x:x + w]
            inner = E.ver[y - 1, x + 1:x + w, :]
            miss = 1.0 - (inner == 1).all(axis=1).mean() if inner.size else 1.0
            if strip_ok(sc, miss, nstraight[y - 1, x:x + w]):
                y -= 1; h += 1; changed = True
        if y + h < R:
            sc = score[y + h, x:x + w]
            inner = E.ver[y + h, x + 1:x + w, :]
            miss = 1.0 - (inner == 1).all(axis=1).mean() if inner.size else 1.0
            if strip_ok(sc, miss, nstraight[y + h, x:x + w]):
                h += 1; changed = True
        if x > 0:
            sc = score[y:y + h, x - 1]
            inner = E.hor[y + 1:y + h, x - 1]
            miss = 1.0 - (inner == 1).mean() if inner.size else 1.0
            if strip_ok(sc, miss, nstraight[y:y + h, x - 1]):
                x -= 1; w += 1; changed = True
        if x + w < C:
            sc = score[y:y + h, x + w]
            inner = E.hor[y + 1:y + h, x + w]
            miss = 1.0 - (inner == 1).mean() if inner.size else 1.0
            if strip_ok(sc, miss, nstraight[y:y + h, x + w]):
                w += 1; changed = True
    return (y, x, h, w)


def crossing_text(dark: np.ndarray, rows: int, cols: int, cell: int) -> np.ndarray:
    """(rows, cols+1): udział wierszy pikseli, w których tusz leży tuż po obu stronach pionowej linii siatki
    (tekst nadrukowany w poprzek kratek, np. pas z treścią zagadki nad rysunkiem)."""
    out = np.zeros((rows, cols + 1))
    inset = int(0.1 * cell); a, b = int(0.03 * cell) + 1, int(0.1 * cell)
    for j in range(rows):
        y0, y1 = j * cell + inset, (j + 1) * cell - inset
        for k in range(1, cols):
            x = k * cell
            left = (dark[y0:y1, x - b:x - a] > 0).any(axis=1)
            right = (dark[y0:y1, x + a:x + b] > 0).any(axis=1)
            out[j, k] = (left & right).mean()
    return out


def _grow_by_crossing_text(rect, crossing: np.ndarray, ink: np.ndarray, E: Edges, frac: float = 0.25):
    """Dołącza do prostokąta rysunku sąsiednie wiersze z tekstem nadrukowanym w poprzek istniejących
    linii siatki (pas z treścią zagadki). Wymaga linii i przecinającego ją tekstu na >= 60 % krawędzi."""
    R, C = ink.shape
    y, x, h, w = rect
    changed = True
    while changed:
        changed = False
        for side in ("top", "bottom"):
            jj = y - 1 if side == "top" else y + h
            if not (0 <= jj < R):
                continue
            inner = crossing[jj, x + 1:x + w]
            lines = (E.ver[jj, x + 1:x + w, :] == 1).any(axis=1)
            if inner.size and ((inner >= frac) & lines).mean() >= 0.6 and (ink[jj, x:x + w] > 0.1).mean() >= 0.7:
                if side == "top":
                    y -= 1
                h += 1; changed = True
    return (y, x, h, w)


def classify_cells(F: CellFeatures, E: Edges | None = None, E_thin: Edges | None = None, outside: np.ndarray | None = None,
                   crossing: np.ndarray | None = None,
                   spread_pic: float = 60.0, chroma_pic: float = 0.10, pic_threshold: float = 1.2) -> tuple[np.ndarray, dict]:
    """Typ każdej komórki: L (litera), O (opis), R (rysunek).

    Barwa tła liter = mediana barwy komórek prawie bez tuszu; barwa tła opisów = mediana barwy komórek
    z dużą ilością tuszu i jednolitym tłem. Rysunek: duży rozrzut koloru w komórce lub barwa niepodobna
    do obu wzorców; obszar rysunku musi być zwartym prostokątem (inaczej komórki wracają do L/O).
    """
    rows, cols = F.ink.shape
    if outside is None:
        outside = np.zeros((rows, cols), bool)
    E_thin = E_thin or E
    uniform = F.spread < spread_pic
    empty = uniform & (F.ink < 0.04) & ~outside
    texty = uniform & (F.ink > 0.15) & ~outside
    ch = _chroma(F.bg)
    letter_ch = np.median(ch[empty], axis=0) if empty.sum() >= 3 else np.array([1 / 3, 1 / 3, 1 / 3])
    clue_ch = np.median(ch[texty], axis=0) if texty.sum() >= 3 else letter_ch
    dL = np.abs(ch - letter_ch).sum(axis=2)
    dC = np.abs(ch - clue_ch).sum(axis=2)
    by_color = np.where(dC < dL, CLUE, LETTER).astype(object)
    types = np.where(F.ink > 0.15, CLUE, np.where(F.ink < 0.04, LETTER, by_color)).astype(object)
    # miękki wynik "rysunkowości" komórki i prostokąt o maksymalnej sumie (wynik - próg)
    score = F.spread / spread_pic + np.minimum(dL, dC) / chroma_pic
    if E_thin is not None:
        score = score + (4 - straight_edges(E_thin)) / 4.0
    score[outside] = 0.0
    keep = _best_rectangle(score - pic_threshold, min_rows=3, min_cols=3, min_cells=9)
    if keep:
        y, x, h, w = keep
        if score[y:y + h, x:x + w].mean() < pic_threshold + 0.4:
            keep = None  # słaby rdzeń (np. cień na stronie), nie rysunek
    if keep and E_thin is not None:
        keep = _grow_rectangle(keep, score, E_thin)
    if keep and crossing is not None:
        keep = _grow_by_crossing_text(keep, crossing, F.ink, E_thin)
    types = np.array(types, dtype=object)
    if keep:
        y, x, h, w = keep
        types[y:y + h, x:x + w] = PICTURE
    types[outside] = OUTSIDE
    info = {"barwa_tla_liter": letter_ch.round(3).tolist(), "barwa_tla_opisow": clue_ch.round(3).tolist(),
            "rysunek": None if not keep else {"wiersz": int(keep[0]), "kolumna": int(keep[1]), "wysokosc": int(keep[2]), "szerokosc": int(keep[3])},
            "wynik_rysunku": score}
    return types, info
