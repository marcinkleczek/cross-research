"""Serwer HTTP (biblioteka standardowa) do wgrywania zdjęć krzyżówek z aparatu telefonu.

Uruchomienie:  python -m app.server [--port 8000] [--katalog input] [--wyniki output]
Na telefonie (w tej samej sieci): http://ADRES_IP_KOMPUTERA:8000/

Zdjęcia trafiają do katalogu wejściowego pod nazwą ZNACZNIK_CZASU_oryginalna_nazwa.
Po wgraniu można uruchomić analizę (moduł `krzyzowka`), a wynik (JSON, nakładka, wycinki)
zapisuje się w katalogu wyników i jest dostępny przez przeglądarkę.
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import sys
import threading
import time
from datetime import datetime
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import unquote, urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".heic", ".webp"}
MAX_UPLOAD = 60 * 1024 * 1024

CONFIG = {"input": "input", "output": "output"}
_analysis_lock = threading.Lock()
_analysis_state: dict[str, dict] = {}


def safe_name(name: str) -> str:
    base = os.path.basename(name).strip().replace(" ", "_")
    base = re.sub(r"[^A-Za-z0-9._-]", "", base) or "zdjecie.jpg"
    return base


def parse_multipart(body: bytes, content_type: str) -> list[tuple[str, str | None, bytes]]:
    """Minimalny parser multipart/form-data: lista (nazwa_pola, nazwa_pliku, dane)."""
    m = re.search(r'boundary="?([^";]+)"?', content_type)
    if not m:
        raise ValueError("brak boundary")
    boundary = b"--" + m.group(1).encode()
    parts = body.split(boundary)
    out = []
    for part in parts[1:]:
        if part.strip() in (b"", b"--"):
            continue
        if part.startswith(b"\r\n"):
            part = part[2:]
        head, sep, data = part.partition(b"\r\n\r\n")
        if not sep:
            continue
        if data.endswith(b"\r\n"):
            data = data[:-2]
        headers = head.decode("utf-8", "replace")
        name = re.search(r'name="([^"]*)"', headers)
        fname = re.search(r'filename="([^"]*)"', headers)
        out.append((name.group(1) if name else "", fname.group(1) if fname else None, data))
    return out


def list_images() -> list[dict]:
    d = CONFIG["input"]
    items = []
    if os.path.isdir(d):
        for f in sorted(os.listdir(d), reverse=True):
            if os.path.splitext(f)[1].lower() in ALLOWED_EXT:
                stem = os.path.splitext(f)[0]
                res = os.path.join(CONFIG["output"], stem, "wynik.json")
                items.append({"nazwa": f, "rozmiar": os.path.getsize(os.path.join(d, f)),
                              "czas": datetime.fromtimestamp(os.path.getmtime(os.path.join(d, f))).strftime("%Y-%m-%d %H:%M:%S"),
                              "wynik": os.path.exists(res), "stan": _analysis_state.get(stem, {}).get("stan")})
    return items


def run_analysis(fname: str) -> None:
    stem = os.path.splitext(fname)[0]
    with _analysis_lock:
        _analysis_state[stem] = {"stan": "w toku", "start": time.time()}
        try:
            from krzyzowka.extract import analyze
            analyze(os.path.join(CONFIG["input"], fname), os.path.join(CONFIG["output"], stem))
            _analysis_state[stem] = {"stan": "gotowe", "czas": round(time.time() - _analysis_state[stem]["start"], 1)}
        except Exception as e:  # noqa: BLE001
            _analysis_state[stem] = {"stan": "błąd", "blad": str(e)}


class Handler(BaseHTTPRequestHandler):
    server_version = "Krzyzowki/0.1"

    def _send(self, code: int, body: bytes, ctype: str = "text/plain; charset=utf-8", extra: dict | None = None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def _file(self, path: str):
        if not os.path.isfile(path):
            return self._send(404, b"nie ma takiego pliku")
        ctype = {".jpz": "application/xml; charset=utf-8", ".ipuz": "application/json; charset=utf-8"}.get(
            os.path.splitext(path)[1].lower()) or mimetypes.guess_type(path)[0] or "application/octet-stream"
        with open(path, "rb") as f:
            data = f.read()
        self._send(200, data, ctype)

    def do_GET(self):  # noqa: N802
        u = urlparse(self.path)
        path = unquote(u.path)
        if path in ("/", "/index.html"):
            return self._file(os.path.join(STATIC_DIR, "index.html"))
        if path == "/api/lista":
            return self._json(list_images())
        if path.startswith("/api/stan/"):
            stem = safe_name(path[len("/api/stan/"):])
            return self._json(_analysis_state.get(os.path.splitext(stem)[0], {"stan": None}))
        if path.startswith("/zdjecia/"):
            return self._file(os.path.join(CONFIG["input"], safe_name(path[len("/zdjecia/"):])))
        if path.startswith("/wyniki/"):
            rel = os.path.normpath(path[len("/wyniki/"):]).replace("\\", "/")
            if rel.startswith("..") or os.path.isabs(rel):
                return self._send(403, b"zabronione")
            return self._file(os.path.join(CONFIG["output"], rel))
        if path.startswith("/static/"):
            return self._file(os.path.join(STATIC_DIR, safe_name(path[len("/static/"):])))
        return self._send(404, b"nie znaleziono")

    def do_POST(self):  # noqa: N802
        u = urlparse(self.path)
        path = unquote(u.path)
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_UPLOAD:
            return self._json({"blad": "plik za duży"}, 413)
        body = self.rfile.read(length) if length else b""
        if path == "/api/wgraj":
            try:
                parts = parse_multipart(body, self.headers.get("Content-Type", ""))
            except ValueError as e:
                return self._json({"blad": str(e)}, 400)
            saved = []
            os.makedirs(CONFIG["input"], exist_ok=True)
            for field, fname, data in parts:
                if not fname or not data:
                    continue
                ext = os.path.splitext(fname)[1].lower()
                if ext not in ALLOWED_EXT:
                    return self._json({"blad": f"niedozwolone rozszerzenie {ext}"}, 400)
                stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                name = f"{stamp}_{safe_name(fname)}"
                target = os.path.join(CONFIG["input"], name)
                i = 1
                while os.path.exists(target):
                    name = f"{stamp}_{i}_{safe_name(fname)}"; target = os.path.join(CONFIG["input"], name); i += 1
                with open(target, "wb") as f:
                    f.write(data)
                saved.append(name)
            if not saved:
                return self._json({"blad": "brak pliku w żądaniu"}, 400)
            return self._json({"zapisano": saved})
        if path.startswith("/api/analizuj/"):
            fname = safe_name(path[len("/api/analizuj/"):])
            if not os.path.isfile(os.path.join(CONFIG["input"], fname)):
                return self._json({"blad": "nie ma takiego zdjęcia"}, 404)
            stem = os.path.splitext(fname)[0]
            if _analysis_state.get(stem, {}).get("stan") == "w toku":
                return self._json({"stan": "w toku"})
            _analysis_state[stem] = {"stan": "w toku", "start": time.time()}
            threading.Thread(target=run_analysis, args=(fname,), daemon=True).start()
            return self._json({"stan": "uruchomiono"})
        return self._send(404, b"nie znaleziono")

    def log_message(self, fmt, *args):  # ciszej
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))


def main(argv=None):
    ap = argparse.ArgumentParser(description="Serwer wgrywania zdjęć krzyżówek z telefonu.")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--katalog", default="input", help="katalog na zdjęcia")
    ap.add_argument("--wyniki", default="output", help="katalog na wyniki analizy")
    args = ap.parse_args(argv)
    CONFIG["input"] = args.katalog
    CONFIG["output"] = args.wyniki
    os.makedirs(args.katalog, exist_ok=True)
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Nasłuch na http://{args.host}:{args.port}/  (zdjęcia -> {os.path.abspath(args.katalog)})")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
