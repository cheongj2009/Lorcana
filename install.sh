#!/bin/bash
# Portable installer for the Lorcana stock watcher.
# Generates a launchd job pointing at THIS folder (wherever it lives) and starts
# it on a 5-minute schedule. Safe to re-run; it reloads cleanly.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LABEL="com.lorcana.stockwatcher"
LEGACY_LABEL="com.jamescheong.stockwatcher"
AGENTS_DIR="$HOME/Library/LaunchAgents"
PLIST="$AGENTS_DIR/$LABEL.plist"
LEGACY_PLIST="$AGENTS_DIR/$LEGACY_LABEL.plist"

mkdir -p "$AGENTS_DIR"

# Remove any older/legacy job so we don't run duplicates.
for old in "$LEGACY_PLIST" "$PLIST"; do
  if [ -f "$old" ]; then
    launchctl unload "$old" 2>/dev/null || true
    rm -f "$old"
  fi
done

cat > "$PLIST" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>$DIR/run.sh</string>
    </array>
    <key>StartInterval</key>
    <integer>300</integer>
    <key>RunAtLoad</key>
    <true/>
    <key>WorkingDirectory</key>
    <string>$DIR</string>
    <key>StandardOutPath</key>
    <string>$DIR/launchd.out.log</string>
    <key>StandardErrorPath</key>
    <string>$DIR/launchd.err.log</string>
</dict>
</plist>
PLIST_EOF

launchctl load "$PLIST"

echo "Installed and started: $LABEL"
echo "  Folder:   $DIR"
echo "  Schedule: every 300s (5 min), runs immediately on load."
echo "  Logs:     $DIR/stock_watcher.log"
echo
echo "Manage it with:"
echo "  launchctl list | grep stockwatcher      # check status"
echo "  $DIR/uninstall.sh                        # stop & remove"
