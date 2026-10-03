"""Numery w kratkach literowych: wyszukanie plam cyfr i rozpoznanie przez dopasowanie szablonów
renderowanych z czcionek bezszeryfowych pogrubionych (bez modeli uczonych)."""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import numpy as np
import cv2
from PIL import Image, ImageDraw, ImageFont

from .cells import LETTER

FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
]
TW, TH = 20, 28


def _normalize(mask: np.ndarray) -> np.ndarray:
    """Przycięcie do obrysu, przeskalowanie do TW x TH, wartości 0..1 (float32)."""
    ys, xs = np.nonzero(mask)
    if len(xs) == 0:
        return np.zeros((TH, TW), np.float32)
    m = mask[ys.min():ys.max() + 1, xs.min():xs.max() + 1].astype(np.float32)
    return cv2.resize(m, (TW, TH), interpolation=cv2.INTER_AREA)


@lru_cache(maxsize=1)
def templates() -> dict[int, list[tuple[np.ndarray, float]]]:
    """Dla każdej cyfry lista (szablon znormalizowany, proporcje szer/wys) z dostępnych czcionek."""
    out = {d: [] for d in range(10)}
    for path in FONT_CANDIDATES:
        try:
            font = ImageFont.truetype(path, 96)
        except OSError:
            continue
        for d in range(10):
            im = Image.new("L", (140, 140), 0)
            ImageDraw.Draw(im).text((20, 10), str(d), fill=255, font=font)
            a = (np.asarray(im) > 128).astype(np.uint8)
            ys, xs = np.nonzero(a)
            aspect = (xs.max() - xs.min() + 1) / (ys.max() - ys.min() + 1)
            out[d].append((_normalize(a), aspect))
    if not any(out.values()):
        raise RuntimeError("brak czcionek do szablonów cyfr")
    return out


def digit_scores(mask: np.ndarray) -> np.ndarray:
    """Wektor 10 wyników podobieństwa (0..1) plamy do cyfr 0..9 (korelacja znormalizowana + kara za proporcje)."""
    ys, xs = np.nonzero(mask)
    aspect = (xs.max() - xs.min() + 1) / (ys.max() - ys.min() + 1)
    q = _normalize(mask)
    q = (q - q.mean()) / (q.std() + 1e-6)
    scores = np.zeros(10)
    for d, lst in templates().items():
        best = 0.0
        for t, ta in lst:
            tn = (t - t.mean()) / (t.std() + 1e-6)
            corr = float((q * tn).mean())  # -1..1
            pen = min(1.0, abs(np.log(aspect / ta)) / 0.7)
            best = max(best, (corr + 1) / 2 * (1 - 0.5 * pen))
        scores[d] = best
    return scores


@dataclass
class NumberCandidate:
    row: int
    col: int
    bbox: tuple[int, int, int, int]
    glyphs: list[np.ndarray]          # maski kolejnych cyfr (od lewej)
    scores: list[np.ndarray]          # wyniki 0..9 dla kolejnych cyfr

    def value_score(self, value: int) -> float:
        s = str(value)
        if len(s) != len(self.glyphs):
            return -1.0
        return float(np.mean([self.scores[i][int(ch)] for i, ch in enumerate(s)]))

    def best_value(self) -> tuple[int, float]:
        digits = [int(np.argmax(sc)) for sc in self.scores]
        v = int("".join(map(str, digits)))
        return v, self.value_score(v)


def find_numbers(types: np.ndarray, dark_nolines: np.ndarray, cell: int, arrows_bboxes: list[tuple[int, int, int, int]]) -> list[NumberCandidate]:
    rows, cols = types.shape
    n, lab, stats, cents = cv2.connectedComponentsWithStats(dark_nolines, connectivity=8)
    comps = []
    hmin, hmax = 0.09 * cell, 0.35 * cell
    for i in range(1, n):
        x, y, w, h, a = (int(stats[i, c]) for c in (cv2.CC_STAT_LEFT, cv2.CC_STAT_TOP, cv2.CC_STAT_WIDTH, cv2.CC_STAT_HEIGHT, cv2.CC_STAT_AREA))
        if not (hmin <= h <= hmax) or w > 0.3 * cell or a < 0.002 * cell * cell:
            continue
        cx, cy = cents[i]
        r, c = int(cy // cell), int(cx // cell)
        if not (0 <= r < rows and 0 <= c < cols) or types[r, c] != LETTER:
            continue
        if any(x < bx1 and x + w > bx0 and y < by1 and y + h > by0 for bx0, by0, bx1, by1 in arrows_bboxes):
            continue
        comps.append((r, c, x, y, w, h, i))
    # grupowanie cyfr w numery (ta sama komórka, podobna wysokość, blisko w poziomie)
    out = []
    by_cell: dict[tuple[int, int], list] = {}
    for comp in comps:
        by_cell.setdefault((comp[0], comp[1]), []).append(comp)
    for (r, c), lst in by_cell.items():
        lst.sort(key=lambda t: t[2])
        groups = []
        for comp in lst:
            if groups:
                prev = groups[-1][-1]
                gap = comp[2] - (prev[2] + prev[4])
                if gap < 0.5 * max(comp[5], prev[5]) and abs(comp[3] - prev[3]) < 0.4 * max(comp[5], prev[5]):
                    groups[-1].append(comp); continue
            groups.append([comp])
        # numer stoi w prawej dolnej ćwiartce komórki; bierzemy grupę najbliższą prawego dolnego rogu
        best = None
        for g in groups:
            if len(g) > 2:
                continue
            x0 = min(t[2] for t in g); y0 = min(t[3] for t in g)
            x1 = max(t[2] + t[4] for t in g); y1 = max(t[3] + t[5] for t in g)
            cx, cy = (x0 + x1) / 2 - c * cell, (y0 + y1) / 2 - r * cell
            if cx < 0.35 * cell or cy < 0.35 * cell:
                continue
            d = np.hypot(cell - cx, cell - cy)
            if best is None or d < best[0]:
                best = (d, g, (x0, y0, x1, y1))
        if best is None:
            continue
        _, g, bbox = best
        glyphs = [(lab[t[3]:t[3] + t[5], t[2]:t[2] + t[4]] == t[6]).astype(np.uint8) for t in g]
        out.append(NumberCandidate(row=r, col=c, bbox=bbox, glyphs=glyphs, scores=[digit_scores(m) for m in glyphs]))
    return out


def assign_numbers(cands: list[NumberCandidate], n_expected: int | None) -> tuple[dict[int, NumberCandidate], list[str]]:
    """Przypisuje kandydatom unikatowe numery 1..N (algorytm węgierski na wynikach dopasowania)."""
    from scipy.optimize import linear_sum_assignment
    warnings = []
    if not cands:
        return {}, warnings
    if n_expected:
        N = n_expected  # liczba pól paska jest wiarygodna; nadmiarowe plamy zostaną nieprzypisane
    else:
        N = max(len(cands), max(c.best_value()[0] for c in cands))
    cost = np.full((len(cands), N), 1.0)
    for i, c in enumerate(cands):
        for v in range(1, N + 1):
            s = c.value_score(v)
            if s >= 0:
                cost[i, v - 1] = 1.0 - s
    ri, ci = linear_sum_assignment(cost)
    result = {}
    assigned = set()
    for i, j in zip(ri, ci):
        v = j + 1
        if cost[i, j] >= 0.999:
            warnings.append(f"plama w kratce ({cands[i].row},{cands[i].col}) nie pasuje do żadnej wolnej wartości 1..{N}")
            continue
        assigned.add(i)
        result[v] = cands[i]
        bv, bs = cands[i].best_value()
        if bv != v:
            warnings.append(f"kratka ({cands[i].row},{cands[i].col}): najlepszy odczyt {bv} ({bs:.2f}), przypisano {v} ({1 - cost[i, j]:.2f}) dla unikatowości")
    extra = [c for i, c in enumerate(cands) if i not in assigned and i not in set(ri)]
    if extra:
        warnings.append("nieprzypisane plamy (prawdopodobnie nie numery): " + ", ".join(f"({c.row},{c.col})" for c in extra))
    missing = [v for v in range(1, N + 1) if v not in result]
    if missing:
        warnings.append(f"brak numerów: {missing}")
    return result, warnings
