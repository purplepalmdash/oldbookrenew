#!/usr/bin/env bash
# Install THIS skill (scan-retypeset) into the global Pi agent skills dir.
# (Repo-level multi-skill installer: ../install.sh from the oldbookrenew root.)
#
# Safety: an existing install that DIFFERS from this copy (local edits?) is
# not silently rm -rf'd: identical -> replaced quietly; differing -> needs
# --force (or "y" on an interactive TTY); non-interactive skip exits 2.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
DEST="${PI_AGENT_HOME:-$HOME/.pi/agent}/skills/scan-retypeset"
FORCE=0
while [ $# -gt 0 ]; do
  case "$1" in
    --force) FORCE=1; shift ;;
    *) echo "unknown arg: $1 (expected [--force])"; exit 1 ;;
  esac
done
if [ "$HERE" = "$(cd "$(dirname "$DEST")" 2>/dev/null && pwd)/$(basename "$HERE")" ]; then
  echo "already in place: $DEST"; exit 0
fi
mkdir -p "$(dirname "$DEST")"
if [ -e "$DEST" ]; then
  if diff -rq --exclude=__pycache__ "$HERE" "$DEST" >/dev/null 2>&1; then
    echo "replacing identical $DEST"
    rm -rf "$DEST"
  elif [ "$FORCE" -eq 1 ]; then
    echo "replacing differing $DEST (--force)"
    rm -rf "$DEST"
  elif [ -t 0 ]; then
    printf "existing $DEST differs from this copy — overwrite? [y/N] "
    read -r ans
    [ "$ans" = "y" ] || { echo "not overwriting (rerun with --force)"; exit 2; }
    rm -rf "$DEST"
  else
    echo "not overwriting $DEST: differs from this copy (use --force)"
    exit 2
  fi
fi
cp -r "$HERE" "$DEST"
find "$DEST" -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
echo "installed: $DEST"
