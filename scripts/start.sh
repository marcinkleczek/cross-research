#!/usr/bin/env bash
# Uruchamia w tle pulpit (8010), serwer wgrywania (8000) i workera; dzienniki w logi/, PID-y w logi/*.pid.
. "$(dirname "$0")/common.sh"
start() { # nazwa, polecenie...
  local nazwa=$1; shift
  if [ -f "logi/$nazwa.pid" ] && kill -0 "$(cat "logi/$nazwa.pid")" 2>/dev/null; then echo "$nazwa już działa (PID $(cat "logi/$nazwa.pid"))"; return; fi
  nohup "$@" >> "logi/$nazwa.log" 2>&1 &
  echo $! > "logi/$nazwa.pid"; echo "$nazwa uruchomiony (PID $!), dziennik logi/$nazwa.log"
}
start pulpit python -m bench.server --port "${PULPIT_PORT:-8010}"
start wgrywanie python -m app.server --port "${WGRYWANIE_PORT:-8000}" --katalog input --wyniki output
if [ "${BEZ_WORKERA:-0}" != "1" ]; then start worker python -m bench.runner start; fi
IP=$(hostname -I 2>/dev/null | awk '{print $1}')
echo "Pulpit:    http://localhost:${PULPIT_PORT:-8010}/"
echo "Wgrywanie: http://${IP:-localhost}:${WGRYWANIE_PORT:-8000}/  (z telefonu w tej samej sieci)"
