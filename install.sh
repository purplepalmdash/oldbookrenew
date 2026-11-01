#!/usr/bin/env bash
# Install the scan-retypeset skill into the global Pi agent skills dir.
# The repository root IS the skill (SKILL.md at top level); everything
# except .git / local artifacts is installed.
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
DEST="${PI_AGENT_HOME:-$HOME/.pi/agent}/skills/scan-retypeset"
mkdir -p "$(dirname "$DEST")"
if [ -e "$DEST" ]; then
  echo "removing existing $DEST"
  rm -rf "$DEST"
fi
mkdir -p "$DEST"
cp -r "$SRC"/SKILL.md "$SRC"/references "$SRC"/scripts "$DEST"/
echo "installed: $DEST"
find "$DEST" -type f | sort
