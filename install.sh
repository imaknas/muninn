#!/bin/sh
# Installs Muninn and starts it in the background, then opens its setup page.
#
#   curl -fsSL https://raw.githubusercontent.com/imaknas/muninn/main/install.sh | sh
#
# What it does, each step said as it goes:
#   1. uv (Astral's Python installer), if it is not there yet;
#   2. Muninn itself, as a uv tool (MUNINN_SOURCE: the package or a wheel's path or URL);
#   3. a data folder that only you can read (macOS: ~/Library/Application Support/Muninn,
#      Linux: ~/.local/share/muninn; MUNINN_DATA to choose another) with its keys and its own schedule;
#   4. a background service that keeps it running and starts it at login (launchd on macOS, systemd --user on
#      Linux); MUNINN_NO_SERVICE=1 skips this, and you run `muninn --data <folder> serve` yourself;
#   5. the setup page in your browser: pick a model, paste its key, pair your phone.
# Safe to run again: it updates Muninn and leaves your data as it is.
set -eu

SOURCE="${MUNINN_SOURCE:-muninn-ai}"  # PyPI's "muninn" is another project
say() { printf '\n→ %s\n' "$*"; }

case "$(uname -s)" in
  Darwin) OS=mac; DATA="${MUNINN_DATA:-$HOME/Library/Application Support/Muninn}" ;;
  Linux)  OS=linux; DATA="${MUNINN_DATA:-${XDG_DATA_HOME:-$HOME/.local/share}/muninn}" ;;
  *) echo "Muninn installs on macOS and Linux; on others, use Docker (see the README)." >&2; exit 1 ;;
esac

if ! command -v uv >/dev/null 2>&1; then
  say "installing uv"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  PATH="$HOME/.local/bin:$PATH"
fi

say "installing Muninn ($SOURCE)"
uv tool install --force --python 3.13 "$SOURCE"
MUNINN="$(uv tool dir --bin)/muninn"

say "data folder: $DATA"
mkdir -p "$DATA"
chmod 700 "$DATA"
if [ ! -f "$DATA/config.yaml" ]; then
  printf '# Muninn runs its own schedule (tick, the morning and evening reports, the nightly reflection).\njobs: default\n' > "$DATA/config.yaml"
  chmod 600 "$DATA/config.yaml"
fi
"$MUNINN" --data "$DATA" init

if [ "${MUNINN_NO_SERVICE:-}" = "1" ]; then
  say "no background service (MUNINN_NO_SERVICE): start it with"
  echo "  $MUNINN --data \"$DATA\" serve"
  exit 0
fi

rm -f "$DATA/setup-url"
if [ "$OS" = mac ]; then
  say "background service (launchd: app.muninn)"
  PLIST="$HOME/Library/LaunchAgents/app.muninn.plist"
  mkdir -p "$HOME/Library/LaunchAgents"
  cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>app.muninn</string>
  <key>ProgramArguments</key>
  <array><string>$MUNINN</string><string>--data</string><string>$DATA</string><string>serve</string></array>
  <key>EnvironmentVariables</key><dict><key>MUNINN_SERVICE</key><string>1</string></dict>
  <key>KeepAlive</key><true/>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>$DATA/muninn.log</string>
  <key>StandardErrorPath</key><string>$DATA/muninn.log</string>
</dict>
</plist>
PL
  launchctl bootout "gui/$(id -u)/app.muninn" >/dev/null 2>&1 || true
  launchctl bootstrap "gui/$(id -u)" "$PLIST"
  OPEN=open
else
  say "background service (systemd --user: muninn.service)"
  UNIT="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/muninn.service"
  mkdir -p "$(dirname "$UNIT")"
  cat > "$UNIT" <<UN
[Unit]
Description=Muninn

[Service]
ExecStart="$MUNINN" --data "$DATA" serve
Environment=MUNINN_SERVICE=1
Restart=always
RestartSec=2

[Install]
WantedBy=default.target
UN
  systemctl --user daemon-reload
  systemctl --user enable --now muninn.service
  loginctl enable-linger "$(id -un)" >/dev/null 2>&1 || true  # keeps it running when you log out
  OPEN=xdg-open
fi

say "waiting for Muninn to start"
i=0
while [ ! -s "$DATA/setup-url" ] && [ $i -lt 30 ]; do sleep 1; i=$((i + 1)); done
if [ -s "$DATA/setup-url" ]; then
  URL="$(cat "$DATA/setup-url")"
  say "set it up at $URL"
  $OPEN "$URL" >/dev/null 2>&1 || true
else
  echo "Muninn is running; a phone is already paired, so there is no setup link. Open it on your phone." >&2
fi
