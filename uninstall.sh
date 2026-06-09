#!/bin/bash
# Stops and removes the Lorcana stock watcher launchd job.
set -euo pipefail

AGENTS_DIR="$HOME/Library/LaunchAgents"
for label in com.lorcana.stockwatcher com.jamescheong.stockwatcher; do
  plist="$AGENTS_DIR/$label.plist"
  if [ -f "$plist" ]; then
    launchctl unload "$plist" 2>/dev/null || true
    rm -f "$plist"
    echo "Removed $label"
  fi
done
echo "Stock watcher stopped. Your folder and config are untouched."
