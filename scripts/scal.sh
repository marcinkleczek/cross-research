#!/usr/bin/env bash
# Scala wyniki z innej kopii bazy (np. z workera na innej maszynie). Użycie: scripts/scal.sh inna_baza.sqlite
. "$(dirname "$0")/common.sh"
exec python -m bench.scal "$1"
