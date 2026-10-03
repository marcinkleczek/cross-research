"""Agregaty wyników z przedziałami ufności (bootstrap) i miarami kalibracji."""
from __future__ import annotations

import math
import random
from collections import defaultdict


def bootstrap_ci(values: list[int | float], n: int = 1000, seed: int = 1) -> tuple[float, float, float]:
    if not values:
        return (float("nan"),) * 3
    rng = random.Random(seed)
    m = sum(values) / len(values)
    if len(values) < 3:
        return m, m, m
    ms = []
    L = len(values)
    for _ in range(n):
        s = 0.0
        for _ in range(L):
            s += values[rng.randrange(L)]
        ms.append(s / L)
    ms.sort()
    return m, ms[int(0.025 * n)], ms[int(0.975 * n) - 1]


def brier_ece(pairs: list[tuple[float, int]], bins: int = 10) -> dict:
    """pairs = (pewność, poprawna). Brier = średni (p - y)^2; ECE = ważona różnica |pewność - trafność| w koszykach."""
    pairs = [(p, y) for p, y in pairs if p is not None]
    if not pairs:
        return {"brier": None, "ece": None, "n": 0, "srednia_pewnosc": None}
    brier = sum((p - y) ** 2 for p, y in pairs) / len(pairs)
    buckets = defaultdict(list)
    for p, y in pairs:
        buckets[min(bins - 1, int(p * bins))].append((p, y))
    ece = 0.0
    for b in buckets.values():
        conf = sum(p for p, _ in b) / len(b); acc = sum(y for _, y in b) / len(b)
        ece += len(b) / len(pairs) * abs(conf - acc)
    return {"brier": round(brier, 4), "ece": round(ece, 4), "n": len(pairs), "srednia_pewnosc": round(sum(p for p, _ in pairs) / len(pairs), 3)}


def summarize(rows: list[dict]) -> dict:
    """rows: odpowiedzi z dołączonymi polami hasła (kategoria, polskosc, dlugosc)."""
    ok = [r for r in rows if not r.get("blad")]
    def rate(key, subset):
        vals = [int(r[key] or 0) for r in subset if r.get(key) is not None]
        m, lo, hi = bootstrap_ci(vals)
        return {"srednia": None if math.isnan(m) else round(m, 4), "ci_dol": None if math.isnan(lo) else round(lo, 4), "ci_gora": None if math.isnan(hi) else round(hi, 4), "n": len(vals)}
    res = {
        "n": len(rows), "bledy": len(rows) - len(ok),
        "trafnosc": rate("poprawna", ok), "trafnosc_luzno": rate("poprawna_luzno", ok), "top5": rate("w_top5", ok),
        "format_ok": rate("format_ok", ok), "dlugosc_ok": rate("dlugosc_ok", ok), "wzorzec_ok": rate("wzorzec_ok", ok),
        "kalibracja": brier_ece([(r.get("pewnosc"), int(r.get("poprawna") or 0)) for r in ok if r.get("format_ok")]),
        "czas_ms_mediana": sorted(r["czas_ms"] or 0 for r in ok)[len(ok) // 2] if ok else None,
        "koszt_usd": round(sum(r.get("koszt_usd") or 0 for r in rows), 4),
        "tokeny_wej": sum(r.get("tokeny_wej") or 0 for r in rows), "tokeny_wyj": sum(r.get("tokeny_wyj") or 0 for r in rows),
        "wg": {},
    }
    for dim in ("kategoria", "polskosc", "dlugosc", "typ_opisu", "krzyzowka_id"):
        groups = defaultdict(list)
        for r in ok:
            groups[str(r.get(dim) if r.get(dim) is not None else "–")].append(r)
        res["wg"][dim] = {k: {"trafnosc": rate("poprawna", v), "trafnosc_luzno": rate("poprawna_luzno", v)} for k, v in sorted(groups.items())}
    return res
