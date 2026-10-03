#!/usr/bin/env bash
# Stan: procesy, kolejka, braki (z API pulpitu). Użycie: scripts/stan.sh [port_pulpitu]
. "$(dirname "$0")/common.sh"
PORT=${1:-${PULPIT_PORT:-8010}}
for f in logi/*.pid; do [ -f "$f" ] && { pid=$(cat "$f"); if kill -0 "$pid" 2>/dev/null; then echo "$(basename "$f" .pid): działa (PID $pid)"; else echo "$(basename "$f" .pid): nie działa"; fi; }; done
curl -sf "http://127.0.0.1:$PORT/api/przeglad" >/dev/null || { echo "Pulpit nie odpowiada na porcie $PORT."; exit 1; }
python - "$PORT" <<'PY'
import json, sys, urllib.request
port = sys.argv[1]
get = lambda p: json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}{p}"))
d = get("/api/przeglad"); print("Kolejka:", d["uruchomienia"], "| worker:", "działa" if d["worker"] else "nie działa", "| OCR:", "trwa" if d["ocr"]["trwa"] else "nie trwa")
for b in get("/api/braki"): print(f"  {b['ile']:>5}  {b['etap']}")
PY
