#!/usr/bin/env bash
# batch_runpages: run the per-page pipeline (03 enhance -> 04 merge -> 05
# typeset -> 06 verify + preview) over all panel pages, in parallel.
#
# Usage:
#   ./batch_runpages.sh              # all panel pages from config
#   ./batch_runpages.sh 10 11 12     # explicit pages
#   JOBS=8 ./batch_runpages.sh       # control parallelism
#
# Prereq: geometry (02) + MCP data (08) exist for the pages.
# Logs: /tmp/rp_<page>.log (per page), summary on stdout.
set -u
cd "$(dirname "$0")" || exit 1
BASE="$(pwd)/pipeline"
if [ -x ".venv/bin/python" ]; then
  PY=".venv/bin/python"
elif [ -n "${PYTHON:-}" ]; then
  PY="$PYTHON"
else
  PY="python3"
fi
JOBS="${JOBS:-6}"

# read panel page range from config
RANGE="$("$PY" -c "
import sys; sys.path.insert(0, '$BASE')
from lib.common import PANEL_PAGES
print(f'{min(PANEL_PAGES)} {max(PANEL_PAGES)}')")"
if [ $# -gt 0 ]; then
  PAGES="$*"
else
  PAGES=$(seq ${RANGE%% *} ${RANGE##* })
fi

echo "== batch_runpages: $PAGES (JOBS=$JOBS) =="
run_one() {
  local p=$1
  if "$PY" "$BASE/run_page.py" --page "$p" >"/tmp/rp_${p}.log" 2>&1; then
    echo "PASS $p"
  else
    echo "FAIL $p $(grep -o '"pass": [a-z]*' "/tmp/rp_${p}.log" 2>/dev/null | tail -1) $(tail -1 "/tmp/rp_${p}.log" | cut -c1-80)"
  fi
}
export -f run_one
export PY BASE
echo "$PAGES" | tr ' ' '\n' | xargs -P "$JOBS" -I{} bash -c 'run_one {}'
echo "== done =="
