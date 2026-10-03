"""Kolejka uruchomień benchmarku: tworzenie, worker, wznawianie.

  python -m bench.runner modele                     # odśwież rejestr modeli (darmowe OpenRouter, Ollama, claude_cli)
  python -m bench.runner dodaj --model openrouter:qwen/qwen3.8-27b:free --wariant z_dlugoscia [--limit 200] [--opoznienie 2]
  python -m bench.runner start                      # worker: wykonuje kolejne uruchomienia z kolejki, aż będzie pusta
  python -m bench.runner start --jedno ID           # tylko wskazane
"""
from __future__ import annotations

import argparse
import json
import random
import threading
import time
import traceback

from . import db, providers, tasks

_stop_flags: dict[int, bool] = {}
_worker_thread: threading.Thread | None = None


def refresh_models(con) -> dict:
    added = 0
    def upsert(dostawca, nazwa, **kw):
        nonlocal added
        mid = f"{dostawca}:{nazwa}"
        ex = db.one(con, "SELECT id FROM modele WHERE id=?", (mid,))
        if not ex:
            added += 1
        db.execute(con, """INSERT INTO modele(id, dostawca, nazwa, etykieta, parametry_mld, darmowy, cena_wej, cena_wyj, kontekst, uwagi, dodano_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET etykieta=excluded.etykieta, darmowy=excluded.darmowy,
            cena_wej=excluded.cena_wej, cena_wyj=excluded.cena_wyj, kontekst=excluded.kontekst""",
            (mid, dostawca, nazwa, kw.get("etykieta") or nazwa, kw.get("parametry_mld"), int(kw.get("darmowy", 0)), kw.get("cena_wej"), kw.get("cena_wyj"), kw.get("kontekst"), kw.get("uwagi"), time.time()))
    info = {}
    try:
        for m in providers.OpenRouter().list_models(only_free=True):
            if m.get("modalnosc", "").startswith("text") or "text" in (m.get("modalnosc") or ""):
                upsert("openrouter", m["nazwa"], etykieta=m.get("etykieta"), darmowy=1, cena_wej=0, cena_wyj=0, kontekst=m.get("kontekst"), uwagi="darmowy, limity dostawcy")
        info["openrouter"] = "ok"
    except Exception as e:  # noqa: BLE001
        info["openrouter"] = f"błąd: {e}"
    try:
        for m in providers.Ollama().list_models():
            p = m.get("parametry")
            try:
                pm = float(str(p).upper().rstrip("B")) if p else None
            except ValueError:
                pm = None
            upsert("ollama", m["nazwa"], parametry_mld=pm, darmowy=1, uwagi="Ollama (lokalnie lub chmura)")
        info["ollama"] = "ok"
    except Exception as e:  # noqa: BLE001
        info["ollama"] = f"błąd: {e}"
    for m in providers.ClaudeCLI().list_models():
        upsert("claude_cli", m["nazwa"], darmowy=1, uwagi="Claude Code CLI, w ramach abonamentu (nie 'mały' model; punkt odniesienia)")
    info["claude_cli"] = "ok"
    info["dodano"] = added
    db.log(con, "modele", f"odświeżono rejestr: {info}")
    return info


def select_items(con, podzbior: dict) -> list[dict]:
    sql = "SELECT * FROM hasla WHERE gotowe=1 AND opis IS NOT NULL AND opis!='' AND odpowiedz IS NOT NULL AND odpowiedz!=''"
    args: list = []
    if podzbior.get("krzyzowki"):
        sql += " AND krzyzowka_id IN (%s)" % ",".join("?" * len(podzbior["krzyzowki"])); args += podzbior["krzyzowki"]
    if podzbior.get("kategorie"):
        sql += " AND kategoria IN (%s)" % ",".join("?" * len(podzbior["kategorie"])); args += podzbior["kategorie"]
    if podzbior.get("polskosc"):
        sql += " AND polskosc=?"; args.append(podzbior["polskosc"])
    if podzbior.get("zrodlo_odpowiedzi"):
        sql += " AND zrodlo_odpowiedzi=?"; args.append(podzbior["zrodlo_odpowiedzi"])
    items = db.rows(con, sql + " ORDER BY id", args)
    if podzbior.get("limit") and len(items) > int(podzbior["limit"]):
        rng = random.Random(int(podzbior.get("ziarno", 1)))
        items = rng.sample(items, int(podzbior["limit"]))
        items.sort(key=lambda r: r["id"])
    return items


def add_run(con, model_id: str, wariant: str, podzbior: dict | None = None, parametry: dict | None = None, nazwa: str | None = None, priorytet: int = 100) -> int:
    if wariant not in tasks.WARIANTY:
        raise ValueError(f"nieznany wariant {wariant}")
    if not db.one(con, "SELECT id FROM modele WHERE id=?", (model_id,)):
        raise ValueError(f"nieznany model {model_id} (odśwież rejestr lub dodaj ręcznie)")
    podzbior = podzbior or {}; parametry = {"temperatura": 0.0, "opoznienie_s": 1.0, "przyklady": False, "max_tokens": 300, **(parametry or {})}
    n = len(select_items(con, podzbior))
    nazwa = nazwa or f"{model_id} · {wariant} · {n} haseł"
    cur = db.execute(con, "INSERT INTO uruchomienia(nazwa, model_id, wariant, podzbior, parametry, status, priorytet, utworzono_at, liczba_hasel) VALUES (?,?,?,?,?,?,?,?,?)",
                     (nazwa, model_id, wariant, json.dumps(podzbior), json.dumps(parametry), "w_kolejce", priorytet, time.time(), n))
    db.log(con, "kolejka", f"dodano uruchomienie {cur.lastrowid}: {nazwa}")
    return cur.lastrowid


def run_one(con, run_id: int) -> None:
    run = db.one(con, "SELECT * FROM uruchomienia WHERE id=?", (run_id,))
    if not run:
        return
    model = db.one(con, "SELECT * FROM modele WHERE id=?", (run["model_id"],))
    podzbior = json.loads(run["podzbior"] or "{}"); par = json.loads(run["parametry"] or "{}")
    items = select_items(con, podzbior)
    done_ids = {r["haslo_id"] for r in db.rows(con, "SELECT haslo_id FROM odpowiedzi WHERE uruchomienie_id=? AND blad IS NULL", (run_id,))}
    pool = [r["odpowiedz"] for r in db.rows(con, "SELECT odpowiedz FROM hasla WHERE odpowiedz IS NOT NULL")]
    prov = providers.get(model["dostawca"])
    db.execute(con, "UPDATE uruchomienia SET status='trwa', start_at=COALESCE(start_at, ?), liczba_hasel=?, blad=NULL WHERE id=?", (time.time(), len(items), run_id))
    _stop_flags[run_id] = False
    consecutive_limit = 0
    try:
        for h in items:
            if _stop_flags.get(run_id):
                db.execute(con, "UPDATE uruchomienia SET status='wstrzymane' WHERE id=?", (run_id,))
                db.log(con, "kolejka", f"wstrzymano uruchomienie {run_id}")
                return
            if h["id"] in done_ids:
                continue
            user, extra = tasks.build_prompt(h, run["wariant"], pool, przyklady=bool(par.get("przyklady")))
            res = prov.generate(model["nazwa"], tasks.SYSTEM, user, schema=tasks.SCHEMA, temperature=float(par.get("temperatura", 0.0)), max_tokens=int(par.get("max_tokens", 300)))
            sc = tasks.score(h, res.get("parsed"), res.get("text"), extra) if not res.get("blad") else {}
            if res.get("blad") and ("HTTP 429" in res["blad"] or "HTTP 503" in res["blad"] or "HTTP 0" in res["blad"]):
                consecutive_limit += 1
            else:
                consecutive_limit = 0
            db.execute(con, """INSERT INTO odpowiedzi(uruchomienie_id, haslo_id, prompt, surowe, odpowiedz, kandydaci, pewnosc, poprawna, poprawna_luzno, w_top5, dlugosc_ok, wzorzec_ok, format_ok,
                czas_ms, tokeny_wej, tokeny_wyj, koszt_usd, blad, at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(uruchomienie_id, haslo_id) DO UPDATE SET prompt=excluded.prompt, surowe=excluded.surowe, odpowiedz=excluded.odpowiedz, kandydaci=excluded.kandydaci,
                pewnosc=excluded.pewnosc, poprawna=excluded.poprawna, poprawna_luzno=excluded.poprawna_luzno, w_top5=excluded.w_top5, dlugosc_ok=excluded.dlugosc_ok, wzorzec_ok=excluded.wzorzec_ok,
                format_ok=excluded.format_ok, czas_ms=excluded.czas_ms, tokeny_wej=excluded.tokeny_wej, tokeny_wyj=excluded.tokeny_wyj, koszt_usd=excluded.koszt_usd, blad=excluded.blad, at=excluded.at""",
                (run_id, h["id"], user, json.dumps({"text": res.get("text"), "surowe": res.get("surowe")}, ensure_ascii=False, default=str)[:30000],
                 sc.get("odpowiedz"), json.dumps(sc.get("kandydaci"), ensure_ascii=False) if sc.get("kandydaci") is not None else None, sc.get("pewnosc"),
                 sc.get("poprawna"), sc.get("poprawna_luzno"), sc.get("w_top5"), sc.get("dlugosc_ok"), sc.get("wzorzec_ok"), sc.get("format_ok"),
                 res.get("czas_ms"), (res.get("usage") or {}).get("wej"), (res.get("usage") or {}).get("wyj"), res.get("koszt_usd"), res.get("blad"), time.time()))
            db.execute(con, """UPDATE uruchomienia SET zrobione=(SELECT COUNT(*) FROM odpowiedzi WHERE uruchomienie_id=? AND blad IS NULL),
                bledy=(SELECT COUNT(*) FROM odpowiedzi WHERE uruchomienie_id=? AND blad IS NOT NULL),
                koszt_usd=(SELECT COALESCE(SUM(koszt_usd),0) FROM odpowiedzi WHERE uruchomienie_id=?),
                tokeny_wej=(SELECT COALESCE(SUM(tokeny_wej),0) FROM odpowiedzi WHERE uruchomienie_id=?),
                tokeny_wyj=(SELECT COALESCE(SUM(tokeny_wyj),0) FROM odpowiedzi WHERE uruchomienie_id=?) WHERE id=?""", (run_id,) * 6)
            if consecutive_limit >= 2:
                # trwały limit dostawcy: odkładamy uruchomienie na koniec kolejki (błędy 429 zostaną powtórzone przy wznowieniu)
                db.execute(con, "UPDATE uruchomienia SET status='w_kolejce', priorytet=priorytet+1, uwagi=? WHERE id=?",
                           (f"odłożone po limicie dostawcy {time.strftime('%H:%M:%S')}", run_id))
                db.log(con, "kolejka", f"uruchomienie {run_id} odłożone: limit dostawcy ({res['blad'][:80]})")
                return "odlozone"
            time.sleep(float(par.get("opoznienie_s", 1.0)))
        cur = db.execute(con, "DELETE FROM odpowiedzi WHERE uruchomienie_id=? AND blad LIKE 'HTTP 429%'", (run_id,))
        if cur.rowcount:
            db.execute(con, "UPDATE uruchomienia SET status='w_kolejce', priorytet=priorytet+1, uwagi=? WHERE id=?", (f"{cur.rowcount} odpowiedzi z limitem 429 do powtórzenia", run_id))
            db.log(con, "kolejka", f"uruchomienie {run_id}: {cur.rowcount} haseł z limitem 429 wraca do kolejki")
            return "odlozone"
        db.execute(con, "UPDATE uruchomienia SET status='zakonczone', koniec_at=? WHERE id=?", (time.time(), run_id))
        db.log(con, "kolejka", f"zakończono uruchomienie {run_id}")
    except Exception as e:  # noqa: BLE001
        db.execute(con, "UPDATE uruchomienia SET status='blad', blad=? WHERE id=?", (traceback.format_exc()[-2000:], run_id))
        db.log(con, "kolejka", f"błąd uruchomienia {run_id}: {e}")


def stop(run_id: int):
    _stop_flags[run_id] = True


def worker_loop(con, only: int | None = None):
    deferred_streak = 0
    while True:
        if only:
            run_one(con, only); return
        nxt = db.one(con, "SELECT id FROM uruchomienia WHERE status='w_kolejce' ORDER BY priorytet, id LIMIT 1")
        if not nxt:
            return
        if run_one(con, nxt["id"]) == "odlozone":
            deferred_streak += 1
            pending = db.one(con, "SELECT COUNT(*) n FROM uruchomienia WHERE status='w_kolejce'")["n"]
            if deferred_streak >= pending:  # wszystkie czekające odłożone z rzędu: przerwa, by limity minęły
                wait = min(900, 60 * deferred_streak)
                db.log(con, "kolejka", f"wszystkie uruchomienia odłożone; przerwa {wait} s")
                time.sleep(wait)
        else:
            deferred_streak = 0


def start_background(only: int | None = None) -> bool:
    """Worker w wątku (dla pulpitu). Zwraca False, gdy już działa."""
    global _worker_thread
    if _worker_thread and _worker_thread.is_alive():
        return False
    con = db.init()
    _worker_thread = threading.Thread(target=worker_loop, args=(con, only), daemon=True)
    _worker_thread.start()
    return True


def worker_alive() -> bool:
    return bool(_worker_thread and _worker_thread.is_alive())


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("modele")
    a = sub.add_parser("dodaj"); a.add_argument("--model", required=True); a.add_argument("--wariant", required=True, choices=tasks.WARIANTY)
    a.add_argument("--limit", type=int); a.add_argument("--opoznienie", type=float, default=1.0); a.add_argument("--przyklady", action="store_true"); a.add_argument("--krzyzowka", action="append")
    s = sub.add_parser("start"); s.add_argument("--jedno", type=int)
    args = ap.parse_args(argv)
    con = db.init()
    if args.cmd == "modele":
        print(refresh_models(con))
    elif args.cmd == "dodaj":
        pod = {}
        if args.limit: pod["limit"] = args.limit
        if args.krzyzowka: pod["krzyzowki"] = args.krzyzowka
        print("uruchomienie", add_run(con, args.model, args.wariant, pod, {"opoznienie_s": args.opoznienie, "przyklady": args.przyklady}))
    else:
        worker_loop(con, args.jedno)


if __name__ == "__main__":
    main()
