#!/usr/bin/env bash
# Installs Muninn's Mac collector: window activity from ActivityWatch and this Mac's commits, sent to Muninn
# (contracts/activity.v1.md). Copy the line from Muninn's settings page (電腦活動) and run it on the Mac:
#
#   curl -fsSL <Muninn's address>/collectors/mac/install.sh | bash
#
# It asks for Muninn's ingest token once (shown beside that line); run again, it keeps what it has and updates
# the collector. A work Chrome profile can be named: `... | bash -s -- "<profile name>"`.
set -euo pipefail

MUNINN_URL="${MUNINN_URL:-__MUNINN_URL__}"  # filled in by the Muninn that served this script
WORK_PROFILE_ARG="${1:-}"
DIR="$HOME/.muninn-agent"
LABEL="local.muninn-agent"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
mkdir -p "$DIR"
chmod 700 "$DIR"

say() { printf '\n→ %s\n' "$*"; }

say "checking python3"
if ! /usr/bin/python3 -c 'import sys; assert sys.version_info >= (3, 9)' 2>/dev/null; then
  echo "python3 is missing: run 'xcode-select --install', then this again." >&2
  exit 1
fi

say "ActivityWatch"
if [ ! -d "/Applications/ActivityWatch.app" ]; then
  if command -v brew >/dev/null; then
    brew install --cask activitywatch
  else
    echo "Install ActivityWatch from https://activitywatch.net/downloads/ (or Homebrew), then run this again." >&2
    exit 1
  fi
fi
open -a ActivityWatch || true
osascript -e 'tell application "System Events" to if not (exists login item "ActivityWatch") then make login item at end with properties {path:"/Applications/ActivityWatch.app", hidden:true}' >/dev/null 2>&1 || true

say "the collector, from $MUNINN_URL"
curl -fsSL "$MUNINN_URL/collectors/mac/muninn_agent.py" -o "$DIR/muninn_agent.py.new"
mv "$DIR/muninn_agent.py.new" "$DIR/muninn_agent.py"

TOKEN=""
if [ -f "$DIR/config.json" ]; then
  TOKEN="$(/usr/bin/python3 -c 'import json, sys; print(json.load(open(sys.argv[1])).get("token", ""))' "$DIR/config.json")"
fi
if [ -z "$TOKEN" ]; then
  printf 'Muninn ingest token (from the settings page, beside the line you copied): ' > /dev/tty
  read -rs TOKEN < /dev/tty
  echo > /dev/tty
fi
if [ -z "$TOKEN" ]; then
  echo "no token given; nothing installed" >&2
  exit 1
fi
EMAIL="$(git config --global user.email || true)"

/usr/bin/python3 - "$DIR/config.json" "$TOKEN" "$MUNINN_URL" "$EMAIL" "$WORK_PROFILE_ARG" <<'PY'
import json, os, sys
path, token, url, email, work_profile = sys.argv[1:]
cfg = json.load(open(path)) if os.path.exists(path) else {}
cfg.update({
    "ingest_url": url.rstrip("/") + "/api/ingest",
    "token": token,
    "git_authors": [email] if email else cfg.get("git_authors", []),
    "repo_roots": cfg.get("repo_roots", ["~"]),
    "nested_roots": cfg.get("nested_roots", []),  # folders holding several repos a level or two down
    "work_profile": work_profile or cfg.get("work_profile", ""),
    "browsers": cfg.get("browsers", ["Google Chrome"]),
})
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "w") as f:
    json.dump(cfg, f, indent=1)
PY

say "launchd job ($LABEL, every 5 minutes)"
cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array><string>/usr/bin/python3</string><string>$DIR/muninn_agent.py</string></array>
  <key>StartInterval</key><integer>300</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>$DIR/agent.log</string>
  <key>StandardErrorPath</key><string>$DIR/agent.log</string>
</dict>
</plist>
PL
launchctl bootout "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"

say "browser extension for the work profile"
PROFILE_NAME="$(/usr/bin/python3 -c 'import json, sys; print(json.load(open(sys.argv[1])).get("work_profile", ""))' "$DIR/config.json")"
WORK_PROFILE="$(/usr/bin/python3 - "$PROFILE_NAME" <<'PY'
import json, os, sys
name = sys.argv[1].lower()
p = os.path.expanduser("~/Library/Application Support/Google/Chrome/Local State")
try:
    cache = json.load(open(p))["profile"]["info_cache"]
    print(next((d for d, info in cache.items() if name and info.get("name", "").lower() == name), ""))
except Exception:
    print("")
PY
)"
STORE="https://chromewebstore.google.com/detail/activitywatch-web-watcher/nglaklhklhcoonedhgnpgddginnjdadi"
if [ -n "$WORK_PROFILE" ]; then
  open -na "Google Chrome" --args --profile-directory="$WORK_PROFILE" "$STORE"
  echo "Opened the Chrome Web Store in the $PROFILE_NAME profile: click 'Add to Chrome' there."
else
  echo "Install the extension from $STORE in your work Chrome profile only (or name that profile: bash -s -- \"<name>\")."
fi
echo "Install it ONLY in the work profile: that is how Muninn tells work browsing from personal."

cat <<'TXT'

Two macOS permissions are needed once (System Settings → Privacy & Security):
  • Accessibility → allow "aw-watcher-window" (or ActivityWatch)
  • Screen Recording → allow ActivityWatch (macOS hides window titles otherwise)

Check it is working in a few minutes:  tail ~/.muninn-agent/agent.log
TXT
