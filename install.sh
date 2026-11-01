#!/usr/bin/env bash
# oldbookrenew: install skill(s) into an agent skills directory.
#
# Usage:
#   ./install.sh                        # install ALL skills into ~/.pi/agent/skills
#   ./install.sh scan-retypeset         # install one skill only
#   ./install.sh --dest ~/.claude/skills paddleocr-digitize guji-restore
#
# Skills are plain text + scripts (no build step); a symlink works too:
#   ln -s $(pwd)/skills/scan-retypeset ~/.pi/agent/skills/scan-retypeset
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)/skills"
DEST="${PI_AGENT_HOME:-$HOME/.pi/agent}/skills"
while [ $# -gt 0 ]; do
  case "$1" in
    --dest) DEST="$2"; shift 2 ;;
    *) SKILLS="$SKILLS $1"; shift ;;
  esac
done
SKILLS="${SKILLS:-$(ls "$SRC")}"
for s in $SKILLS; do
  [ -f "$SRC/$s/SKILL.md" ] || { echo "not a skill (no SKILL.md): $s"; exit 1; }
  mkdir -p "$DEST"
  if [ -e "$DEST/$s" ]; then
    echo "removing existing $DEST/$s"
    rm -rf "$DEST/$s"
  fi
  cp -r "$SRC/$s" "$DEST/$s"
  find "$DEST/$s" -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
  echo "installed: $DEST/$s"
done
echo "done: $DEST"
