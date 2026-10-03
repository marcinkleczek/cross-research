"""Ramki opisów: obszar komórek typu O podzielony prostymi odcinkami (ściany) na pojedyncze ramki.

Ramki w tych krzyżówkach nie trzymają się siatki liter (kolumna opisów bywa dzielona na N równych
ramek niezależnie od wierszy), dlatego ramki wyznaczamy jako składowe spójne obszaru opisów
po odjęciu wszystkich długich prostych odcinków tuszu oraz granic z komórkami innego typu.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import cv2

from .cells import CLUE


@dataclass
class ClueBox:
    id: int
    x0: int
    y0: int
    x1: int
    y1: int
    cells: list[tuple[int, int]] = field(default_factory=list)   # komórki (wiersz, kolumna) pokryte ramką
    words: list[int] = field(default_factory=list)                # id haseł przypisanych tej ramce

    @property
    def cx(self):
        return 0.5 * (self.x0 + self.x1)

    @property
    def cy(self):
        return 0.5 * (self.y0 + self.y1)

    def dist(self, px: float, py: float) -> float:
        """Odległość punktu od prostokąta ramki (0 wewnątrz)."""
        dx = max(self.x0 - px, 0, px - self.x1)
        dy = max(self.y0 - py, 0, py - self.y1)
        return float(np.hypot(dx, dy))


def wall_mask(line_mask: np.ndarray, cell: int, min_len_frac: float = 0.45) -> np.ndarray:
    """Długie proste odcinki tuszu (poziome i pionowe) - granice ramek i linie siatki."""
    L = max(8, int(min_len_frac * cell))
    mh = cv2.dilate(line_mask, cv2.getStructuringElement(cv2.MORPH_RECT, (1, 3)))
    mv = cv2.dilate(line_mask, cv2.getStructuringElement(cv2.MORPH_RECT, (3, 1)))
    hor = cv2.morphologyEx(mh, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (L, 1)))
    ver = cv2.morphologyEx(mv, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, L)))
    walls = cv2.bitwise_or(hor, ver)
    return cv2.dilate(walls, cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5)))


def find_clue_boxes(types: np.ndarray, line_mask: np.ndarray, cell: int) -> list[ClueBox]:
    rows, cols = types.shape
    H, W = rows * cell, cols * cell
    area = np.zeros((H, W), np.uint8)
    for j in range(rows):
        for k in range(cols):
            if types[j, k] == CLUE:
                area[j * cell:(j + 1) * cell, k * cell:(k + 1) * cell] = 255
    walls = wall_mask(line_mask, cell)
    # granice między komórką opisu a komórką innego typu oraz obrys siatki
    t = 3
    for j in range(rows):
        for k in range(cols):
            if types[j, k] != CLUE:
                continue
            y0, y1, x0, x1 = j * cell, (j + 1) * cell, k * cell, (k + 1) * cell
            if j == 0 or types[j - 1, k] != CLUE:
                walls[max(0, y0 - t):y0 + t, x0:x1] = 255
            if j == rows - 1 or types[j + 1, k] != CLUE:
                walls[y1 - t:min(H, y1 + t), x0:x1] = 255
            if k == 0 or types[j, k - 1] != CLUE:
                walls[y0:y1, max(0, x0 - t):x0 + t] = 255
            if k == cols - 1 or types[j, k + 1] != CLUE:
                walls[y0:y1, x1 - t:min(W, x1 + t)] = 255
    free = cv2.bitwise_and(area, cv2.bitwise_not(walls))
    n, lab, stats, _ = cv2.connectedComponentsWithStats(free, connectivity=4)
    boxes = []
    min_area = 0.12 * cell * cell
    for i in range(1, n):
        x, y, w, h, a = (int(stats[i, c]) for c in (cv2.CC_STAT_LEFT, cv2.CC_STAT_TOP, cv2.CC_STAT_WIDTH, cv2.CC_STAT_HEIGHT, cv2.CC_STAT_AREA))
        if a < min_area or w < 0.3 * cell or h < 0.18 * cell:
            continue
        cells = []
        for j in range(max(0, y // cell), min(rows, (y + h - 1) // cell + 1)):
            for k in range(max(0, x // cell), min(cols, (x + w - 1) // cell + 1)):
                ix = max(0, min(x + w, (k + 1) * cell) - max(x, k * cell))
                iy = max(0, min(y + h, (j + 1) * cell) - max(y, j * cell))
                if ix * iy >= 0.1 * cell * cell:
                    cells.append((j, k))
        boxes.append(ClueBox(id=0, x0=x, y0=y, x1=x + w, y1=y + h, cells=cells))
    # numeracja: od góry, wierszami (z tolerancją), potem od lewej
    boxes.sort(key=lambda b: (round(b.cy / (cell * 0.5)), b.cx))
    for i, b in enumerate(boxes, 1):
        b.id = i
    return boxes
