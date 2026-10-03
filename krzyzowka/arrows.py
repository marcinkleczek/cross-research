"""Strzałki (wypełnione trójkąty) w komórkach literowych i ich kierunek."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import cv2

from .cells import LETTER

RIGHT, DOWN, LEFT, UP = "prawo", "dol", "lewo", "gora"
DIRS = {RIGHT: (0, 1), DOWN: (1, 0), LEFT: (0, -1), UP: (-1, 0)}


@dataclass
class Arrow:
    x: float          # środek ciężkości [px obrazu zrektyfikowanego]
    y: float
    direction: str
    row: int          # komórka, w której leży trójkąt
    col: int
    score: float
    bbox: tuple[int, int, int, int]
    stem: str | None = None   # strona, z której dochodzi "ogonek" strzałki: gora/dol/lewo/prawo (ramka źródłowa)


def detect_stems(arrows: list["Arrow"], thin: np.ndarray, cell: int) -> None:
    """Strzałki łamane: cienka kreska od krawędzi komórki do trójkąta wskazuje stronę ramki opisu.
    Szukamy jej w masce cienkich struktur (black-hat), z dala od linii siatki (margines 6 px),
    tylko gdy trójkąt nie przylega do krawędzi (odstęp >= 0.12 komórki)."""
    m = 6
    for a in arrows:
        x0, y0, x1, y1 = a.bbox
        r, c = a.row, a.col
        top, bot, left, right = r * cell, (r + 1) * cell, c * cell, (c + 1) * cell
        best = None
        for side, ya, yb in (("gora", top + m, y0 - 1), ("dol", y1 + 1, bot - m)):
            if yb - ya >= 0.12 * cell:
                strip = thin[ya:yb, max(0, x0 - 2):x1 + 3] > 0
                frac = strip.any(axis=1).mean()
                if frac >= 0.8 and (best is None or frac > best[1]):
                    best = (side, frac)
        for side, xa, xb in (("lewo", left + m, x0 - 1), ("prawo", x1 + 1, right - m)):
            if xb - xa >= 0.12 * cell:
                strip = thin[max(0, y0 - 2):y1 + 3, xa:xb] > 0
                frac = strip.any(axis=0).mean()
                if frac >= 0.8 and (best is None or frac > best[1]):
                    best = (side, frac)
        a.stem = best[0] if best else None


def _triangle_template(w: int, h: int, direction: str) -> np.ndarray:
    """Wypełniony trójkąt równoramienny wpisany w prostokąt w x h, wierzchołek w kierunku `direction`."""
    if direction == RIGHT:
        pts = [(0, 0), (w - 1, h // 2), (0, h - 1)]
    elif direction == LEFT:
        pts = [(w - 1, 0), (0, h // 2), (w - 1, h - 1)]
    elif direction == DOWN:
        pts = [(0, 0), (w - 1, 0), (w // 2, h - 1)]
    else:
        pts = [(0, h - 1), (w - 1, h - 1), (w // 2, 0)]
    t = np.zeros((h, w), np.uint8)
    cv2.fillPoly(t, [np.array(pts, np.int32)], 1)
    return t


def ink_without_lines(dark: np.ndarray, cell: int) -> np.ndarray:
    """Maska tuszu bez linii siatki i ramek (usunięte długie proste odcinki)."""
    L = max(8, int(0.35 * cell))
    mh = cv2.dilate(dark, cv2.getStructuringElement(cv2.MORPH_RECT, (1, 3)))
    mv = cv2.dilate(dark, cv2.getStructuringElement(cv2.MORPH_RECT, (3, 1)))
    hor = cv2.morphologyEx(mh, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (L, 1)))
    ver = cv2.morphologyEx(mv, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, L)))
    lines = cv2.dilate(cv2.bitwise_or(hor, ver), cv2.getStructuringElement(cv2.MORPH_RECT, (7, 7)))
    return cv2.bitwise_and(dark, cv2.bitwise_not(lines))


def find_arrows(types: np.ndarray, dark_nolines: np.ndarray, cell: int, min_iou: float = 0.72) -> list[Arrow]:
    rows, cols = types.shape
    n, lab, stats, cents = cv2.connectedComponentsWithStats(dark_nolines, connectivity=8)
    out = []
    amin, amax = 0.004 * cell * cell, 0.06 * cell * cell
    for i in range(1, n):
        x, y, w, h, a = (int(stats[i, c]) for c in (cv2.CC_STAT_LEFT, cv2.CC_STAT_TOP, cv2.CC_STAT_WIDTH, cv2.CC_STAT_HEIGHT, cv2.CC_STAT_AREA))
        if not (amin <= a <= amax) or w < 0.06 * cell or h < 0.06 * cell:
            continue
        if not (0.45 <= w / h <= 2.2):
            continue
        cx, cy = cents[i]
        r, c = int(cy // cell), int(cx // cell)
        if not (0 <= r < rows and 0 <= c < cols) or types[r, c] != LETTER:
            continue
        comp = (lab[y:y + h, x:x + w] == i).astype(np.uint8)
        if comp.mean() < 0.4:  # trójkąt wypełnia ~50 % prostokąta otaczającego
            continue
        best, bestd = 0.0, None
        for d in (RIGHT, DOWN, LEFT, UP):
            t = _triangle_template(w, h, d)
            inter = np.logical_and(comp, t).sum(); union = np.logical_or(comp, t).sum()
            iou = inter / union if union else 0
            if iou > best:
                best, bestd = iou, d
        if best >= min_iou:
            out.append(Arrow(x=float(cx), y=float(cy), direction=bestd, row=r, col=c, score=float(best), bbox=(x, y, x + w, y + h)))
    return out
