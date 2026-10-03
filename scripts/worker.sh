#!/usr/bin/env bash
# Worker kolejki uruchomień (wykonuje kolejkę do końca, potem kończy).
# Użycie: scripts/worker.sh                 wszystkie uruchomienia
#         scripts/worker.sh ollama          tylko dostawca (ollama | openrouter | claude_cli); po jednym na dostawcę = równolegle
#         scripts/worker.sh 42              jedno uruchomienie
. "$(dirname "$0")/common.sh"
case "${1:-}" in
  "") exec python -m bench.runner start ;;
  ollama|openrouter|claude_cli) exec python -m bench.runner start --dostawca "$1" ;;
  *) exec python -m bench.runner start --jedno "$1" ;;
esac
