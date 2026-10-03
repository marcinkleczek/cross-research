"""Pasek rozwiązania pod siatką (ponumerowane pola, pogrubione separatory słów) i podpis nad nim."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import cv2

from .image import gray
from .grid import GridQuad, thin_dark, line_masks


@dataclass
class StripRow:
    y0: int
    y1: int
    x_lines: list[int]            # położenia linii pionowych [px obrazu rozszerzonego]
    thick: list[bool]             # czy dana linia jest pogrubiona (granica słowa)
    boxes: int


@dataclass
class Strip:
    rows: list[StripRow] = field(default_factory=list)
    total: int = 0
    word_lengths: list[int] = field(default_factory=list)
    bbox: tuple[int, int, int, int] | None = None   # w obrazie rozszerzonym
    caption_bbox: tuple[int, int, int, int] | None = None


def extended_warp(img: np.ndarray, q: GridQuad, scale: float, extra_frac: float = 0.4, side_frac: float = 0.08) -> tuple[np.ndarray, np.ndarray, int, int]:
    """Rektyfikacja homografią siatki wraz z pasem pod nią (extra_frac wysokości) i marginesem bocznym
    (side_frac szerokości; pasek rozwiązania bywa szerszy od siatki). `scale` = piksele wyjściowe na piksel
    szerokości siatki. Zwraca (obraz, homografia, wysokość siatki w obrazie, położenie lewej krawędzi siatki)."""
    tl, tr, br, bl = q.corners
    ex = side_frac * (tr - tl); ex2 = side_frac * (br - bl)
    tl2, tr2 = tl - ex, tr + ex
    bl2, br2 = bl - ex2, br + ex2
    ext_bl = bl2 + extra_frac * (bl2 - tl2)
    ext_br = br2 + extra_frac * (br2 - tr2)
    src = np.array([tl2, tr2, ext_br, ext_bl], dtype=np.float32)
    Wg = q.width * scale; Hg = q.height * scale
    W = int(round(Wg * (1 + 2 * side_frac))); H = int(round(Hg * (1 + extra_frac)))
    dst = np.array([[0, 0], [W - 1, 0], [W - 1, H - 1], [0, H - 1]], dtype=np.float32)
    Hm = cv2.getPerspectiveTransform(src, dst)
    out = cv2.warpPerspective(img, Hm, (W, H), flags=cv2.INTER_AREA, borderMode=cv2.BORDER_REPLICATE)
    return out, Hm, int(round(Hg)), int(round(side_frac * Wg))


def _runs(flags: np.ndarray, merge_gap: int) -> list[tuple[int, int]]:
    """Przedziały [a, b] kolejnych wartości True, łączone gdy przerwa < merge_gap."""
    idx = np.nonzero(flags)[0]
    if len(idx) == 0:
        return []
    out = [[int(idx[0]), int(idx[0])]]
    for i in idx[1:]:
        if i - out[-1][1] < merge_gap:
            out[-1][1] = int(i)
        else:
            out.append([int(i), int(i)])
    return [(a, b) for a, b in out]


def find_strip(ext: np.ndarray, grid_h: int, cell: int, ink_thresh: int = 40) -> tuple[Strip, list[str]]:
    """Pasek rozwiązania: pasma między kolejnymi długimi liniami poziomymi pod siatką o wysokości
    0.3-0.8 komórki; w pasmie pola wyznaczają linie pionowe (przerwy o szerokości zbliżonej do mediany),
    a kreska wyraźnie grubsza od pozostałych to granica słowa."""
    warnings = []
    strip = Strip()
    g = cv2.GaussianBlur(gray(ext), (3, 3), 0)
    # niższy próg black-hat: skrajna kreska paska graniczy z ciemnym tłem strony (słabszy kontrast)
    thin = thin_dark(g, max(2, cell // 40), thresh=max(18, int(0.75 * ink_thresh)))
    region = thin.copy(); region[:grid_h + int(0.5 * cell)] = 0
    hor, ver = line_masks(region, int(0.25 * cell))
    H, W = region.shape
    hcount = (hor > 0).sum(axis=1)
    hlines = _runs(hcount >= 1.5 * cell, int(0.08 * cell))
    ys = [int((a + b) / 2) for a, b in hlines]
    # kreski pionowe jako składowe spójne (odporne na krzywiznę paska)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(ver, connectivity=8)
    segs = [(stats[i, cv2.CC_STAT_LEFT] + stats[i, cv2.CC_STAT_WIDTH] / 2.0, stats[i, cv2.CC_STAT_TOP],
             stats[i, cv2.CC_STAT_TOP] + stats[i, cv2.CC_STAT_HEIGHT], stats[i, cv2.CC_STAT_WIDTH])
            for i in range(1, n) if stats[i, cv2.CC_STAT_WIDTH] <= 0.25 * cell]
    bands = [(ya, yb) for ya, yb in zip(ys[:-1], ys[1:]) if 0.3 * cell <= yb - ya <= 0.8 * cell]
    # dolna linia paska bywa niewidoczna w masce black-hat (ciemne tło strony pod nią):
    # pasmo domykamy wysokością kresek zaczynających się przy ostatniej linii poziomej
    if ys:
        y_last = ys[-1]
        starts = [sg for sg in segs if abs(sg[1] - y_last) < 0.1 * cell and 0.3 * cell <= sg[2] - sg[1] <= 0.8 * cell]
        if len(starts) >= 3 and not any(abs(b[0] - y_last) < 0.1 * cell for b in bands):
            bands.append((y_last, int(y_last + np.median([sg[2] - sg[1] for sg in starts]))))
    rows_found = []
    for ya, yb in bands:
        hit = [sg for sg in segs if min(sg[2], yb) - max(sg[1], ya) >= 0.5 * (yb - ya)]
        hit.sort(key=lambda t: t[0])
        xs = np.array([sg[0] for sg in hit])
        if len(xs) < 3:
            continue
        # grubość kreski: długość ciągu kolumn z pokryciem >= 0.5 w pasmie wokół x
        vprof = (ver[ya + 3:max(ya + 4, yb - 2), :] > 0).mean(axis=0)
        ws = []
        for x in xs:
            a = int(max(0, x - 0.12 * cell)); b = int(min(W, x + 0.12 * cell))
            runs = _runs(vprof[a:b] >= 0.5, 2)
            ws.append(max([r[1] - r[0] + 1 for r in runs], default=1))
        ws = np.array(ws, dtype=float)
        gaps = np.diff(xs)
        med = float(np.median(gaps))
        if not (0.3 * cell <= med <= 0.85 * cell):
            continue
        keep = [0]
        for i in range(1, len(xs)):
            if xs[i] - xs[keep[-1]] >= 0.3 * med:
                keep.append(i)
            else:
                ws[keep[-1]] = max(ws[keep[-1]], ws[i])
        xs = xs[keep]; ws = ws[keep]; gaps = np.diff(xs)
        ok = (gaps >= 0.6 * med) & (gaps <= 1.4 * med)
        boxes = int(ok.sum())
        if boxes < 2:
            continue
        wmed = float(np.median(ws))
        thick = [bool(w >= 1.7 * wmed) for w in ws]
        rows_found.append(StripRow(y0=ya, y1=yb, x_lines=[int(v) for v in xs], thick=thick, boxes=boxes))
    if not rows_found:
        warnings.append("pasek rozwiązania: nie znaleziono pól pod siatką")
        return strip, warnings
    rows_found = [r for r in rows_found if r.y0 - rows_found[0].y0 <= 3 * cell]
    strip.rows = rows_found
    strip.total = sum(r.boxes for r in rows_found)
    lengths = []
    run = 0
    for ri, r in enumerate(rows_found):
        gaps = np.diff(r.x_lines)
        med = float(np.median(gaps))
        for gi, gp in enumerate(gaps):
            if not (0.6 * med <= gp <= 1.4 * med):
                continue
            run += 1
            if gi + 1 < len(r.x_lines) - 1 and r.thick[gi + 1]:
                lengths.append(run); run = 0
        if ri == len(rows_found) - 1 or r.thick[-1]:
            if run:
                lengths.append(run); run = 0
    if run:
        lengths.append(run)
    strip.word_lengths = lengths
    x0 = min(min(r.x_lines) for r in rows_found); x1 = max(max(r.x_lines) for r in rows_found)
    strip.bbox = (x0, rows_found[0].y0, x1, rows_found[-1].y1)
    strip.caption_bbox = (0, grid_h, W, rows_found[0].y0)
    return strip, warnings
