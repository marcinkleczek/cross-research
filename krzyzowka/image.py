"""Wczytywanie zdjęć: orientacja EXIF, skalowanie, maska ciemnych pikseli."""
from __future__ import annotations

import numpy as np
import cv2
from PIL import Image, ImageOps


def load_rgb(path: str) -> np.ndarray:
    """Zwraca obraz RGB (uint8) z zastosowaną orientacją EXIF."""
    im = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    return np.asarray(im)


def resize_long(img: np.ndarray, long_side: int) -> tuple[np.ndarray, float]:
    """Skaluje tak, by dłuższy bok miał `long_side` pikseli. Zwraca (obraz, współczynnik)."""
    h, w = img.shape[:2]
    s = long_side / max(h, w)
    if s >= 1.0:
        return img, 1.0
    out = cv2.resize(img, (int(round(w * s)), int(round(h * s))), interpolation=cv2.INTER_AREA)
    return out, s


def gray(img: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)


def dark_mask(g: np.ndarray, block: int = 41, c: int = 18) -> np.ndarray:
    """Maska tuszu (linie, tekst) odporna na nierówne oświetlenie: próg adaptacyjny."""
    block = max(3, block | 1)
    return cv2.adaptiveThreshold(g, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, block, c)
