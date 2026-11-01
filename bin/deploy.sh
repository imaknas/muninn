#!/usr/bin/env bash
# Test, update the runtime clone, restart the server, check the running server
# end to end (bin/smoke.py), and only then push main. The commit is pinned when
# the deploy starts: the tests, the runtime and the push all use that commit,
# even if main moves on meanwhile. The runtime takes it straight from this
# checkout, so a version that fails the smoke check never reaches the remote.
# When the new version fails after the restart, the runtime is put back on the
# previous version (rebuilt and restarted) and data/deploys.log says why.
# One deploy at a time: every checkout takes the same lock inside the runtime.
#
#   bin/deploy.sh
#
# Exit codes:
#   0  live, logged and pushed
#   1  refused (not on main, uncommitted changes, npm missing, no runtime,
#      or the runtime cannot be fast-forwarded to this commit); nothing changed
#   2  launchd job not loaded: the runtime is updated but was not restarted;
#      nothing pushed
#   3  the server did not become healthy; rolled back to the previous version,
#      nothing pushed
#   4  build or tests failed; nothing changed, nothing pushed
#   5  the smoke check failed; rolled back to the previous version, nothing pushed
#   6  another deploy held the lock for too long; nothing changed
#   7  the rollback itself failed: the runtime is stuck half-way, the commands
#      to finish by hand are printed
#   8  live and logged, but the push failed; push the printed commit by hand
#   9  updating the runtime (npm ci, build, uv sync, init) failed before the
#      restart; rolled back to the previous version, nothing pushed
#
# Env overrides: MUNINN_RUNTIME (default ~/services/muninn), MUNINN_LABEL
# (launchd label, default local.muninn), MUNINN_PORT (default 18795),
# MUNINN_LOCK_WAIT (seconds to wait for another deploy, default 1200),
# MUNINN_TESTS (how the pinned commit is tested: "platform" hands it to the
# platform's test runner, `agenthub test --when deploy`, which runs the tiers the
# project declares for a deploy on that commit exported and does not run again
# a tree it already passed; "here" runs the unit and browser tests in this
# checkout; unset: the platform's runner when it is installed, else here).
# For testing this script against a scratch runtime without launchd:
# MUNINN_RESTART_CMD (shell command that restarts the server instead of
# launchctl kickstart), MUNINN_SMOKE_CMD (shell command that replaces the
# bin/smoke.py call), MUNINN_HEALTH_WAIT (seconds to wait for /healthz,
# default 30).
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
RUNTIME="${MUNINN_RUNTIME:-$HOME/services/muninn}"
LABEL="${MUNINN_LABEL:-local.muninn}"
PORT="${MUNINN_PORT:-18795}"
LOCK_WAIT="${MUNINN_LOCK_WAIT:-1200}"
HEALTH_WAIT="${MUNINN_HEALTH_WAIT:-30}"
RESTART_CMD="${MUNINN_RESTART_CMD:-}"
SMOKE_CMD="${MUNINN_SMOKE_CMD:-}"
UV="$(command -v uv || echo "$HOME/.local/bin/uv")"
NPM="$(command -v npm || true)"
if [ -z "$NPM" ]; then
  echo "deploy: npm not found; the app's TypeScript needs Node to build" >&2
  exit 1
fi

cd "$REPO"
check_checkout() {
  branch="$(git rev-parse --abbrev-ref HEAD)"
  if [ "$branch" != "main" ]; then
    echo "deploy: on branch $branch; deploy from main" >&2
    exit 1
  fi
  if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
    echo "deploy: uncommitted changes; commit first" >&2
    exit 1
  fi
}
check_checkout
if [ ! -d "$RUNTIME/.git" ]; then
  echo "deploy: $RUNTIME is not a git clone of Muninn" >&2
  exit 1
fi

# The lock lives in the runtime, so deploys from any checkout wait for each
# other. It is held on fd 9 for as long as this script runs and goes away with
# the process, so a killed deploy never leaves a stale lock behind. Commands
# that may leave a process running (the restart) get fd 9 closed.
mkdir -p "$RUNTIME/data"
chmod 700 "$RUNTIME/data"
exec 9>>"$RUNTIME/data/deploy.lock"
if command -v flock >/dev/null 2>&1; then
  lock() { flock -w "$1" 9; }
elif command -v lockf >/dev/null 2>&1; then
  lock() { lockf -s -t "$1" 9; }
else
  echo "deploy: neither flock nor lockf found; cannot take the deploy lock" >&2
  exit 1
fi
if ! lock 0; then
  echo "→ waiting for another deploy to finish (up to ${LOCK_WAIT}s)"
  if ! lock "$LOCK_WAIT"; then
    echo "deploy: someone else is deploying (lock $RUNTIME/data/deploy.lock still held after ${LOCK_WAIT}s); nothing changed" >&2
    exit 6
  fi
  check_checkout  # the checkout may have changed while this deploy waited
fi

# Pinned after the lock, so a deploy that waited ships what main is once it
# runs, and nothing that lands on main from here on is carried along.
sha="$(git rev-parse HEAD)"
short="$(git rev-parse --short HEAD)"
previous_full="$(git -C "$RUNTIME" rev-parse HEAD)"
previous="$(git -C "$RUNTIME" rev-parse --short HEAD)"
# Only ever move the runtime forward: a deploy from a checkout that is behind
# what is live would otherwise take the server back in time.
if ! git merge-base --is-ancestor "$previous_full" "$sha" 2>/dev/null; then
  echo "deploy: the runtime runs $previous, which is not part of $short; pull main first" >&2
  exit 1
fi
echo "→ deploying $short (live now: $previous)"

echo "→ build (TypeScript: type check and compile)"
"$NPM" ci --no-audit --no-fund --silent || { echo "deploy: npm ci failed; nothing pushed" >&2; exit 4; }
"$NPM" run --silent build || { echo "deploy: the TypeScript does not compile; nothing pushed" >&2; exit 4; }
TESTS="${MUNINN_TESTS:-}"
if [ -z "$TESTS" ]; then
  if command -v agenthub >/dev/null 2>&1; then TESTS=platform; else TESTS=here; fi
fi
if [ "$TESTS" = platform ]; then
  echo "→ tests (the platform's runner, on $short)"
  agenthub test --when deploy --rev "$sha" || { echo "deploy: tests failed; nothing pushed" >&2; exit 4; }
else
  echo "→ tests"
  "$UV" run pytest -q -n 4 || { echo "deploy: tests failed; nothing pushed" >&2; exit 4; }
  echo "→ browser tests"
  "$UV" run pytest -q -m e2e -n 8 || {
    echo "deploy: browser tests failed; nothing pushed (first time on this machine? run: uv run playwright install webkit)" >&2
    exit 4
  }
fi
# The tests ran on this checkout's files: if main moved meanwhile (a merge
# fast-forwards this checkout), they may have tested a mix, not the pinned commit.
if [ "$(git rev-parse HEAD)" != "$sha" ]; then
  echo "deploy: main moved to $(git rev-parse --short HEAD) during the tests, so $short was not tested on its own; nothing changed, run again" >&2
  exit 1
fi

restart_server() {
  if [ -n "$RESTART_CMD" ]; then
    bash -c "$RESTART_CMD" 9>&-
  else
    launchctl kickstart -k "gui/$(id -u)/$LABEL" 9>&-
  fi
}

wait_healthy() {
  for _ in $(seq 1 "$HEALTH_WAIT"); do
    if curl -fsS "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  return 1
}

# the optional parts the runtime is synced with, those the version checked out declares (an older one may not)
extras() {
  if grep -q '^autofill = ' pyproject.toml 2>/dev/null; then echo "--extra autofill"; fi
}

stuck() {
  local step="$1"
  echo "deploy: ROLLBACK STUCK at: $step" >&2
  echo "  $RUNTIME may be anywhere between $short and $previous, and the server may be down." >&2
  echo "  to finish going back to $previous by hand:" >&2
  echo "    cd $RUNTIME" >&2
  echo "    git reset --hard $previous" >&2
  echo "    npm ci --no-audit --no-fund && npm run build" >&2
  echo "    uv sync --frozen --no-dev $(extras)" >&2
  if [ -n "$RESTART_CMD" ]; then
    echo "    $RESTART_CMD" >&2
  else
    echo "    launchctl kickstart -k gui/$(id -u)/$LABEL" >&2
  fi
  echo "    curl -fsS http://127.0.0.1:$PORT/healthz" >&2
  exit 7
}

# Put the runtime fully back on the previous version: code, front end and
# packages, then restart. No tests and no push: that version already passed
# both and was running until a moment ago.
rollback() {
  local reason="$1" code="$2"
  echo "→ roll back $RUNTIME to $previous ($reason)"
  cd "$RUNTIME"
  git reset --hard --quiet "$previous_full" || stuck "git reset --hard $previous"
  "$NPM" ci --no-audit --no-fund --silent || stuck "npm ci"
  "$NPM" run --silent build || stuck "npm run build"
  "$UV" sync --frozen --no-dev $(extras) || stuck "uv sync --frozen --no-dev $(extras)"
  restart_server || stuck "restart"
  wait_healthy || stuck "waiting for the server on port $PORT to become healthy"
  echo "$(date '+%Y-%m-%d %H:%M') rollback $short -> $previous: $reason" >> data/deploys.log
  echo "deploy: $short failed ($reason); rolled back, the server runs $previous again. Nothing pushed." >&2
  exit "$code"
}

echo "→ update $RUNTIME"
cd "$RUNTIME"
# Straight from this checkout, not from the remote: the remote only gets the
# commit once it has passed the smoke check. The runtime's origin is untouched,
# so a plain `git pull --ff-only origin main` there keeps working.
if ! { git fetch --quiet "$REPO" "$sha" && git merge --ff-only --quiet "$sha"; }; then
  echo "deploy: could not fast-forward $RUNTIME to $short (local changes there?); nothing changed, nothing pushed" >&2
  exit 1
fi
"$NPM" ci --no-audit --no-fund --silent || rollback "update failed at npm ci" 9
"$NPM" run --silent build || rollback "update failed at npm run build" 9
"$UV" sync --frozen --no-dev $(extras) || rollback "update failed at uv sync" 9
# the browser application forms are filled in (optional: without it packets are applied to by hand)
if [ -x .venv/bin/playwright ]; then
  .venv/bin/playwright install chromium >/dev/null || echo "deploy: no browser for filling in application forms (playwright install chromium failed)" >&2
fi
.venv/bin/muninn init || rollback "update failed at muninn init" 9

echo "→ restart $LABEL"
if [ -z "$RESTART_CMD" ] && ! launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1; then
  echo "deploy: launchd job $LABEL is not loaded; code is updated but the server was not restarted; nothing pushed" >&2
  exit 2
fi
restart_server || rollback "restart failed" 3

echo "→ health check"
wait_healthy || rollback "server not healthy on port $PORT" 3

echo "→ smoke check"
# The served app is compared with the runtime's own build of the pinned commit
# (third argument), not with this checkout, which may have moved on.
if [ -n "$SMOKE_CMD" ]; then
  smoke() { bash -c "$SMOKE_CMD"; }
else
  smoke() { "$RUNTIME/.venv/bin/python" "$REPO/bin/smoke.py" "$RUNTIME" "$PORT" "$RUNTIME"; }
fi
smoke || rollback "smoke check failed" 5
echo "$(date '+%Y-%m-%d %H:%M') $previous -> $short" >> data/deploys.log

echo "→ push $short"
cd "$REPO"
if ! git push origin "$sha:refs/heads/main"; then
  echo "deploy: the server runs $short and is healthy, but the push failed." >&2
  echo "  push it by hand: git -C $REPO push origin $sha:refs/heads/main" >&2
  exit 8
fi
# Keeps the runtime's view of the remote current, as the pull used to.
git -C "$RUNTIME" fetch --quiet origin || true
echo "ok: http://127.0.0.1:$PORT is healthy ($short)"
