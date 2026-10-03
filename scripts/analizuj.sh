#!/usr/bin/env bash
# Ekstrakcja struktury ze zdjęć i import do bazy. Użycie: scripts/analizuj.sh [plik_lub_katalog ...]  (domyślnie input/)
. "$(dirname "$0")/common.sh"
python -m krzyzowka.cli "${@:-input}" -o output
python -m bench.importer output
