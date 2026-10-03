"""Import wyników ekstrakcji (`output/<nazwa>/wynik.json`) do bazy benchmarku.
Uruchomienie: python -m bench.importer [KATALOG_WYNIKOW] [--zrodlo "Krzyżówki z koroną"]"""
from __future__ import annotations

import argparse
import json
import os
import time

from . import db

DIR_MAP = {"poziomo": "poziomo", "pionowo": "pionowo", "poziomo_wstecz": "poziomo_wstecz", "pionowo_wstecz": "pionowo_wstecz"}


def import_result(con, out_dir: str, zrodlo: str | None = None) -> dict:
    p = os.path.join(out_dir, "wynik.json")
    r = json.load(open(p, encoding="utf-8"))
    kid = os.path.splitext(r["plik"])[0]
    s = r["siatka"]
    db.execute(con, """INSERT INTO krzyzowki(id, plik, zrodlo, wiersze, kolumny, katalog_wyniku, zaimportowano_at, liczba_ramek, liczba_hasel, pasek_pol, zagadka, ostrzezenia)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET plik=excluded.plik, wiersze=excluded.wiersze, kolumny=excluded.kolumny, katalog_wyniku=excluded.katalog_wyniku,
        zaimportowano_at=excluded.zaimportowano_at, liczba_ramek=excluded.liczba_ramek, liczba_hasel=excluded.liczba_hasel, pasek_pol=excluded.pasek_pol,
        zagadka=excluded.zagadka, ostrzezenia=excluded.ostrzezenia, zrodlo=COALESCE(krzyzowki.zrodlo, excluded.zrodlo)""",
        (kid, r["plik"], zrodlo, s["wiersze"], s["kolumny"], os.path.abspath(out_dir), time.time(), len(r["opisy"]), len(r["hasla"]),
         (r.get("pasek_rozwiazania") or {}).get("liczba_pol"), 1 if r.get("zagadka") else 0, json.dumps(r["ostrzezenia"], ensure_ascii=False)))
    ramka_ids = {}
    for b in r["opisy"]:
        db.execute(con, """INSERT INTO ramki(krzyzowka_id, nr, plik_wycinka, komorki, prostokat)
            VALUES (?,?,?,?,?) ON CONFLICT(krzyzowka_id, nr) DO UPDATE SET plik_wycinka=excluded.plik_wycinka, komorki=excluded.komorki, prostokat=excluded.prostokat""",
            (kid, b["id"], os.path.join(os.path.abspath(out_dir), b.get("plik", "")), json.dumps(b["komorki"]), json.dumps(b["prostokat_px"])))
        ramka_ids[b["id"]] = db.one(con, "SELECT id FROM ramki WHERE krzyzowka_id=? AND nr=?", (kid, b["id"]))["id"]
    n = 0
    for w in r["hasla"]:
        if w["dlugosc"] < 2:
            continue
        hid = f"{kid}/H{w['id']}"
        rid = ramka_ids.get(w["opis"])
        # istniejące etykiety i odpowiedzi zachowujemy; aktualizujemy tylko geometrię i powiązanie z ramką
        db.execute(con, """INSERT INTO hasla(id, krzyzowka_id, nr, ramka_id, dlugosc, kierunek, start_w, start_k, litery)
            VALUES (?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET ramka_id=excluded.ramka_id, dlugosc=excluded.dlugosc, kierunek=excluded.kierunek,
            start_w=excluded.start_w, start_k=excluded.start_k, litery=excluded.litery""",
            (hid, kid, w["id"], rid, w["dlugosc"], DIR_MAP[w["kierunek"]], w["start"]["wiersz"], w["start"]["kolumna"],
             json.dumps([[l["wiersz"], l["kolumna"]] for l in w["litery"]])))
        n += 1
    # opis hasła = tekst ramki, jeśli już odczytany
    db.execute(con, """UPDATE hasla SET opis = (SELECT tekst FROM ramki WHERE ramki.id = hasla.ramka_id)
                       WHERE krzyzowka_id=? AND (opis IS NULL OR opis='') """, (kid,))
    return {"krzyzowka": kid, "ramki": len(r["opisy"]), "hasla": n}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("katalog", nargs="?", default="output")
    ap.add_argument("--zrodlo", default=None)
    a = ap.parse_args(argv)
    con = db.init()
    for d in sorted(os.listdir(a.katalog)):
        p = os.path.join(a.katalog, d)
        if os.path.isfile(os.path.join(p, "wynik.json")):
            print(import_result(con, p, a.zrodlo))
    db.log(con, "import", f"zaimportowano katalog {a.katalog}")


if __name__ == "__main__":
    main()
