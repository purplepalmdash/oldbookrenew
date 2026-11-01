#!/usr/bin/env bash
# oldbookrenew: install skill(s) into an agent skills directory.
#
# Usage:
#   ./install.sh                        # install ALL skills into ~/.pi/agent/skills
#   ./install.sh scan-retypeset         # install one skill only
#   ./install.sh --dest ~/.claude/skills paddleocr-digitize guji-restore
#   ./install.sh --force                # overwrite existing installs even if they differ
#
# Safety: an existing skill directory that DIFFERS from the repo copy
# (e.g. the user made local edits to an installed skill) is not silently
# rm -rf'd: identical content -> replaced quietly; differing content ->
# needs --force (or "y" on an interactive TTY). Non-interactive runs
# skip differing installs and exit 2 so the caller notices.
#
# Skills are plain text + scripts (no build step); a symlink works too:
#   ln -s $(pwd)/skills/scan-retypeset ~/.pi/agent/skills/scan-retypeset
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)/skills"
DEST="${PI_AGENT_HOME:-$HOME/.pi/agent}/skills"
FORCE=0
SKILLS=""
while [ $# -gt 0 ]; do
  case "$1" in
    --dest) DEST="$2"; shift 2 ;;
    --force) FORCE=1; shift ;;
    -h|--help) sed -n '2,17p' "$0"; exit 0 ;;
    *) SKILLS="$SKILLS $1"; shift ;;
  esac
done
SKILLS="${SKILLS:-$(ls "$SRC")}"
skipped=0
for s in $SKILLS; do
  [ -f "$SRC/$s/SKILL.md" ] || { echo "not a skill (no SKILL.md): $s"; exit 1; }
  mkdir -p "$DEST"
  src="$(cd "$SRC/$s" && pwd)"
  if [ -e "$DEST/$s" ]; then
    if [ "$src" = "$(cd "$DEST/$s" && pwd)" ]; then
      echo "already in place: $DEST/$s"; continue
    fi
    if diff -rq --exclude=__pycache__ "$src" "$DEST/$s" >/dev/null 2>&1; then
      echo "replacing identical $DEST/$s"
      rm -rf "$DEST/$s"
    elif [ "$FORCE" -eq 1 ]; then
      echo "replacing differing $DEST/$s (--force)"
      rm -rf "$DEST/$s"
    elif [ -t 0 ]; then
      printf "existing $DEST/$s differs from the repo copy — overwrite? [y/N] "
      read -r ans
      if [ "$ans" != "y" ]; then
        echo "skipping $s (rerun with --force to overwrite)"
        skipped=$((skipped+1)); continue
      fi
      rm -rf "$DEST/$s"
    else
      echo "skipping $s: $DEST/$s differs from the repo copy (use --force to overwrite)"
      skipped=$((skipped+1)); continue
    fi
  fi
  cp -r "$src" "$DEST"
  find "$DEST/$s" -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
  echo "installed: $DEST/$s"
done
echo "done: $DEST"
if [ "$skipped" -ne 0 ]; then
  echo "skipped $skipped skill(s) with differing local copies — rerun with --force to overwrite"
  exit 2
fi
