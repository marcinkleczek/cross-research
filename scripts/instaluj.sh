#!/usr/bin/env bash
# Jednorazowo: środowisko wirtualne, zależności, szablon .env, ekstrakcja ze zdjęć i import do bazy.
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -m venv .venv
. .venv/bin/activate
pip install -q --upgrade pip
pip install -q -r requirements.txt
[ -f .env ] || { cp .env.example .env; echo "Utworzono .env – wpisz klucze OPENROUTER_API_KEY i OLLAMA_API_KEY."; }
. scripts/common.sh
echo "Ekstrakcja ze zdjęć w input/ ..."
python -m krzyzowka.cli input -o output
echo "Import do bazy $BENCH_DB ..."
python -m bench.importer output
echo "Gotowe. Uruchom: scripts/start.sh"
