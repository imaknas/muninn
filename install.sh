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
#      Linux); where Linux has no systemd user session (a container, WSL, a bare server) it is kept running in the
#      background until the next reboot instead; MUNINN_NO_SERVICE=1 skips this, and you run
#      `muninn --data <folder> serve` yourself;
#   5. the setup page in your browser: pick a model, paste its key, pair your phone.
# Safe to run again: it updates Muninn and leaves your data as it is. `muninn uninstall` takes it away again.
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

PORT="$("$MUNINN" --data "$DATA" config --effective 2>/dev/null | sed -n 's/^port: *//p' | head -1)"
PORT="${PORT:-18795}"
# whether the port is free once the Muninn before (if any) has stopped: anything still answering there is another
# program, or another Muninn, and the setup link would open that one
free() { curl -s -o /dev/null --max-time 2 "http://127.0.0.1:$PORT/"; [ $? -eq 7 ]; }
port_free() {
  i=0
  until free || [ $i -ge 10 ]; do sleep 1; i=$((i + 1)); done
  if ! free; then
    echo "Port $PORT is already in use by another program (or another Muninn): set port: to a free one in" >&2
    echo "$DATA/config.yaml, then run this again." >&2
    exit 1
  fi
}

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
  <key>EnvironmentVariables</key><dict>
    <key>MUNINN_SERVICE</key><string>1</string>
    <key>PATH</key><string>$(printf '%s' "$PATH" | sed 's/&/\&amp;/g; s/</\&lt;/g')</string>
  </dict>
  <key>KeepAlive</key><true/>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>$DATA/muninn.log</string>
  <key>StandardErrorPath</key><string>$DATA/muninn.log</string>
</dict>
</plist>
PL
  launchctl bootout "gui/$(id -u)/app.muninn" >/dev/null 2>&1 || true
  port_free
  launchctl bootstrap "gui/$(id -u)" "$PLIST"
  OPEN=open
  LOGS="$DATA/muninn.log"
elif systemctl --user show-environment >/dev/null 2>&1; then
  say "background service (systemd --user: muninn.service)"
  UNIT="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/muninn.service"
  mkdir -p "$(dirname "$UNIT")"
  cat > "$UNIT" <<UN
[Unit]
Description=Muninn

[Service]
ExecStart="$MUNINN" --data "$DATA" serve
Environment=MUNINN_SERVICE=1
Environment="PATH=$(printf '%s' "$PATH" | sed 's/%/%%/g')"
Restart=always
RestartSec=2

[Install]
WantedBy=default.target
UN
  systemctl --user daemon-reload
  systemctl --user enable muninn.service
  systemctl --user stop muninn.service  # one already running is on the version before
  port_free
  systemctl --user start muninn.service
  loginctl enable-linger "$(id -un)" >/dev/null 2>&1 || true  # keeps it running when you log out
  OPEN=xdg-open
  LOGS="journalctl --user -u muninn"
else
  say "no systemd user session here: Muninn runs in the background until the next reboot"
  RUN="$DATA/run.sh"
  cat > "$RUN" <<RN
#!/bin/sh
# Keeps Muninn running where there is no service manager, starting it again when it stops (as it does after a
# change of settings). Started by install.sh; stop it with: kill \$(cat "$DATA/run.pid")
echo \$\$ > "$DATA/run.pid"
trap 'kill "\$child" 2>/dev/null; exit 0' TERM INT
while :; do
  MUNINN_SERVICE=1 "$MUNINN" --data "$DATA" serve >> "$DATA/muninn.log" 2>&1 &
  child=\$!
  echo "\$child" > "$DATA/run.child"  # what muninn upgrade stops, for the loop to start the new version
  wait "\$child"
  sleep 2
done
RN
  chmod 700 "$RUN"
  if [ -s "$DATA/run.pid" ]; then kill "$(cat "$DATA/run.pid")" 2>/dev/null || true; fi  # the one an earlier install started
  port_free
  nohup "$RUN" >/dev/null 2>&1 &
  echo "  to keep it running across reboots, start $RUN from your system's startup, or use Docker (compose.yaml)"
  echo "  to stop it: kill \$(cat \"$DATA/run.pid\")"
  OPEN=xdg-open
  LOGS="$DATA/muninn.log"
fi

say "waiting for Muninn to start"
up() { curl -fsS "http://127.0.0.1:$PORT/healthz" 2>/dev/null | grep -q '"version"'; }  # Muninn, not whatever else holds the port
i=0
until up || [ $i -ge 30 ]; do sleep 1; i=$((i + 1)); done
if ! up; then
  echo "Muninn did not start within 30 seconds: what it said is in $LOGS" >&2
  exit 1
elif [ -s "$DATA/setup-url" ]; then
  URL="$(cat "$DATA/setup-url")"
  say "set it up at $URL"
  $OPEN "$URL" >/dev/null 2>&1 || true
else
  echo "Muninn is running; a device is already paired, so there is no setup link. Open it where you paired." >&2
fi
