"""Kratownica: liczba wierszy/kolumn, przebieg (wygiętych) linii siatki, przepróbkowanie
do idealnie regularnej siatki o stałym boku komórki.

Wejście: obraz po homografii (narożniki siatki w narożnikach obrazu). Linie są tam prawie proste,
ale zagięcie strony daje odchyłki rzędu ułamka komórki, które usuwamy odwzorowaniem po węzłach.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import cv2

from .image import gray
from .grid import thin_dark, line_masks


@dataclass
class Lattice:
    rows: int
    cols: int
    cell: int                 # bok komórki w obrazie zrektyfikowanym [px]
    nodes_warp: np.ndarray    # (rows+1, cols+1, 2) węzły w obrazie po homografii
    nodes_src: np.ndarray     # (rows+1, cols+1, 2) węzły w obrazie wejściowym
    rect: np.ndarray          # obraz zrektyfikowany RGB (rows*cell x cols*cell)
    rect_thin: np.ndarray     # maska cienkich ciemnych struktur w obrazie zrektyfikowanym
    outside: np.ndarray = None  # (rows, cols) True, gdy komórka leży (częściowo) poza kadrem zdjęcia
    map_x: np.ndarray = None    # mapy przepróbkowania: piksel obrazu zrektyfikowanego -> współrzędne zdjęcia
    map_y: np.ndarray = None

    @property
    def width(self):
        return self.cols * self.cell

    @property
    def height(self):
        return self.rows * self.cell


def _autocorr_period(profile: np.ndarray, min_lag: int, max_lag: int) -> float:
    """Okres sygnału z autokorelacji: pierwsze wyraźne maksimum w [min_lag, max_lag], doprecyzowane parabolą."""
    p = profile.astype(np.float64)
    p = p - p.mean()
    n = len(p)
    f = np.fft.rfft(p, 2 * n)
    ac = np.fft.irfft(f * np.conj(f))[:n]
    ac /= ac[0] + 1e-9
    max_lag = min(max_lag, n - 2)
    seg = ac[min_lag:max_lag]
    # kandydaci: maksima lokalne; bierzemy najwyższe, ale karzemy wielokrotności (dzielimy przez pierwiastek z lag)
    best, best_score = None, -1
    for i in range(1, len(seg) - 1):
        if seg[i] >= seg[i - 1] and seg[i] >= seg[i + 1] and seg[i] > 0:
            lag = i + min_lag
            score = seg[i] / np.sqrt(lag / min_lag)
            if score > best_score:
                best, best_score = lag, score
    if best is None:
        raise RuntimeError("nie udało się wyznaczyć okresu siatki")
    y0, y1, y2 = ac[best - 1], ac[best], ac[best + 1]
    denom = (y0 - 2 * y1 + y2)
    delta = 0.5 * (y0 - y2) / denom if abs(denom) > 1e-12 else 0.0
    return best + float(np.clip(delta, -0.5, 0.5))


def estimate_counts(hor: np.ndarray, ver: np.ndarray, min_cell: int, max_cell: int) -> tuple[int, int]:
    """Liczba wierszy i kolumn z okresowości masek linii."""
    H, W = hor.shape
    px = _autocorr_period(ver.sum(axis=0), min_cell, max_cell)
    py = _autocorr_period(hor.sum(axis=1), min_cell, max_cell)
    cols = max(1, int(round(W / px)))
    rows = max(1, int(round(H / py)))
    return rows, cols


def _line_samples(mask: np.ndarray, nominal: float, pitch: float, win: int):
    """Próbki położenia jednej linii siatki (poziomej; dla pionowej maska transponowana).
    Zwraca tablice (x, y_wykryte)."""
    H, W = mask.shape
    half = 0.35 * pitch
    lo = int(max(0, np.floor(nominal - half))); hi = int(min(H, np.ceil(nominal + half) + 1))
    band = mask[lo:hi].astype(np.float32) / 255.0
    xs, ys = [], []
    for x0 in range(0, W, win):
        x1 = min(W, x0 + win)
        prof = band[:, x0:x1].sum(axis=1)
        if prof.max() >= 0.5 * (x1 - x0):
            i = int(prof.argmax())
            a, b = max(0, i - 2), min(len(prof), i + 3)
            w = prof[a:b]
            ys.append(lo + (np.arange(a, b) * w).sum() / w.sum())
            xs.append(0.5 * (x0 + x1))
    return np.array(xs), np.array(ys)


def _poly2_basis(x: np.ndarray, y: np.ndarray, deg: int) -> np.ndarray:
    cols = [x ** i * y ** j for i in range(deg + 1) for j in range(deg + 1 - i)]
    return np.stack(cols, axis=1)


class PolyField:
    """Gładkie pole f(x, y): wielomian 2D stopnia `deg`. Globalny RANSAC (hipoteza kwadratowa
    z 6 próbek), potem dopasowanie do zgodnych próbek z malejącą tolerancją do max(tol_px, 3*MAD)."""

    def __init__(self, deg: int = 3):
        self.deg = deg
        self.coef = None
        self.scale = 1.0

    def fit(self, x, y, v, tol_px: float = 2.0, ransac_tol: float = 12.0, iters: int = 1500, rng=None):
        x = np.asarray(x, float); y = np.asarray(y, float); v = np.asarray(v, float)
        n = len(x)
        self.scale = max(x.max(), y.max(), 1.0)
        nb = (self.deg + 1) * (self.deg + 2) // 2
        if n < 2 * nb:
            self.coef = np.zeros(nb)
            return 0
        rng = rng or np.random.default_rng(2)
        B2 = _poly2_basis(x / self.scale, y / self.scale, 2)
        best = np.abs(v - np.median(v)) < ransac_tol
        for _ in range(iters):
            idx = rng.choice(n, 6, replace=False)
            try:
                c = np.linalg.solve(B2[idx], v[idx])
            except np.linalg.LinAlgError:
                continue
            inl = np.abs(B2 @ c - v) < ransac_tol
            if inl.sum() > best.sum():
                best = inl
        keep = best
        tol = ransac_tol
        B = _poly2_basis(x / self.scale, y / self.scale, self.deg)
        for it in range(10):
            if keep.sum() < 2 * nb:
                break
            coef = np.linalg.lstsq(B[keep], v[keep], rcond=None)[0]
            res = np.abs(B @ coef - v)
            mad = np.median(res[keep]) + 1e-6
            new_keep = res < max(tol, tol_px, 3.0 * mad)
            self.coef = coef
            tol *= 0.6
            if np.array_equal(new_keep, keep) and tol < tol_px:
                break
            keep = new_keep
        if self.coef is None:
            self.coef = np.zeros(nb)
        return int(keep.sum())

    def __call__(self, x, y):
        x = np.atleast_1d(np.asarray(x, float)); y = np.atleast_1d(np.asarray(y, float))
        return _poly2_basis(x / self.scale, y / self.scale, self.deg) @ self.coef


def _hat_basis(t: np.ndarray, knots: np.ndarray) -> np.ndarray:
    """Funkcje kapeluszowe (kawałkami liniowe) na węzłach `knots` dla punktów `t`."""
    t = np.clip(t, knots[0], knots[-1])
    B = np.zeros((len(t), len(knots)))
    idx = np.clip(np.searchsorted(knots, t, side="right") - 1, 0, len(knots) - 2)
    w = (t - knots[idx]) / (knots[idx + 1] - knots[idx])
    B[np.arange(len(t)), idx] = 1 - w
    B[np.arange(len(t)), idx + 1] = w
    return B


class SeparableField:
    """Pole przesunięć linii siatki: v(along, across) = a(along) + across_n * b(along),
    gdzie a i b są kawałkami liniowe (węzły co komórkę). Model oddaje zagięcie strony (ostre
    załamanie wzdłuż grzbietu), a człon liniowy w poprzek różnice skali/pochylenia.

    Dopasowanie odporne: start od median po oknach (po wszystkich liniach), odrzucenie próbek
    odstających na każdej linii osobno (RANSAC prostej), potem regularyzowane LS.
    """

    def __init__(self, knots: np.ndarray, across_len: float):
        self.knots = np.asarray(knots, float)
        self.across_len = float(across_len)
        self.coef = None

    def _basis(self, along, across):
        B = _hat_basis(np.asarray(along, float), self.knots)
        t = (np.asarray(across, float) / self.across_len - 0.5)[:, None]
        return np.hstack([B, B * t])

    def fit(self, along, across, v, line_id, tol: float = 4.0, lam: float = 1.0, rng=None):
        along = np.asarray(along, float); across = np.asarray(across, float); v = np.asarray(v, float)
        line_id = np.asarray(line_id)
        rng = rng or np.random.default_rng(3)
        K = len(self.knots)
        # 1) odporny zarys a(along): mediana po oknach między węzłami
        idx = np.clip(np.searchsorted(self.knots, along, side="right") - 1, 0, K - 2)
        a0 = np.zeros(K)
        for i in range(K):
            m = (idx == i) | (idx == i - 1)
            a0[i] = np.median(v[m]) if m.sum() >= 3 else np.nan
        if np.isnan(a0).all():
            self.coef = np.zeros(2 * K)
            return 0
        ok = ~np.isnan(a0)
        a0 = np.interp(self.knots, self.knots[ok], a0[ok])
        base = _hat_basis(along, self.knots) @ a0
        # 2) na każdej linii: prosta (offset + nachylenie) do reszt, RANSAC -> inliery
        keep = np.zeros(len(v), bool)
        for lid in np.unique(line_id):
            m = np.nonzero(line_id == lid)[0]
            r = v[m] - base[m]; x = along[m]
            if len(m) < 3:
                continue
            best = np.abs(r - np.median(r)) < tol
            for _ in range(60):
                i, j = rng.choice(len(m), 2, replace=False)
                if x[i] == x[j]:
                    continue
                sl = (r[j] - r[i]) / (x[j] - x[i]); off = r[i] - sl * x[i]
                inl = np.abs(r - (sl * x + off)) < tol
                if inl.sum() > best.sum():
                    best = inl
            keep[m[best]] = True
        # 3) regularyzowane LS na inlierach (kara za drugie różnice współczynników a i b)
        A = self._basis(along[keep], across[keep])
        D = np.zeros((2 * (K - 2), 2 * K))
        for i in range(K - 2):
            D[i, i:i + 3] = (1, -2, 1)
            D[K - 2 + i, K + i:K + i + 3] = (1, -2, 1)
        lhs = A.T @ A + lam * (D.T @ D) + 1e-6 * np.eye(2 * K)
        self.coef = np.linalg.solve(lhs, A.T @ v[keep])
        return int(keep.sum())

    def __call__(self, along, across):
        along = np.atleast_1d(np.asarray(along, float)); across = np.atleast_1d(np.asarray(across, float))
        return self._basis(along, across) @ self.coef


def _snap(mask: np.ndarray, X: np.ndarray, Y: np.ndarray, radius: float, half_win: float, transpose: bool) -> np.ndarray:
    """Lokalna korekta węzłów: dla każdego węzła szuka linii w pasie +-radius wokół przewidywanego
    położenia (okno +-half_win wzdłuż linii). Korekty są filtrowane medianowo wzdłuż linii, a węzły
    bez wykrytej linii dostają korektę interpolowaną z sąsiadów. Zwraca tablicę korekt (0 gdy brak)."""
    if transpose:
        mask = mask.T; X, Y = Y.T, X.T  # teraz: linie "poziome" w masce transponowanej, X wzdłuż, Y w poprzek
    H, W = mask.shape
    R, C = X.shape
    corr = np.full((R, C), np.nan)
    for j in range(R):
        for k in range(C):
            x = X[j, k]; y = Y[j, k]
            x0 = int(max(0, x - half_win)); x1 = int(min(W, x + half_win))
            y0 = int(max(0, np.floor(y - radius))); y1 = int(min(H, np.ceil(y + radius) + 1))
            if x1 - x0 < 4 or y1 - y0 < 3:
                continue
            prof = (mask[y0:y1, x0:x1] > 0).mean(axis=1)
            if prof.max() >= 0.6:
                i = int(prof.argmax()); a, b = max(0, i - 2), min(len(prof), i + 3)
                w = prof[a:b]
                corr[j, k] = y0 + (np.arange(a, b) * w).sum() / w.sum() - y
    out = np.zeros((R, C))
    for j in range(R):
        row = corr[j]
        ok = ~np.isnan(row)
        if ok.sum() == 0:
            continue
        filled = np.interp(np.arange(C), np.nonzero(ok)[0], row[ok])
        # filtr medianowy (3) i odrzucenie korekt odbiegających od sąsiadów
        med = np.array([np.median(filled[max(0, i - 1):i + 2]) for i in range(C)])
        dev = np.abs(filled - med) > 0.3 * radius
        filled[dev] = med[dev]
        out[j] = np.clip(filled, -radius, radius)
    return out.T if transpose else out


def build_lattice(img_src: np.ndarray, img_warp: np.ndarray, H_src2warp: np.ndarray,
                  cell: int = 128, rows: int | None = None, cols: int | None = None, model: str = "poly", snap: bool = True) -> Lattice:
    Hw, Ww = img_warp.shape[:2]
    g = cv2.GaussianBlur(gray(img_warp), (3, 3), 0)
    line_px = max(2, int(round(max(Hw, Ww) / 500)))
    thin = thin_dark(g, line_px)
    min_len = int(0.025 * max(Hw, Ww))
    hor, ver = line_masks(thin, min_len)
    if rows is None or cols is None:
        r, c = estimate_counts(hor, ver, min_cell=int(0.03 * max(Hw, Ww)), max_cell=int(0.25 * min(Hw, Ww)))
        rows = rows or r; cols = cols or c
    py = Hw / rows; px = Ww / cols
    # próbki wszystkich linii poziomych -> pole dy(x, y); pionowych -> pole dx(x, y)
    sx, sy, sv, sl = [], [], [], []
    for j in range(rows + 1):
        xs, ys = _line_samples(hor, j * py, py, max(4, int(px / 4)))
        ok = (ys > 3) & (ys < Hw - 4)
        xs, ys = xs[ok], ys[ok]
        sx.append(xs); sy.append(np.full(len(xs), j * py)); sv.append(ys - j * py); sl.append(np.full(len(xs), j))
    if model == "sep":
        fy = SeparableField(np.arange(cols + 1) * px, Hw)
        fy.fit(np.concatenate(sx), np.concatenate(sy), np.concatenate(sv), np.concatenate(sl), tol=max(3.0, 0.02 * py))
    else:
        fy = PolyField(3)
        fy.fit(np.concatenate(sx), np.concatenate(sy), np.concatenate(sv), ransac_tol=max(6.0, 0.08 * py))
    sx, sy, sv, sl = [], [], [], []
    for k in range(cols + 1):
        ys, xs = _line_samples(ver.T, k * px, px, max(4, int(py / 4)))
        ok = (xs > 3) & (xs < Ww - 4)
        xs, ys = xs[ok], ys[ok]
        sx.append(np.full(len(ys), k * px)); sy.append(ys); sv.append(xs - k * px); sl.append(np.full(len(ys), k))
    if model == "sep":
        fx = SeparableField(np.arange(rows + 1) * py, Ww)
        fx.fit(np.concatenate(sy), np.concatenate(sx), np.concatenate(sv), np.concatenate(sl), tol=max(3.0, 0.02 * px))
    else:
        fxp = PolyField(3)
        fxp.fit(np.concatenate(sx), np.concatenate(sy), np.concatenate(sv), ransac_tol=max(6.0, 0.08 * px))
        fx = lambda yy, xx: fxp(xx, yy)
    nodes = np.zeros((rows + 1, cols + 1, 2), np.float64)
    J, K = np.meshgrid(np.arange(rows + 1), np.arange(cols + 1), indexing="ij")
    X0 = (K * px).astype(float); Y0 = (J * py).astype(float)
    X, Y = X0.copy(), Y0.copy()
    for _ in range(3):
        Y = Y0 + fy(X.ravel(), Y0.ravel()).reshape(Y0.shape)
        X = X0 + fx(Y.ravel(), X0.ravel()).reshape(X0.shape)
    if snap:
        Y = Y + _snap(hor, X, Y, radius=0.15 * py, half_win=px / 4, transpose=False)
        X = X + _snap(ver, X, Y, radius=0.15 * px, half_win=py / 4, transpose=True)
    nodes[..., 0] = np.clip(X, -0.5 * px, Ww + 0.5 * px)
    nodes[..., 1] = np.clip(Y, -0.5 * py, Hw + 0.5 * py)
    # węzły w obrazie wejściowym
    Hinv = np.linalg.inv(H_src2warp)
    pts = nodes.reshape(-1, 1, 2).astype(np.float64)
    nodes_src = cv2.perspectiveTransform(pts, Hinv).reshape(rows + 1, cols + 1, 2)
    # mapa przepróbkowania: dwuliniowa interpolacja węzłów w każdej komórce
    map_x = np.zeros((rows * cell, cols * cell), np.float32)
    map_y = np.zeros_like(map_x)
    t = (np.arange(cell) + 0.5) / cell
    tu, tv = np.meshgrid(t, t)  # tu: wzdłuż x, tv: wzdłuż y
    for j in range(rows):
        for k in range(cols):
            p00 = nodes_src[j, k]; p01 = nodes_src[j, k + 1]; p10 = nodes_src[j + 1, k]; p11 = nodes_src[j + 1, k + 1]
            X = (1 - tv) * ((1 - tu) * p00[0] + tu * p01[0]) + tv * ((1 - tu) * p10[0] + tu * p11[0])
            Y = (1 - tv) * ((1 - tu) * p00[1] + tu * p01[1]) + tv * ((1 - tu) * p10[1] + tu * p11[1])
            map_x[j * cell:(j + 1) * cell, k * cell:(k + 1) * cell] = X
            map_y[j * cell:(j + 1) * cell, k * cell:(k + 1) * cell] = Y
    rect = cv2.remap(img_src, map_x, map_y, interpolation=cv2.INTER_AREA, borderMode=cv2.BORDER_REPLICATE)
    Hs, Ws = img_src.shape[:2]
    margin = 0.15 * min(px, py)
    inside = ((nodes_src[..., 0] > -margin) & (nodes_src[..., 0] < Ws + margin)
              & (nodes_src[..., 1] > -margin) & (nodes_src[..., 1] < Hs + margin))
    outside = ~(inside[:-1, :-1] & inside[:-1, 1:] & inside[1:, :-1] & inside[1:, 1:])
    g2 = cv2.GaussianBlur(gray(rect), (3, 3), 0)
    rect_thin = thin_dark(g2, max(2, cell // 40))
    return Lattice(rows=rows, cols=cols, cell=cell, nodes_warp=nodes, nodes_src=nodes_src, rect=rect, rect_thin=rect_thin, outside=outside, map_x=map_x, map_y=map_y)
