#!/usr/bin/env bash
# Worker kolejki uruchomień (wykonuje kolejkę do końca, potem kończy). Użycie: scripts/worker.sh [id_uruchomienia]
. "$(dirname "$0")/common.sh"
if [ -n "${1:-}" ]; then exec python -m bench.runner start --jedno "$1"; fi
exec python -m bench.runner start
