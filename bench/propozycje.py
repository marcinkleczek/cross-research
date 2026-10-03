"""Półautomatyczny klucz: propozycje kilku modeli dla każdego hasła z opisem, uzgodnienie liter na skrzyżowaniach,
wynik do ręcznej weryfikacji na ekranie siatki.

  python -m bench.propozycje zbierz [--modele ollama:gemma4:31b,ollama:gpt-oss:120b,claude_cli:sonnet] [--krzyzowka ID]
  python -m bench.propozycje uzgodnij IMG_1803      # wypisuje wypełnienie i konflikty
"""
from __future__ import annotations

import argparse
import json
import time
from collections import defaultdict

from . import db, providers, tasks

DOMYSLNE_MODELE = ["ollama:gemma4:31b", "ollama:gpt-oss:120b", "claude_cli:sonnet"]


def zbierz(con, modele: list[str], krzyzowka: str | None = None, opoznienie: float = 0.3, stop=None) -> dict:
    sql = "SELECT * FROM hasla WHERE opis IS NOT NULL AND opis!='' AND dlugosc>=2"
    args = []
    if krzyzowka:
        sql += " AND krzyzowka_id=?"; args.append(krzyzowka)
    hasla = db.rows(con, sql + " ORDER BY id", args)
    pool = [r["odpowiedz"] for r in db.rows(con, "SELECT odpowiedz FROM hasla WHERE odpowiedz IS NOT NULL")]
    stat = {"zapytan": 0, "bledow": 0}
    for mid in modele:
        m = db.one(con, "SELECT * FROM modele WHERE id=?", (mid,))
        if not m:
            print("nieznany model", mid); continue
        prov = providers.get(m["dostawca"])
        for h in hasla:
            if stop and stop():
                return stat
            if db.one(con, "SELECT id FROM propozycje WHERE haslo_id=? AND model_id=?", (h["id"], mid)):
                continue
            hh = dict(h); hh["odpowiedz"] = hh["odpowiedz"] or "?" * h["dlugosc"]
            user, _ = tasks.build_prompt(hh, "z_dlugoscia", pool)
            res = prov.generate(m["nazwa"], tasks.SYSTEM, user, schema=tasks.SCHEMA)
            parsed = res.get("parsed")
            if not res.get("blad") and not (isinstance(parsed, dict) and parsed.get("odpowiedz")):
                res2 = prov.generate(m["nazwa"], tasks.SYSTEM_TEKST, user, schema=None)
                parsed = tasks.parse_text(res2.get("text"), h["dlugosc"]) if not res2.get("blad") else None
            stat["zapytan"] += 1
            if res.get("blad") or not parsed:
                stat["bledow"] += 1
                print(f"{h['id']} {mid}: BŁĄD {res.get('blad') or 'brak odpowiedzi'}"[:160]); continue
            odp = tasks.normalize(parsed.get("odpowiedz"))
            kand = [tasks.normalize(c) for c in (parsed.get("kandydaci") or []) if isinstance(c, str)]
            kand = [odp] + [c for c in kand if c and c != odp]
            try:
                pew = float(parsed.get("pewnosc")) if parsed.get("pewnosc") is not None else None
                if pew is not None and pew > 1: pew /= 100
            except (TypeError, ValueError):
                pew = None
            db.execute(con, "INSERT OR REPLACE INTO propozycje(haslo_id, model_id, odpowiedz, kandydaci, pewnosc, surowe, at) VALUES (?,?,?,?,?,?,?)",
                       (h["id"], mid, odp, json.dumps(kand[:5], ensure_ascii=False), pew, (res.get("text") or "")[:2000], time.time()))
            print(f"{h['id']} {mid}: {odp} ({len(odp)}/{h['dlugosc']})")
            time.sleep(opoznienie)
    db.log(con, "propozycje", f"zebrano: {stat}, modele {modele}, krzyżówka {krzyzowka or 'wszystkie'}")
    return stat


def wagi_modeli(con) -> dict[str, float]:
    """Waga modelu = trafność ścisła na hasłach z kluczem (z uruchomień), domyślnie 0.5."""
    out = {}
    for r in db.rows(con, """SELECT u.model_id, AVG(o.poprawna) t, COUNT(*) n FROM odpowiedzi o JOIN uruchomienia u ON u.id=o.uruchomienie_id
                             WHERE o.blad IS NULL AND u.wariant='z_dlugoscia' GROUP BY u.model_id"""):
        if r["n"] >= 10:
            out[r["model_id"]] = max(0.15, float(r["t"] or 0))
    return out


def uzgodnij(con, krzyzowka: str, rundy: int = 4) -> dict:
    """Dla każdego hasła wybiera kandydata o najwyższym wyniku: głosy modeli (ważone trafnością i pewnością)
    + zgodność liter na skrzyżowaniach z aktualnie wybranymi sąsiadami − konflikty."""
    hasla = db.rows(con, "SELECT * FROM hasla WHERE krzyzowka_id=? AND dlugosc>=2 ORDER BY nr", (krzyzowka,))
    props = defaultdict(list)
    for p in db.rows(con, "SELECT p.* FROM propozycje p JOIN hasla h ON h.id=p.haslo_id WHERE h.krzyzowka_id=?", (krzyzowka,)):
        props[p["haslo_id"]].append(p)
    wagi = wagi_modeli(con)
    cand_scores: dict[str, dict[str, float]] = {}
    votes: dict[str, dict[str, list]] = {}
    for h in hasla:
        sc = defaultdict(float); vt = defaultdict(list)
        if h["odpowiedz"]:
            sc[tasks.normalize(h["odpowiedz"])] += 10.0  # klucz ręczny ma pierwszeństwo
            vt[tasks.normalize(h["odpowiedz"])].append("klucz")
            for a in tasks.accepted(h):
                sc[a] += 10.0
        for p in props[h["id"]]:
            w = wagi.get(p["model_id"], 0.5)
            pew = p["pewnosc"] if p["pewnosc"] is not None else 0.6
            kand = json.loads(p["kandydaci"] or "[]")
            for i, c in enumerate(kand):
                if len(c) != h["dlugosc"]:
                    continue
                sc[c] += w * (0.5 + 0.5 * pew) * (1.0 if i == 0 else 0.35)
                if i == 0:
                    vt[c].append(p["model_id"])
        cand_scores[h["id"]] = dict(sc); votes[h["id"]] = dict(vt)
    cells_of = {h["id"]: [tuple(c) for c in json.loads(h["litery"])] for h in hasla}
    best = {hid: (max(sc.items(), key=lambda x: x[1])[0] if sc else None) for hid, sc in cand_scores.items()}
    for _ in range(rundy):
        # litery w kratkach wg aktualnych wyborów
        grid: dict[tuple, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        for hid, w in best.items():
            if w:
                for i, c in enumerate(cells_of[hid]):
                    grid[c][w[i]] += cand_scores[hid].get(w, 0.0)
        new_best = {}
        for hid, sc in cand_scores.items():
            if not sc:
                new_best[hid] = None; continue
            own = best.get(hid)
            scored = {}
            for cand, s in sc.items():
                bonus = 0.0
                for i, c in enumerate(cells_of[hid]):
                    others = {k: v for k, v in grid[c].items()}
                    if own:
                        others[own[i]] = others.get(own[i], 0.0) - sc.get(own, 0.0)  # bez własnego głosu
                    tot = sum(v for v in others.values() if v > 0)
                    if tot <= 0:
                        continue
                    agree = max(others.get(cand[i], 0.0), 0.0) / tot
                    bonus += 0.6 * agree - 0.5 * (1 - agree)
                scored[cand] = s + bonus
            new_best[hid] = max(scored.items(), key=lambda x: x[1])[0]
        if new_best == best:
            break
        best = new_best
    # raport: litery w kratkach, konflikty
    grid_letters: dict[tuple, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    for hid, w in best.items():
        if w:
            for i, c in enumerate(cells_of[hid]):
                grid_letters[c][w[i]].append(hid)
    konflikty_cell = {c: dict(v) for c, v in grid_letters.items() if len(v) > 1}
    out = []
    for h in hasla:
        hid = h["id"]; w = best.get(hid)
        sc = cand_scores[hid]
        ranked = sorted(sc.items(), key=lambda x: -x[1])
        konfl = [c for c in cells_of[hid] if c in konflikty_cell]
        alt = [c for c, s in ranked if w and c != w and s >= 0.6 * sc.get(w, 0)]
        out.append({"haslo_id": hid, "nr": h["nr"], "opis": h["opis"], "dlugosc": h["dlugosc"], "kierunek": h["kierunek"], "litery": cells_of[hid],
                    "klucz": h["odpowiedz"], "alternatywy": h["alternatywy"], "gotowe": h["gotowe"],
                    "propozycja": w, "wynik": round(sc.get(w, 0), 2) if w else None, "glosy": votes[hid].get(w, []) if w else [],
                    "kandydaci": [{"slowo": c, "wynik": round(s, 2), "glosy": votes[hid].get(c, [])} for c, s in ranked[:6]],
                    "konflikty": konfl, "alternatywy_sugerowane": alt[:3],
                    "stan": "klucz" if h["odpowiedz"] else ("brak" if not w else ("konflikt" if konfl else ("pewne" if len(votes[hid].get(w, [])) >= 2 else "slabe")))})
    letters = {}
    for c, v in grid_letters.items():
        letters[f"{c[0]},{c[1]}"] = max(v.items(), key=lambda x: len(x[1]))[0]
    return {"krzyzowka": krzyzowka, "hasla": out, "litery": letters, "konflikty": {f"{c[0]},{c[1]}": v for c, v in konflikty_cell.items()},
            "modele": sorted({p["model_id"] for ps in props.values() for p in ps}), "wagi": wagi}


def main(argv=None):
    ap = argparse.ArgumentParser(); sub = ap.add_subparsers(dest="cmd", required=True)
    z = sub.add_parser("zbierz"); z.add_argument("--modele", default=",".join(DOMYSLNE_MODELE)); z.add_argument("--krzyzowka"); z.add_argument("--opoznienie", type=float, default=0.3)
    u = sub.add_parser("uzgodnij"); u.add_argument("krzyzowka")
    a = ap.parse_args(argv); con = db.init()
    if a.cmd == "zbierz":
        print(zbierz(con, a.modele.split(","), a.krzyzowka, a.opoznienie))
    else:
        r = uzgodnij(con, a.krzyzowka)
        for h in r["hasla"]:
            print(f"H{h['nr']:>2} {h['stan']:8s} {str(h['propozycja']):12s} głosy {len(h['glosy'])} {h['opis'][:50]}" + (f"  KONFLIKT {h['konflikty']}" if h['konflikty'] else ""))


if __name__ == "__main__":
    main()
