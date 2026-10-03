"""Nakładki kontrolne na obrazie zrektyfikowanym."""
from __future__ import annotations

import numpy as np
import cv2

from .cells import LETTER, CLUE, PICTURE, OUTSIDE


def draw_edges(vis: np.ndarray, edges, rows: int, cols: int, cell: int):
    col = {1: (0, 180, 0), 2: (0, 140, 255)}
    for j in range(rows + 1):
        for k in range(cols):
            v = edges.hor[j, k]
            if v:
                cv2.line(vis, (k * cell + 4, j * cell), ((k + 1) * cell - 4, j * cell), col[v], 3)
    for j in range(rows):
        for k in range(cols + 1):
            for h in range(2):
                v = edges.ver[j, k, h]
                if v:
                    y0 = j * cell + h * cell // 2 + 4; y1 = j * cell + (h + 1) * cell // 2 - 4
                    cv2.line(vis, (k * cell, y0), (k * cell, y1), col[v], 3)
    for j in range(rows):
        for k in range(cols):
            if edges.split[j, k]:
                y = j * cell + cell // 2
                cv2.line(vis, (k * cell + 10, y), ((k + 1) * cell - 10, y), (255, 0, 0), 2)


def draw_types(vis: np.ndarray, types: np.ndarray, cell: int):
    colors = {LETTER: None, CLUE: (255, 0, 255), PICTURE: (0, 255, 255), OUTSIDE: (0, 0, 0)}
    rows, cols = types.shape
    for j in range(rows):
        for k in range(cols):
            c = colors.get(types[j, k])
            if c:
                cv2.rectangle(vis, (k * cell + 6, j * cell + 6), ((k + 1) * cell - 6, (j + 1) * cell - 6), c, 2)


def render_overlay(lat, types, edges, boxes, arrows, words, numbers) -> np.ndarray:
    """Nakładka kontrolna (RGB): typy komórek, krawędzie faliste, ramki opisów z numerami,
    strzałki z kierunkiem, przebieg haseł, rozpoznane numery kratek."""
    cell = lat.cell
    vis = lat.rect.copy()
    rows, cols = types.shape
    tint = vis.copy()
    for j in range(rows):
        for k in range(cols):
            c = {CLUE: (200, 120, 255), PICTURE: (255, 230, 90), OUTSIDE: (40, 40, 40)}.get(types[j, k])
            if c:
                cv2.rectangle(tint, (k * cell, j * cell), ((k + 1) * cell, (j + 1) * cell), c, -1)
    vis = cv2.addWeighted(vis, 0.75, tint, 0.25, 0)
    # linie siatki i separatory faliste
    for j in range(rows + 1):
        for k in range(cols):
            if edges.hor[j, k] == 2:
                cv2.line(vis, (k * cell, j * cell), ((k + 1) * cell, j * cell), (255, 120, 0), 5)
    for j in range(rows):
        for k in range(cols + 1):
            if edges.ver[j, k, :].max() == 2:
                cv2.line(vis, (k * cell, j * cell), (k * cell, (j + 1) * cell), (255, 120, 0), 5)
    for b in boxes:
        cv2.rectangle(vis, (b.x0, b.y0), (b.x1, b.y1), (200, 0, 200), 2)
        cv2.putText(vis, f"O{b.id}", (b.x0 + 3, b.y0 + 16), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (160, 0, 160), 2)
    colors = [(220, 0, 0), (0, 140, 0), (0, 0, 220), (200, 120, 0), (0, 150, 150), (150, 0, 150)]
    for w in words:
        col = colors[w.id % len(colors)]
        pts = [(k * cell + cell // 2, j * cell + cell // 2) for j, k in w.cells]
        if len(pts) >= 2:
            cv2.polylines(vis, [np.array(pts, np.int32)], False, col, 2)
        if pts:
            cv2.circle(vis, pts[0], 7, col, -1)
            cv2.putText(vis, f"H{w.id}" + (f"/O{w.clue_id}" if w.clue_id else "/?"), (pts[0][0] - 20, pts[0][1] - 10),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.42, col, 1)
    for a in arrows:
        x0, y0, x1, y1 = a.bbox
        cv2.rectangle(vis, (x0 - 2, y0 - 2), (x1 + 2, y1 + 2), (0, 200, 0), 2)
    for v, c in numbers.items():
        x0, y0, x1, y1 = c.bbox
        cv2.rectangle(vis, (x0 - 2, y0 - 2), (x1 + 2, y1 + 2), (0, 90, 255), 1)
        cv2.putText(vis, str(v), (x0 - 2, y0 - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 90, 255), 1)
    return vis
