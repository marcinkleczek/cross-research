#!/usr/bin/env bash
# Wspólne ustawienia: katalog projektu, środowisko wirtualne, zmienne z .env.
set -euo pipefail
PROJEKT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJEKT"
if [ -d .venv ]; then
  # shellcheck disable=SC1091
  . .venv/bin/activate
fi
if [ -f .env ]; then
  set -a; . ./.env; set +a
fi
export BENCH_DB="${BENCH_DB:-$PROJEKT/bench.sqlite}"
mkdir -p logi
