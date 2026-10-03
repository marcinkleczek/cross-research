"""Pełny potok: zdjęcie -> struktura krzyżówki (JSON) + wycinki + nakładka kontrolna."""
from __future__ import annotations

import json
import os
from dataclasses import asdict

import numpy as np
import cv2

from . import __version__
from .image import load_rgb
from .grid import find_grid, warp
from .lattice import build_lattice
from .cells import (LETTER, CLUE, PICTURE, OUTSIDE, line_mask, detect_edges, cell_features, classify_cells,
                    crossing_text)
from .clues import find_clue_boxes
from .arrows import find_arrows, ink_without_lines, detect_stems
from .digits import find_numbers, assign_numbers
from .words import build_words
from .strip import extended_warp, find_strip
from .debug import render_overlay

DIR_PL = {"prawo": "poziomo", "dol": "pionowo", "lewo": "poziomo_wstecz", "gora": "pionowo_wstecz"}


def _crop_src(img: np.ndarray, map_x: np.ndarray, map_y: np.ndarray, x0: int, y0: int, x1: int, y1: int, scale: float) -> np.ndarray:
    """Wycinek prostokąta z obrazu zrektyfikowanego, pobrany z oryginału (pełna rozdzielczość)."""
    H, W = map_x.shape
    pts = []
    for (x, y) in ((x0, y0), (x1 - 1, y0), (x1 - 1, y1 - 1), (x0, y1 - 1)):
        xx = min(max(x, 0), W - 1); yy = min(max(y, 0), H - 1)
        pts.append((map_x[yy, xx], map_y[yy, xx]))
    w = max(8, int(round((x1 - x0) * scale))); h = max(8, int(round((y1 - y0) * scale)))
    M = cv2.getPerspectiveTransform(np.array(pts, np.float32), np.array([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]], np.float32))
    return cv2.warpPerspective(img, M, (w, h), flags=cv2.INTER_CUBIC)


def _json_default(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, (np.bool_,)):
        return bool(o)
    raise TypeError(f"nieserializowalny typ {type(o)}")


def analyze(path: str, out_dir: str, cell: int = 128, save_crops: bool = True) -> dict:
    os.makedirs(out_dir, exist_ok=True)
    img = load_rgb(path)
    q = find_grid(img)
    w, Hm = warp(img, q)
    lat = build_lattice(img, w, Hm, cell=cell)
    rows, cols = lat.rows, lat.cols
    lm = line_mask(lat.rect, lat.rect_thin, cell)
    E = detect_edges(lm, rows, cols, cell)
    Et = detect_edges(lat.rect_thin, rows, cols, cell)
    F = cell_features(lat.rect, rows, cols, cell)
    types, info = classify_cells(F, E, Et, lat.outside, crossing_text(F.dark, rows, cols, cell))
    boxes = find_clue_boxes(types, lm, cell)
    nolines = ink_without_lines(F.dark, cell)
    arrows = find_arrows(types, nolines, cell)
    detect_stems(arrows, lat.rect_thin, cell)
    words, warn_w = build_words(types, E, arrows, boxes, cell)
    # pasek rozwiązania
    ext, Hext, grid_h, ext_x0 = extended_warp(img, q, scale=(cols * cell) / q.width)
    strip, warn_s = find_strip(ext, grid_h, cell)
    cands = find_numbers(types, nolines, cell, [a.bbox for a in arrows])
    numbers, warn_n = assign_numbers(cands, strip.total or None)
    num_at = {(c.row, c.col): v for v, c in numbers.items()}
    warnings = warn_w + warn_s + warn_n
    if lat.outside.any():
        warnings.append(f"{int(lat.outside.sum())} komórek leży poza kadrem zdjęcia")
    # skala wycinków: piksele oryginału na piksel obrazu zrektyfikowanego
    src_cell = float(np.median(np.linalg.norm(np.diff(lat.nodes_src, axis=1), axis=2)))
    crop_scale = max(1.0, src_cell / cell)

    def cell_rect_src(j0, k0, j1, k1):
        return [lat.nodes_src[j0, k0].round(1).tolist(), lat.nodes_src[j0, k1].round(1).tolist(),
                lat.nodes_src[j1, k1].round(1).tolist(), lat.nodes_src[j1, k0].round(1).tolist()]

    result = {
        "plik": os.path.basename(path),
        "wersja": __version__,
        "siatka": {
            "wiersze": rows, "kolumny": cols, "bok_komorki_px": cell,
            "narozniki_na_zdjeciu": q.corners.round(1).tolist(),
            "wezly_na_zdjeciu": lat.nodes_src.round(1).tolist(),
        },
        "komorki": ["".join(types[j, k] for k in range(cols)) for j in range(rows)],
        "legenda_komorek": {"L": "litera", "O": "opis", "R": "rysunek/zagadka", "X": "poza kadrem"},
        "opisy": [],
        "hasla": [],
        "numery": {},
        "pasek_rozwiazania": None,
        "zagadka": None,
        "ostrzezenia": warnings,
    }
    if save_crops:
        os.makedirs(os.path.join(out_dir, "opisy"), exist_ok=True)
    for b in boxes:
        entry = {
            "id": b.id,
            "komorki": [{"wiersz": j, "kolumna": k} for j, k in b.cells],
            "prostokat_px": [b.x0, b.y0, b.x1, b.y1],
            "hasla": b.words,
        }
        if save_crops:
            crop = _crop_src(img, lat.map_x, lat.map_y, b.x0 - 2, b.y0 - 2, b.x1 + 2, b.y1 + 2, crop_scale)
            fn = os.path.join("opisy", f"opis_{b.id:02d}.png")
            cv2.imwrite(os.path.join(out_dir, fn), cv2.cvtColor(crop, cv2.COLOR_RGB2BGR))
            entry["plik"] = fn
        result["opisy"].append(entry)
    for wd in words:
        result["hasla"].append({
            "id": wd.id,
            "opis": wd.clue_id,
            "start": {"wiersz": wd.start[0], "kolumna": wd.start[1]},
            "kierunek": DIR_PL[wd.direction],
            "dlugosc": len(wd.cells),
            "litery": [{"wiersz": j, "kolumna": k, "numer": num_at.get((j, k))} for j, k in wd.cells],
            "strzalka": {"wiersz": wd.arrow.row, "kolumna": wd.arrow.col, "x_px": round(wd.arrow.x, 1), "y_px": round(wd.arrow.y, 1),
                         "pewnosc": round(wd.arrow.score, 2)},
            "uwaga": wd.note or None,
        })
    for v in sorted(numbers):
        c = numbers[v]
        bv, bs = c.best_value()
        result["numery"][str(v)] = {"wiersz": c.row, "kolumna": c.col, "pewnosc": round(c.value_score(v), 2)}
    if strip.total:
        entry = {
            "liczba_pol": strip.total,
            "wiersze_paska": [r.boxes for r in strip.rows],
            "dlugosci_slow": strip.word_lengths,
        }
        if save_crops and strip.bbox:
            x0, y0, x1, y1 = strip.bbox
            m = int(0.15 * cell)
            crop = ext[max(0, y0 - m):y1 + m, max(0, x0 - m):x1 + m]
            cv2.imwrite(os.path.join(out_dir, "pasek.png"), cv2.cvtColor(crop, cv2.COLOR_RGB2BGR))
            entry["plik"] = "pasek.png"
            if strip.caption_bbox:
                cx0, cy0, cx1, cy1 = strip.caption_bbox
                if cy1 - cy0 > 0.15 * cell:
                    cap = ext[cy0:cy1, cx0:cx1]
                    cv2.imwrite(os.path.join(out_dir, "podpis.png"), cv2.cvtColor(cap, cv2.COLOR_RGB2BGR))
                    entry["podpis_plik"] = "podpis.png"
        result["pasek_rozwiazania"] = entry
    pic = info.get("rysunek")
    if pic:
        j0, k0, h, wdt = pic["wiersz"], pic["kolumna"], pic["wysokosc"], pic["szerokosc"]
        entry = {
            "typ": "rysunek_z_dymkiem",
            "opis": "Pole bez liter: zagadka obrazkowa / tekst, którego brakujący fragment jest rozwiązaniem (hasłem z paska).",
            "wiersz": j0, "kolumna": k0, "wysokosc": h, "szerokosc": wdt,
            "obrys_na_zdjeciu": cell_rect_src(j0, k0, j0 + h, k0 + wdt),
        }
        if save_crops:
            crop = _crop_src(img, lat.map_x, lat.map_y, k0 * cell, j0 * cell, (k0 + wdt) * cell, (j0 + h) * cell, crop_scale)
            cv2.imwrite(os.path.join(out_dir, "zagadka.png"), cv2.cvtColor(crop, cv2.COLOR_RGB2BGR))
            entry["plik"] = "zagadka.png"
        result["zagadka"] = entry
    with open(os.path.join(out_dir, "wynik.json"), "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1, default=_json_default)
    overlay = render_overlay(lat, types, E, boxes, arrows, words, numbers)
    cv2.imwrite(os.path.join(out_dir, "naklad.jpg"), cv2.cvtColor(overlay, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 80])
    cv2.imwrite(os.path.join(out_dir, "siatka.jpg"), cv2.cvtColor(lat.rect, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 85])
    return result
