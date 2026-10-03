#!/usr/bin/env bash
# Zatrzymuje procesy uruchomione przez scripts/start.sh.
. "$(dirname "$0")/common.sh"
for f in logi/*.pid; do
  [ -f "$f" ] || continue
  pid=$(cat "$f"); nazwa=$(basename "$f" .pid)
  if kill -0 "$pid" 2>/dev/null; then kill "$pid" && echo "$nazwa zatrzymany (PID $pid)"; else echo "$nazwa nie działał"; fi
  rm -f "$f"
done
