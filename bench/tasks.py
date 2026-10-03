"""Warianty zadań, budowa promptu, parsowanie i ocena odpowiedzi.

Warianty (etap 1, pojedyncze hasła):
  bez_dlugosci  – sam opis
  z_dlugoscia   – opis + liczba liter
  wzorzec25 / wzorzec50 – opis + wzorzec z odsłoniętymi 25 % / 50 % liter (deterministycznie wg id hasła)
  wybor5        – opis + długość + 5 propozycji (poprawna i 4 dystraktory tej samej długości z puli odpowiedzi)

Ocena ścisła: wielkie litery, usunięte spacje i łączniki, PEŁNA zgodność znaków (Ł ≠ L, Ś ≠ S).
Ocena luźna (pomocnicza): to samo po zdjęciu znaków diakrytycznych.
"""
from __future__ import annotations

import hashlib
import json
import random
import re
import unicodedata

WARIANTY = ["bez_dlugosci", "z_dlugoscia", "wzorzec25", "wzorzec50", "wybor5"]

SCHEMA = {
    "type": "object",
    "properties": {
        "odpowiedz": {"type": "string", "description": "jedno hasło wielkimi literami, z polskimi znakami"},
        "kandydaci": {"type": "array", "items": {"type": "string"}, "description": "do 5 alternatyw, od najbardziej prawdopodobnej"},
        "pewnosc": {"type": "number", "description": "0..1"},
    },
    "required": ["odpowiedz", "kandydaci", "pewnosc"],
    "additionalProperties": False,
}

SYSTEM = (
    "Rozwiązujesz polską krzyżówkę. Dla podanego opisu podaj hasło: jeden wyraz (lub nazwę własną pisaną łącznie), "
    "WIELKIMI LITERAMI, z polskimi znakami diakrytycznymi (Ą, Ć, Ę, Ł, Ń, Ó, Ś, Ź, Ż), bez spacji i łączników. "
    "Odpowiadaj wyłącznie w formacie JSON: {\"odpowiedz\": \"HASŁO\", \"kandydaci\": [\"HASŁO\", \"ALTERNATYWA\", ...], \"pewnosc\": 0.0-1.0}. "
    "Żadnego tekstu poza JSON."
)

PRZYKLADY = [
    ("Stolica Polski", 8, "WARSZAWA"),
    ("Autor „Pana Tadeusza”", 10, "MICKIEWICZ"),
    ("Ptak symbol mądrości", 4, "SOWA"),
]


def normalize(s: str | None) -> str:
    if not s:
        return ""
    s = s.strip().upper().replace("Ł", "Ł")  # jawnie: Ł zachowane
    s = re.sub(r"[\s\-‐-―.·'’\"„”]", "", s)
    return s


def strip_diacritics(s: str) -> str:
    s = s.replace("Ł", "L").replace("ł", "l")
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def _rng(haslo_id: str, salt: str) -> random.Random:
    h = hashlib.sha256(f"{haslo_id}|{salt}".encode()).hexdigest()
    return random.Random(int(h[:16], 16))


def pattern(answer: str, frac: float, haslo_id: str) -> str:
    """Wzorzec z odsłoniętym ułamkiem liter, pozycje losowane deterministycznie z id hasła."""
    letters = list(normalize(answer))
    n = len(letters)
    k = max(1, round(frac * n)) if n > 1 else 0
    idx = set(_rng(haslo_id, f"wzorzec{frac}").sample(range(n), k))
    return " ".join(letters[i] if i in idx else "_" for i in range(n))


def distractors(answer: str, pool: list[str], haslo_id: str, k: int = 4) -> list[str]:
    a = normalize(answer)
    cands = sorted({normalize(p) for p in pool if normalize(p) != a and len(normalize(p)) == len(a)})
    rng = _rng(haslo_id, "wybor5")
    if len(cands) < k:  # za mało słów tej długości: dopełniamy anagramami/zamianą litery (oznaczone jako syntetyczne)
        alphabet = "AĄBCĆDEĘFGHIJKLŁMNŃOÓPRSŚTUWYZŹŻ"
        while len(cands) < k:
            s = list(a); i = rng.randrange(len(s)); s[i] = rng.choice(alphabet)
            s = "".join(s)
            if s != a and s not in cands:
                cands.append(s)
    chosen = rng.sample(cands, k)
    opts = chosen + [a]
    rng.shuffle(opts)
    return opts


def build_prompt(haslo: dict, wariant: str, pool: list[str] | None = None, przyklady: bool = False) -> tuple[str, dict]:
    """Zwraca (treść użytkownika, dane pomocnicze do oceny: wzorzec, opcje)."""
    opis = haslo["opis"]; n = haslo["dlugosc"]; hid = haslo["id"]; ans = haslo["odpowiedz"]
    extra = {}
    lines = []
    if przyklady:
        lines.append("Przykłady:")
        for o, d, a in PRZYKLADY:
            lines.append(f'Opis: {o} ({d} liter) -> {{"odpowiedz": "{a}", "kandydaci": ["{a}"], "pewnosc": 0.95}}')
        lines.append("")
    if wariant == "bez_dlugosci":
        lines.append(f"Opis: {opis}")
    elif wariant == "z_dlugoscia":
        lines.append(f"Opis: {opis}\nLiczba liter: {n}")
    elif wariant in ("wzorzec25", "wzorzec50"):
        frac = 0.25 if wariant == "wzorzec25" else 0.5
        pat = pattern(ans, frac, hid); extra["wzorzec"] = pat
        lines.append(f"Opis: {opis}\nLiczba liter: {n}\nWzorzec (znane litery, _ = nieznana): {pat}")
    elif wariant == "wybor5":
        opts = distractors(ans, pool or [], hid); extra["opcje"] = opts
        lines.append(f"Opis: {opis}\nLiczba liter: {n}\nWybierz jedną z propozycji: " + ", ".join(opts))
    else:
        raise ValueError(wariant)
    lines.append("Podaj JSON.")
    return "\n".join(lines), extra


def score(haslo: dict, parsed: dict | None, text: str | None, extra: dict) -> dict:
    ans = normalize(haslo["odpowiedz"])
    out = {"format_ok": 0, "odpowiedz": None, "kandydaci": None, "pewnosc": None, "poprawna": 0, "poprawna_luzno": 0, "w_top5": 0, "dlugosc_ok": None, "wzorzec_ok": None}
    if isinstance(parsed, dict) and isinstance(parsed.get("odpowiedz"), str):
        out["format_ok"] = 1
        raw = parsed["odpowiedz"]
        cands = parsed.get("kandydaci") if isinstance(parsed.get("kandydaci"), list) else []
        try:
            out["pewnosc"] = float(parsed.get("pewnosc")) if parsed.get("pewnosc") is not None else None
            if out["pewnosc"] is not None and out["pewnosc"] > 1:
                out["pewnosc"] /= 100.0
        except (TypeError, ValueError):
            out["pewnosc"] = None
    elif text:
        # awaryjnie: pierwsze słowo z wielkich liter w tekście
        m = re.search(r"[A-ZĄĆĘŁŃÓŚŹŻ]{2,}", text.upper())
        raw = m.group(0) if m else text
        cands = []
    else:
        return out
    a = normalize(raw)
    out["odpowiedz"] = a
    cl = [normalize(c) for c in cands if isinstance(c, str)][:5]
    if a and a not in cl:
        cl = [a] + cl[:4]
    out["kandydaci"] = cl
    out["poprawna"] = int(a == ans)
    out["poprawna_luzno"] = int(strip_diacritics(a) == strip_diacritics(ans))
    out["w_top5"] = int(ans in cl)
    out["dlugosc_ok"] = int(len(a) == len(ans))
    if "wzorzec" in extra:
        pat = extra["wzorzec"].split(" ")
        out["wzorzec_ok"] = int(len(a) == len(pat) and all(p == "_" or p == c for p, c in zip(pat, a)))
    return out
