#!/bin/bash
# Wrapper used by launchd: runs the watcher and appends output to a log file.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG="$DIR/stock_watcher.log"

# Prefer Homebrew python3, fall back to PATH python3.
PYTHON_BIN="$(command -v python3 || true)"
if [ -x "/opt/homebrew/bin/python3" ]; then
  PYTHON_BIN="/opt/homebrew/bin/python3"
fi

{
  echo "===== run at $(date '+%Y-%m-%d %H:%M:%S %z') ====="
  "$PYTHON_BIN" "$DIR/stock_watcher.py"
} >> "$LOG" 2>&1
