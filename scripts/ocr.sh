#!/usr/bin/env bash
# OCR ramek opisów przez Claude Code. Użycie: scripts/ocr.sh [lista | uruchom [--model sonnet] [--krzyzowka ID] [--limit N]]
. "$(dirname "$0")/common.sh"
command -v claude >/dev/null || { echo "Brak polecenia claude (Claude Code CLI)."; exit 1; }
exec python -m bench.ocr "${@:-lista}"
