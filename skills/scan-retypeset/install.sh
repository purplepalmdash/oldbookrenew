#!/usr/bin/env bash
# Install THIS skill (scan-retypeset) into the global Pi agent skills dir.
# (Repo-level multi-skill installer: ../install.sh from the oldbookrenew root.)
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
DEST="${PI_AGENT_HOME:-$HOME/.pi/agent}/skills/scan-retypeset"
mkdir -p "$(dirname "$DEST")"
if [ -e "$DEST" ]; then
  echo "removing existing $DEST"
  rm -rf "$DEST"
fi
cp -r "$HERE" "$DEST"
find "$DEST" -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
echo "installed: $DEST"
