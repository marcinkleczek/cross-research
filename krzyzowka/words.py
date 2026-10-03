"""Hasła: przypisanie strzałek do ramek opisów i przebieg liter od kratki startowej."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .cells import LETTER, Edges
from .arrows import Arrow, DIRS, RIGHT, DOWN, LEFT, UP
from .clues import ClueBox


@dataclass
class Word:
    id: int
    clue_id: int | None
    start: tuple[int, int]
    direction: str
    cells: list[tuple[int, int]]
    arrow: Arrow
    note: str = ""


def _blocked(E: Edges, r: int, c: int, direction: str) -> bool:
    """Czy między (r,c) a następną komórką w kierunku jest separator słów (linia falista lub pogrubiona)."""
    if direction == RIGHT:
        return E.ver[r, c + 1, :].max() == 2
    if direction == LEFT:
        return E.ver[r, c, :].max() == 2
    if direction == DOWN:
        return E.hor[r + 1, c] == 2
    return E.hor[r, c] == 2


def trace_word(types: np.ndarray, E: Edges, start: tuple[int, int], direction: str) -> list[tuple[int, int]]:
    rows, cols = types.shape
    dr, dc = DIRS[direction]
    r, c = start
    cells = []
    while 0 <= r < rows and 0 <= c < cols and types[r, c] == LETTER:
        cells.append((r, c))
        if _blocked(E, r, c, direction):
            break
        r, c = r + dr, c + dc
    return cells


def _side_of(a: Arrow, b: ClueBox, cell: int) -> str | None:
    """Po której stronie komórki strzałki leży ramka (gora/dol/lewo/prawo), jeśli przylega do tej komórki."""
    top, bot, left, right = a.row * cell, (a.row + 1) * cell, a.col * cell, (a.col + 1) * cell
    tol = 0.12 * cell
    if b.y1 <= top + tol and b.x1 > left - tol and b.x0 < right + tol:
        return "gora"
    if b.y0 >= bot - tol and b.x1 > left - tol and b.x0 < right + tol:
        return "dol"
    if b.x1 <= left + tol and b.y1 > top - tol and b.y0 < bot + tol:
        return "lewo"
    if b.x0 >= right - tol and b.y1 > top - tol and b.y0 < bot + tol:
        return "prawo"
    return None


def assign_arrows(arrows: list[Arrow], boxes: list[ClueBox], cell: int, max_dist: float = 1.1) -> tuple[list[tuple[Arrow, ClueBox | None]], list[str]]:
    """Każda strzałka dostaje najbliższą ramkę opisu. Jedna ramka może mieć kilka strzałek (ten sam
    wyraz wpisany poziomo i pionowo - konwencja tych krzyżówek), więc nie wymuszamy unikatowości.
    Koszt: odległość środka trójkąta od prostokąta ramki [komórki]; ogonek strzałki wskazujący stronę
    ramki obniża koszt, ramka leżąca "przed" grotem (w kierunku hasła) podwyższa."""
    warnings = []
    out = []
    for a in arrows:
        best = None
        dr, dc = DIRS[a.direction]
        for b in boxes:
            d = b.dist(a.x, a.y) / cell
            if d > max_dist:
                continue
            side = _side_of(a, b, cell)
            cost = d
            if a.stem and side == a.stem:
                cost -= 0.6
            ahead = (b.cx - a.x) * dc + (b.cy - a.y) * dr
            if ahead > 0.3 * cell:
                cost += 0.4
            # typowy skład: opis na lewo od strzałki w dół, opis nad strzałką w prawo
            if (a.direction == DOWN and side == "lewo") or (a.direction == RIGHT and side == "gora"):
                cost -= 0.2
            if (a.direction == UP and side == "lewo") or (a.direction == LEFT and side == "gora"):
                cost -= 0.2
            if best is None or cost < best[0]:
                best = (cost, b)
        if best is None:
            warnings.append(f"strzałka w kratce ({a.row},{a.col}) bez ramki opisu w zasięgu")
            out.append((a, None))
        else:
            out.append((a, best[1]))
    counts = {}
    for a, b in out:
        if b:
            counts[b.id] = counts.get(b.id, 0) + 1
    for b in boxes:
        n = counts.get(b.id, 0)
        if n == 0:
            warnings.append(f"ramka opisu {b.id} bez strzałki")
        elif n > 2:
            warnings.append(f"ramka opisu {b.id} ma {n} strzałek")
    return out, warnings


def build_words(types: np.ndarray, E: Edges, arrows: list[Arrow], boxes: list[ClueBox], cell: int) -> tuple[list[Word], list[str]]:
    pairs, warnings = assign_arrows(arrows, boxes, cell)
    words = []
    for a, b in pairs:
        cells = trace_word(types, E, (a.row, a.col), a.direction)
        note = ""
        if len(cells) < 2:
            note = "hasło krótsze niż 2 litery"
            warnings.append(f"strzałka w kratce ({a.row},{a.col}) kierunek {a.direction}: {note}")
        w = Word(id=len(words) + 1, clue_id=b.id if b else None, start=(a.row, a.col), direction=a.direction, cells=cells, arrow=a, note=note)
        words.append(w)
        if b:
            b.words.append(w.id)
    return words, warnings
