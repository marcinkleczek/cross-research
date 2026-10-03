#!/usr/bin/env bash
# Pulpit benchmarku (domyślnie port 8010). Użycie: scripts/pulpit.sh [port]
. "$(dirname "$0")/common.sh"
exec python -m bench.server --port "${1:-8010}"
