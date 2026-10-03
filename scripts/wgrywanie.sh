#!/usr/bin/env bash
# Serwer wgrywania zdjęć z telefonu (domyślnie port 8000). Użycie: scripts/wgrywanie.sh [port]
. "$(dirname "$0")/common.sh"
IP=$(hostname -I 2>/dev/null | awk '{print $1}')
echo "Na telefonie w tej samej sieci: http://${IP:-ADRES_IP}:${1:-8000}/"
exec python -m app.server --port "${1:-8000}" --katalog input --wyniki output
