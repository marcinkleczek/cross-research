"""OCR treści opisów przez Claude Code z linii poleceń (`claude -p`), model do wyboru.

Domyślnie sonnet: w próbie na wycinkach z polskimi znakami haiku mylił diakrytykę (Grożna, skrzyzowanie,
niecheć, Bawarie), sonnet odczytał wszystkie poprawnie przy podobnym czasie (~6 s na wycinek).

Przebieg: lista ramek bez tekstu -> dla każdej wycinek PNG -> `claude -p` z narzędziem Read i `--json-schema`
-> tekst do bazy (ramki.tekst_ocr, ramki.tekst, hasla.opis). Wznawialne: pomija ramki już odczytane.

Uruchomienie:
  python -m bench.ocr lista                      # ile ramek czeka, per krzyżówka
  python -m bench.ocr uruchom [--model haiku] [--limit N] [--krzyzowka ID] [--opoznienie 1.0]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import time

from . import db

SCHEMA = {
    "type": "object",
    "properties": {
        "opisy": {"type": "array", "items": {"type": "string"}},
        "pewnosc": {"type": "number"},
        "uwagi": {"type": "string"},
    },
    "required": ["opisy", "pewnosc"],
}

PROMPT = (
    "Odczytaj plik {plik}. To wycinek jednej kratki z opisem z polskiej krzyżówki szwedzkiej.\n"
    "Zasady: przepisz tekst dokładnie, z polskimi znakami; połącz przeniesienia wyrazów (np. 'Mu-' i 'cha' to 'Mucha'); "
    "zachowaj cudzysłowy, wielokropki i skróty; jeśli kratka ma kilka opisów oddzielonych kreską, podaj każdy jako osobny element "
    "listy w kolejności od góry; nie dopisuj niczego od siebie; nie rozwiązuj hasła. "
    "W polu pewnosc podaj 0..1, w uwagi ewentualne wątpliwości (nieczytelny fragment)."
)


def pending(con, krzyzowka: str | None = None):
    sql = "SELECT r.*, k.id AS kid FROM ramki r JOIN krzyzowki k ON k.id=r.krzyzowka_id WHERE (r.tekst IS NULL OR r.tekst='') AND r.ocr_status != 'gotowe'"
    args = ()
    if krzyzowka:
        sql += " AND r.krzyzowka_id=?"; args = (krzyzowka,)
    return db.rows(con, sql + " ORDER BY r.krzyzowka_id, r.nr", args)


def run_claude(prompt: str, model: str, cwd: str, timeout: int = 300) -> dict:
    cmd = ["claude", "-p", prompt, "--model", model, "--output-format", "json", "--allowedTools", "Read",
           "--max-turns", "4", "--json-schema", json.dumps(SCHEMA, ensure_ascii=False)]
    t = time.time()
    pr = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd, timeout=timeout)
    ms = int((time.time() - t) * 1000)
    if pr.returncode != 0 and not pr.stdout.strip():
        return {"ok": False, "blad": pr.stderr[-2000:], "czas_ms": ms}
    try:
        d = json.loads(pr.stdout)
    except json.JSONDecodeError:
        return {"ok": False, "blad": "niepoprawny JSON z claude: " + pr.stdout[-500:], "czas_ms": ms}
    wynik = d.get("structured_output") or d.get("result")
    if isinstance(wynik, str):
        s = wynik.strip()
        if s.startswith("```"):
            s = s.strip("`")
            s = s[s.find("{"):s.rfind("}") + 1]
        try:
            wynik = json.loads(s)
        except json.JSONDecodeError:
            return {"ok": False, "blad": "brak struktury w odpowiedzi: " + s[:300], "czas_ms": ms, "surowe": d}
    return {"ok": True, "dane": wynik, "czas_ms": ms, "koszt": d.get("total_cost_usd"), "surowe": d}


def ocr_one(con, ramka: dict, model: str) -> dict:
    plik = ramka["plik_wycinka"]
    if not plik or not os.path.isfile(plik):
        db.execute(con, "UPDATE ramki SET ocr_status='brak_pliku' WHERE id=?", (ramka["id"],))
        return {"ok": False, "blad": "brak pliku wycinka"}
    res = run_claude(PROMPT.format(plik=plik), model, cwd=os.path.dirname(plik))
    if not res["ok"]:
        db.execute(con, "UPDATE ramki SET ocr_status='blad', ocr_surowe=?, ocr_model=?, ocr_at=? WHERE id=?",
                   (json.dumps(res, ensure_ascii=False, default=str)[:20000], model, time.time(), ramka["id"]))
        return res
    opisy = [o.strip() for o in res["dane"].get("opisy", []) if o and o.strip()]
    tekst = " | ".join(opisy)
    db.execute(con, "UPDATE ramki SET tekst_ocr=?, tekst=?, ocr_status='gotowe', ocr_model=?, ocr_surowe=?, ocr_at=? WHERE id=?",
               (tekst, tekst, model, json.dumps(res["dane"], ensure_ascii=False), time.time(), ramka["id"]))
    db.execute(con, "UPDATE hasla SET opis=? WHERE ramka_id=? AND (opis IS NULL OR opis='')", (tekst, ramka["id"]))
    return {"ok": True, "tekst": tekst, "czas_ms": res["czas_ms"], "koszt": res.get("koszt"), "pewnosc": res["dane"].get("pewnosc")}


def run(con, model: str = "sonnet", limit: int | None = None, krzyzowka: str | None = None, opoznienie: float = 0.5, stop=None):
    lst = pending(con, krzyzowka)
    if limit:
        lst = lst[:limit]
    done = 0; errs = 0; t0 = time.time()
    for r in lst:
        if stop and stop():
            break
        res = ocr_one(con, r, model)
        if res.get("ok"):
            done += 1
            print(f"{r['krzyzowka_id']}/O{r['nr']}: {res['tekst']}  ({res['czas_ms']} ms)")
        else:
            errs += 1
            print(f"{r['krzyzowka_id']}/O{r['nr']}: BŁĄD {res.get('blad', '')[:200]}")
        time.sleep(opoznienie)
    db.log(con, "ocr", f"model {model}: odczytano {done}, błędów {errs}, {time.time() - t0:.0f} s")
    return {"odczytano": done, "bledy": errs}


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("lista")
    u = sub.add_parser("uruchom")
    u.add_argument("--model", default="sonnet")
    u.add_argument("--limit", type=int)
    u.add_argument("--krzyzowka")
    u.add_argument("--opoznienie", type=float, default=0.5)
    a = ap.parse_args(argv)
    con = db.init()
    if a.cmd == "lista":
        lst = pending(con)
        per = {}
        for r in lst:
            per[r["krzyzowka_id"]] = per.get(r["krzyzowka_id"], 0) + 1
        for k, v in per.items():
            print(f"{k}: {v} ramek do odczytu")
        print(f"razem: {len(lst)}")
    else:
        print(run(con, a.model, a.limit, a.krzyzowka, a.opoznienie))


if __name__ == "__main__":
    main()
