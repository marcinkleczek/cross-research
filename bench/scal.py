"""Scalanie wyników z innej kopii bazy (np. workera uruchomionego na innej maszynie).

  python -m bench.scal inna_baza.sqlite

Kopiuje odpowiedzi (upsert po (uruchomienie_id, haslo_id)) i aktualizuje stan uruchomień, gdy w źródle jest dalej.
Uruchomienia i hasła są identyfikowane tymi samymi id, bo obie kopie pochodzą z tej samej bazy w repozytorium.
Nowe uruchomienia ze źródła (id nieobecne w bazie docelowej) są dopisywane z tym samym id.
"""
from __future__ import annotations

import sqlite3
import sys

from . import db


def scal(con, src_path: str) -> dict:
    src = sqlite3.connect(src_path); src.row_factory = sqlite3.Row
    cols_o = [r[1] for r in src.execute("PRAGMA table_info(odpowiedzi)")]
    cols_u = [r[1] for r in src.execute("PRAGMA table_info(uruchomienia)")]
    n_runs = n_ans = 0
    for u in src.execute("SELECT * FROM uruchomienia"):
        u = dict(u)
        mine = db.one(con, "SELECT * FROM uruchomienia WHERE id=?", (u["id"],))
        if mine is None:
            if not db.one(con, "SELECT id FROM modele WHERE id=?", (u["model_id"],)):
                m = src.execute("SELECT * FROM modele WHERE id=?", (u["model_id"],)).fetchone()
                if m:
                    mc = [r[1] for r in src.execute("PRAGMA table_info(modele)")]
                    db.execute(con, f"INSERT OR IGNORE INTO modele({','.join(mc)}) VALUES ({','.join('?'*len(mc))})", [m[c] for c in mc])
            db.execute(con, f"INSERT INTO uruchomienia({','.join(cols_u)}) VALUES ({','.join('?'*len(cols_u))})", [u[c] for c in cols_u]); n_runs += 1
        elif (u["zrobione"] or 0) > (mine["zrobione"] or 0) or (u["status"] == "zakonczone" and mine["status"] != "zakonczone"):
            db.execute(con, "UPDATE uruchomienia SET status=?, zrobione=?, bledy=?, start_at=COALESCE(?, start_at), koniec_at=?, koszt_usd=?, tokeny_wej=?, tokeny_wyj=?, uwagi=? WHERE id=?",
                       (u["status"], u["zrobione"], u["bledy"], u["start_at"], u["koniec_at"], u["koszt_usd"], u["tokeny_wej"], u["tokeny_wyj"], u["uwagi"], u["id"])); n_runs += 1
    for o in src.execute("SELECT * FROM odpowiedzi WHERE blad IS NULL"):
        o = dict(o)
        mine = db.one(con, "SELECT id, blad FROM odpowiedzi WHERE uruchomienie_id=? AND haslo_id=?", (o["uruchomienie_id"], o["haslo_id"]))
        if mine and mine["blad"] is None:
            continue
        cols = [c for c in cols_o if c != "id"]
        db.execute(con, f"INSERT INTO odpowiedzi({','.join(cols)}) VALUES ({','.join('?'*len(cols))}) ON CONFLICT(uruchomienie_id, haslo_id) DO UPDATE SET "
                   + ", ".join(f"{c}=excluded.{c}" for c in cols if c not in ("uruchomienie_id", "haslo_id")), [o[c] for c in cols]); n_ans += 1
    for r in db.rows(con, "SELECT DISTINCT uruchomienie_id FROM odpowiedzi"):
        db.execute(con, """UPDATE uruchomienia SET zrobione=(SELECT COUNT(*) FROM odpowiedzi WHERE uruchomienie_id=? AND blad IS NULL),
            bledy=(SELECT COUNT(*) FROM odpowiedzi WHERE uruchomienie_id=? AND blad IS NOT NULL) WHERE id=?""", (r["uruchomienie_id"],) * 3)
    db.log(con, "scal", f"scalono z {src_path}: uruchomień {n_runs}, odpowiedzi {n_ans}")
    return {"uruchomienia": n_runs, "odpowiedzi": n_ans}


if __name__ == "__main__":
    print(scal(db.init(), sys.argv[1]))
