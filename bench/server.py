"""Pulpit benchmarku: stan danych, OCR, rejestr modeli, kolejka uruchomień, wyniki. Biblioteka standardowa.
Uruchomienie: python -m bench.server [--port 8010]"""
from __future__ import annotations

import argparse
import csv
import io
import json
import mimetypes
import os
import sys
import threading
import time
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, unquote

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bench import db, runner, tasks, metrics, ocr, importer  # noqa: E402

STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
KATEGORIE = ["jezyk", "geografia", "historia", "kultura", "nauka", "przyroda", "sport", "religia_mitologia", "zycie_codzienne", "skroty_jednostki", "krzyzowkowe", "inne"]
POLSKOSC = ["polska", "swiat", "neutralne"]
TYPY_OPISU = ["definicja", "synonim", "nazwa_wlasna", "dopelnienie", "skrot", "inne"]

_ocr_state = {"trwa": False, "stop": False, "wynik": None, "model": None}


def ocr_thread(model, krzyzowka, limit, opoznienie):
    con = db.init()
    _ocr_state.update(trwa=True, stop=False, model=model, wynik=None)
    try:
        _ocr_state["wynik"] = ocr.run(con, model, limit, krzyzowka, opoznienie, stop=lambda: _ocr_state["stop"])
    finally:
        _ocr_state["trwa"] = False


class H(BaseHTTPRequestHandler):
    server_version = "KrzyzowkiBench/0.1"

    def _send(self, code, body: bytes, ctype="application/json; charset=utf-8"):
        self.send_response(code); self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(body))); self.send_header("Cache-Control", "no-store"); self.end_headers(); self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False, default=str).encode())

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}") if n else {}

    def log_message(self, fmt, *a):
        pass

    def do_GET(self):  # noqa: N802
        con = self.server.con
        u = urlparse(self.path); p = unquote(u.path); q = {k: v[0] for k, v in parse_qs(u.query).items()}
        try:
            if p == "/favicon.ico":
                return self._send(204, b"", "image/x-icon")
            if p in ("/", "/index.html"):
                return self._send(200, open(os.path.join(STATIC, "dashboard.html"), "rb").read(), "text/html; charset=utf-8")
            if p == "/api/przeglad":
                return self._json(self.przeglad(con))
            if p == "/api/slowniki":
                return self._json({"kategorie": KATEGORIE, "polskosc": POLSKOSC, "typy_opisu": TYPY_OPISU, "warianty": tasks.WARIANTY})
            if p == "/api/krzyzowki":
                return self._json(db.rows(con, """SELECT k.*, (SELECT COUNT(*) FROM ramki r WHERE r.krzyzowka_id=k.id AND r.ocr_status='gotowe') ocr_gotowe,
                    (SELECT COUNT(*) FROM hasla h WHERE h.krzyzowka_id=k.id AND h.odpowiedz IS NOT NULL AND h.odpowiedz!='') z_odpowiedzia,
                    (SELECT COUNT(*) FROM hasla h WHERE h.krzyzowka_id=k.id AND h.gotowe=1) gotowe FROM krzyzowki k ORDER BY k.id"""))
            if p == "/api/hasla":
                sql = "SELECT h.*, r.plik_wycinka, r.ocr_status, r.tekst ramka_tekst FROM hasla h LEFT JOIN ramki r ON r.id=h.ramka_id WHERE 1=1"; args = []
                if q.get("krzyzowka"): sql += " AND h.krzyzowka_id=?"; args.append(q["krzyzowka"])
                if q.get("tylko") == "bez_odpowiedzi": sql += " AND (h.odpowiedz IS NULL OR h.odpowiedz='')"
                if q.get("tylko") == "gotowe": sql += " AND h.gotowe=1"
                return self._json(db.rows(con, sql + " ORDER BY h.krzyzowka_id, h.nr LIMIT ?", args + [int(q.get("limit", 2000))]))
            if p == "/api/ramki":
                return self._json(db.rows(con, "SELECT * FROM ramki WHERE krzyzowka_id=? ORDER BY nr", (q.get("krzyzowka"),)))
            if p.startswith("/wycinek/"):
                r = db.one(con, "SELECT plik_wycinka FROM ramki WHERE id=?", (int(p.split("/")[-1]),))
                if not r or not os.path.isfile(r["plik_wycinka"]): return self._send(404, b"brak")
                return self._send(200, open(r["plik_wycinka"], "rb").read(), mimetypes.guess_type(r["plik_wycinka"])[0] or "image/png")
            if p == "/api/ocr/stan":
                lst = ocr.pending(con); per = {}
                for r in lst: per[r["krzyzowka_id"]] = per.get(r["krzyzowka_id"], 0) + 1
                return self._json({**_ocr_state, "do_odczytu": len(lst), "per_krzyzowka": per,
                                   "statusy": db.rows(con, "SELECT ocr_status, COUNT(*) n FROM ramki GROUP BY ocr_status")})
            if p == "/api/modele":
                return self._json(db.rows(con, """SELECT m.*, (SELECT COUNT(*) FROM uruchomienia u WHERE u.model_id=m.id) uruchomien FROM modele m ORDER BY dostawca, nazwa"""))
            if p == "/api/uruchomienia":
                return self._json(db.rows(con, "SELECT u.*, m.dostawca, m.nazwa model_nazwa FROM uruchomienia u LEFT JOIN modele m ON m.id=u.model_id ORDER BY CASE status WHEN 'trwa' THEN 0 WHEN 'w_kolejce' THEN 1 ELSE 2 END, priorytet, id") + [])
            if p == "/api/worker":
                return self._json({"dziala": runner.worker_alive()})
            if p.startswith("/api/wyniki/"):
                rid = p.split("/")[3]
                csvmode = rid.endswith(".csv"); rid = int(rid.replace(".csv", ""))
                rws = db.rows(con, """SELECT o.*, h.opis, h.odpowiedz klucz, h.dlugosc, h.kategoria, h.polskosc, h.typ_opisu, h.krzyzowka_id FROM odpowiedzi o JOIN hasla h ON h.id=o.haslo_id WHERE o.uruchomienie_id=? ORDER BY o.haslo_id""", (rid,))
                if csvmode:
                    buf = io.StringIO(); w = csv.writer(buf)
                    cols = ["haslo_id", "opis", "klucz", "odpowiedz", "kandydaci", "pewnosc", "poprawna", "poprawna_luzno", "w_top5", "dlugosc_ok", "wzorzec_ok", "format_ok", "czas_ms", "tokeny_wej", "tokeny_wyj", "koszt_usd", "kategoria", "polskosc", "blad"]
                    w.writerow(cols); [w.writerow([r.get(c) for c in cols]) for r in rws]
                    return self._send(200, buf.getvalue().encode("utf-8-sig"), "text/csv; charset=utf-8")
                run = db.one(con, "SELECT u.*, m.dostawca, m.nazwa model_nazwa FROM uruchomienia u LEFT JOIN modele m ON m.id=u.model_id WHERE u.id=?", (rid,))
                for r in rws: r.pop("surowe", None); r.pop("prompt", None)
                return self._json({"uruchomienie": run, "metryki": metrics.summarize(rws), "odpowiedzi": rws if q.get("pelne") else rws[:500]})
            if p == "/api/porownanie":
                out = []
                for run in db.rows(con, "SELECT u.*, m.dostawca, m.nazwa model_nazwa FROM uruchomienia u LEFT JOIN modele m ON m.id=u.model_id WHERE u.zrobione>0 ORDER BY u.id"):
                    rws = db.rows(con, "SELECT o.*, h.kategoria, h.polskosc, h.dlugosc, h.typ_opisu, h.krzyzowka_id FROM odpowiedzi o JOIN hasla h ON h.id=o.haslo_id WHERE o.uruchomienie_id=?", (run["id"],))
                    m = metrics.summarize(rws); m.pop("wg", None)
                    out.append({"uruchomienie": {k: run[k] for k in ("id", "nazwa", "model_id", "wariant", "status", "liczba_hasel", "zrobione", "bledy", "start_at", "koniec_at")},
                                "metryki": m, "wg_polskosc": {k: v["trafnosc"] for k, v in metrics.summarize(rws)["wg"]["polskosc"].items()}})
                return self._json(out)
            if p == "/api/zdarzenia":
                return self._json(db.rows(con, "SELECT * FROM zdarzenia ORDER BY id DESC LIMIT 100"))
            if p.startswith("/static/"):
                name = os.path.basename(p)
                for d in (STATIC, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app", "static")):
                    f = os.path.join(d, name)
                    if os.path.isfile(f):
                        return self._send(200, open(f, "rb").read(), mimetypes.guess_type(f)[0] or "application/octet-stream")
                return self._send(404, b"brak")
            return self._send(404, b"nie znaleziono")
        except Exception as e:  # noqa: BLE001
            import traceback; return self._json({"blad": str(e), "slad": traceback.format_exc()[-1500:]}, 500)

    def do_POST(self):  # noqa: N802
        con = self.server.con
        p = unquote(urlparse(self.path).path); b = self._body()
        try:
            if p == "/api/import":
                kat = b.get("katalog") or "output"; res = []
                for d in sorted(os.listdir(kat)):
                    if os.path.isfile(os.path.join(kat, d, "wynik.json")):
                        res.append(importer.import_result(con, os.path.join(kat, d), b.get("zrodlo")))
                db.log(con, "import", f"pulpit: {len(res)} krzyżówek z {kat}")
                return self._json({"zaimportowano": res})
            if p.startswith("/api/haslo/"):
                hid = p[len("/api/haslo/"):]
                fields = {k: b[k] for k in ("opis", "odpowiedz", "kategoria", "polskosc", "typ_opisu", "gotowe", "uwagi", "zrodlo_odpowiedzi") if k in b}
                if "odpowiedz" in fields and fields["odpowiedz"]:
                    fields["odpowiedz"] = tasks.normalize(fields["odpowiedz"])
                    fields.setdefault("zrodlo_odpowiedzi", "reczna")
                    h = db.one(con, "SELECT dlugosc FROM hasla WHERE id=?", (hid,))
                    if h and len(fields["odpowiedz"]) != h["dlugosc"]:
                        return self._json({"blad": f"odpowiedź ma {len(fields['odpowiedz'])} liter, hasło w siatce ma {h['dlugosc']}"}, 400)
                if fields:
                    db.execute(con, "UPDATE hasla SET " + ", ".join(f"{k}=?" for k in fields) + " WHERE id=?", list(fields.values()) + [hid])
                return self._json(db.one(con, "SELECT * FROM hasla WHERE id=?", (hid,)))
            if p == "/api/hasla/gotowe":
                # oznacz jako gotowe wszystkie z opisem i odpowiedzią (opcjonalnie w jednej krzyżówce)
                sql = "UPDATE hasla SET gotowe=? WHERE opis IS NOT NULL AND opis!='' AND odpowiedz IS NOT NULL AND odpowiedz!=''"; args = [int(b.get("gotowe", 1))]
                if b.get("krzyzowka"): sql += " AND krzyzowka_id=?"; args.append(b["krzyzowka"])
                cur = db.execute(con, sql, args); return self._json({"zmieniono": cur.rowcount})
            if p.startswith("/api/ramka/"):
                rid = int(p.split("/")[-1])
                db.execute(con, "UPDATE ramki SET tekst=?, zweryfikowano=?, ocr_status=CASE WHEN ?!='' THEN 'gotowe' ELSE ocr_status END WHERE id=?", (b.get("tekst", ""), int(b.get("zweryfikowano", 1)), b.get("tekst", ""), rid))
                db.execute(con, "UPDATE hasla SET opis=? WHERE ramka_id=?", (b.get("tekst", ""), rid))
                return self._json({"ok": True})
            if p == "/api/ocr/start":
                if _ocr_state["trwa"]: return self._json({"blad": "OCR już trwa"}, 409)
                threading.Thread(target=ocr_thread, args=(b.get("model", "sonnet"), b.get("krzyzowka") or None, int(b["limit"]) if b.get("limit") else None, float(b.get("opoznienie", 0.5))), daemon=True).start()
                return self._json({"ok": True})
            if p == "/api/ocr/stop":
                _ocr_state["stop"] = True; return self._json({"ok": True})
            if p == "/api/modele/odswiez":
                return self._json(runner.refresh_models(con))
            if p == "/api/modele/dodaj":
                mid = f"{b['dostawca']}:{b['nazwa']}"
                db.execute(con, "INSERT OR REPLACE INTO modele(id, dostawca, nazwa, etykieta, parametry_mld, darmowy, aktywny, uwagi, dodano_at) VALUES (?,?,?,?,?,?,1,?,?)",
                           (mid, b["dostawca"], b["nazwa"], b.get("etykieta") or b["nazwa"], b.get("parametry_mld"), int(b.get("darmowy", 0)), b.get("uwagi"), time.time()))
                return self._json({"id": mid})
            if p.startswith("/api/model/"):
                mid = p[len("/api/model/"):]
                fields = {k: b[k] for k in ("aktywny", "parametry_mld", "etykieta", "uwagi", "darmowy") if k in b}
                if fields: db.execute(con, "UPDATE modele SET " + ", ".join(f"{k}=?" for k in fields) + " WHERE id=?", list(fields.values()) + [mid])
                return self._json({"ok": True})
            if p == "/api/uruchomienia":
                ids = []
                for mid in (b.get("modele") or [b.get("model_id")]):
                    for w in (b.get("warianty") or [b.get("wariant")]):
                        ids.append(runner.add_run(con, mid, w, b.get("podzbior") or {}, b.get("parametry") or {}, priorytet=int(b.get("priorytet", 100))))
                return self._json({"ids": ids})
            if p.startswith("/api/uruchomienie/"):
                _, _, _, rid, akcja = p.split("/")[:5]; rid = int(rid)
                if akcja == "start":
                    if not runner.start_background(rid): return self._json({"blad": "worker już działa; uruchomienie wykona się w kolejce"}, 409)
                elif akcja == "stop": runner.stop(rid)
                elif akcja == "wznow": db.execute(con, "UPDATE uruchomienia SET status='w_kolejce' WHERE id=?", (rid,))
                elif akcja == "usun": db.execute(con, "DELETE FROM uruchomienia WHERE id=?", (rid,))
                elif akcja == "priorytet": db.execute(con, "UPDATE uruchomienia SET priorytet=? WHERE id=?", (int(b.get("priorytet", 100)), rid))
                elif akcja == "powtorz_bledy": db.execute(con, "DELETE FROM odpowiedzi WHERE uruchomienie_id=? AND blad IS NOT NULL", (rid,)); db.execute(con, "UPDATE uruchomienia SET status='w_kolejce' WHERE id=?", (rid,))
                return self._json({"ok": True})
            if p == "/api/worker/start":
                return self._json({"uruchomiono": runner.start_background()})
            return self._send(404, b"nie znaleziono")
        except Exception as e:  # noqa: BLE001
            import traceback; return self._json({"blad": str(e), "slad": traceback.format_exc()[-1500:]}, 500)

    def przeglad(self, con):
        c = lambda sql, a=(): db.one(con, sql, a)["n"]  # noqa: E731
        d = {
            "zdjecia_w_input": len([f for f in os.listdir("input") if f.lower().endswith((".jpg", ".jpeg", ".png"))]) if os.path.isdir("input") else None,
            "krzyzowki": c("SELECT COUNT(*) n FROM krzyzowki"),
            "ramki": c("SELECT COUNT(*) n FROM ramki"), "ramki_ocr": c("SELECT COUNT(*) n FROM ramki WHERE ocr_status='gotowe'"),
            "ramki_zweryfikowane": c("SELECT COUNT(*) n FROM ramki WHERE zweryfikowano=1"),
            "hasla": c("SELECT COUNT(*) n FROM hasla"), "hasla_z_opisem": c("SELECT COUNT(*) n FROM hasla WHERE opis IS NOT NULL AND opis!=''"),
            "hasla_z_odpowiedzia": c("SELECT COUNT(*) n FROM hasla WHERE odpowiedz IS NOT NULL AND odpowiedz!=''"),
            "hasla_z_kategoria": c("SELECT COUNT(*) n FROM hasla WHERE kategoria IS NOT NULL"), "hasla_gotowe": c("SELECT COUNT(*) n FROM hasla WHERE gotowe=1"),
            "modele": c("SELECT COUNT(*) n FROM modele WHERE aktywny=1"), "modele_darmowe": c("SELECT COUNT(*) n FROM modele WHERE aktywny=1 AND darmowy=1"),
            "uruchomienia": {r["status"]: r["n"] for r in db.rows(con, "SELECT status, COUNT(*) n FROM uruchomienia GROUP BY status")},
            "odpowiedzi": c("SELECT COUNT(*) n FROM odpowiedzi"), "koszt_usd": db.one(con, "SELECT COALESCE(SUM(koszt_usd),0) n FROM odpowiedzi")["n"],
            "worker": runner.worker_alive(), "ocr": _ocr_state,
            "kategorie": db.rows(con, "SELECT COALESCE(kategoria,'–') kategoria, COUNT(*) n FROM hasla WHERE gotowe=1 GROUP BY kategoria ORDER BY n DESC"),
            "polskosc": db.rows(con, "SELECT COALESCE(polskosc,'–') polskosc, COUNT(*) n FROM hasla WHERE gotowe=1 GROUP BY polskosc"),
            "zdarzenia": db.rows(con, "SELECT * FROM zdarzenia ORDER BY id DESC LIMIT 12"),
            "klucze": {"OPENROUTER_API_KEY": bool(os.environ.get("OPENROUTER_API_KEY")), "OLLAMA_API_KEY": bool(os.environ.get("OLLAMA_API_KEY")), "OLLAMA_HOST": os.environ.get("OLLAMA_HOST") or "http://127.0.0.1:11434"},
        }
        return d


def main(argv=None):
    ap = argparse.ArgumentParser(); ap.add_argument("--port", type=int, default=8010); ap.add_argument("--host", default="0.0.0.0")
    a = ap.parse_args(argv)
    srv = ThreadingHTTPServer((a.host, a.port), H); srv.con = db.init()
    print(f"Pulpit: http://{a.host}:{a.port}/  baza: {db.DB_PATH}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
