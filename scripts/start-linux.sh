#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME="$ROOT/.runtime"
LOGS="$ROOT/logs"
DATA="$ROOT/data"
mkdir -p "$RUNTIME" "$LOGS" "$DATA"

if [[ ! -f "$ROOT/.env" ]]; then
  cp "$ROOT/.env.example" "$ROOT/.env"
fi
set -a
# shellcheck disable=SC1091
source "$ROOT/.env"
set +a

if [[ ! -x "$ROOT/bin/sunnyregister" || ! -x "$ROOT/python-worker/.venv/bin/python" ]]; then
  "$ROOT/scripts/setup-linux.sh"
fi

is_running() {
  [[ -f "$1" ]] && kill -0 "$(cat "$1")" >/dev/null 2>&1
}
if is_running "$RUNTIME/backend.pid" || is_running "$RUNTIME/python-worker.pid"; then
  echo "SunnyRegister already appears to be running. Run scripts/stop-linux.sh first." >&2
  exit 1
fi

export TZ="${TZ:-Asia/Shanghai}"
export SUNNY_TIMEZONE="$TZ"
export SUNNY_HEALTHCHECK_ENABLED="${SUNNY_HEALTHCHECK_ENABLED:-true}"
export SUNNY_HEALTHCHECK_TIME="${SUNNY_HEALTHCHECK_TIME:-06:00}"
export SUNNY_HEALTHCHECK_CONCURRENCY="${SUNNY_HEALTHCHECK_CONCURRENCY:-2}"
export PYTHONUTF8=1
if [[ -z "${DATABASE_URL:-}" ]]; then
  echo "DATABASE_URL is required. Configure PostgreSQL in .env before starting SunnyRegister." >&2
  exit 1
fi
export PYTHON_WORKER_URL="http://127.0.0.1:8765"
export PYTHON_TASK_TYPES="sunny_register,sunny_login,sunny_refresh_session,sunny_acquire_rt,sunny_rebind"
export PORT="${SUNNYREGISTER_PORT:-8000}"
export DISPLAY="${WORKER_DISPLAY:-${DISPLAY:-:99}}"

# Background Camoufox does not need a display. Keep Xvfb/noVNC disabled by
# default so an idle server does not retain graphical processes or memory.
if [[ "${ENABLE_XVFB:-false}" == "true" || "${ENABLE_XVFB:-false}" == "1" ]] && ! xdpyinfo -display "$DISPLAY" >/dev/null 2>&1; then
  Xvfb "$DISPLAY" -screen 0 "${XVFB_WHD:-1600x900x24}" -nolisten tcp -ac >"$LOGS/xvfb.log" 2>&1 &
  echo $! > "$RUNTIME/xvfb.pid"
fi

if [[ "${ENABLE_NOVNC:-false}" == "true" || "${ENABLE_NOVNC:-false}" == "1" ]]; then
  if ! xdpyinfo -display "$DISPLAY" >/dev/null 2>&1; then
    echo "ENABLE_NOVNC requires a display; set ENABLE_XVFB=true or provide WORKER_DISPLAY." >&2
    exit 1
  fi
  x11vnc -display "$DISPLAY" -forever -shared -nopw -quiet -listen 127.0.0.1 -rfbport 5900 >"$LOGS/x11vnc.log" 2>&1 &
  echo $! > "$RUNTIME/x11vnc.pid"
  websockify --web=/usr/share/novnc/ "127.0.0.1:${NOVNC_PORT:-6080}" localhost:5900 >"$LOGS/novnc.log" 2>&1 &
  echo $! > "$RUNTIME/novnc.pid"
fi

(
  cd "$ROOT/python-worker"
  nohup "$ROOT/python-worker/.venv/bin/python" -m uvicorn worker:app --host 127.0.0.1 --port 8765 >"$LOGS/python-worker.out.log" 2>"$LOGS/python-worker.err.log" &
  echo $! > "$RUNTIME/python-worker.pid"
)

# The backend may need to load a large task_events table during startup.
# Keep the timeout configurable so a slow but healthy database is not mistaken
# for a failed deployment. Override in .env when the database needs longer.
STARTUP_TIMEOUT_SECONDS="${SUNNY_STARTUP_TIMEOUT_SECONDS:-300}"
if ! [[ "$STARTUP_TIMEOUT_SECONDS" =~ ^[1-9][0-9]*$ ]]; then
  echo "SUNNY_STARTUP_TIMEOUT_SECONDS must be a positive integer; got: $STARTUP_TIMEOUT_SECONDS" >&2
  exit 1
fi

wait_for_ready() {
  local pid_file="$1"
  local url="$2"
  local service_name="$3"
  local elapsed=0

  while (( elapsed < STARTUP_TIMEOUT_SECONDS )); do
    if ! is_running "$pid_file"; then
      echo "$service_name exited before becoming ready. Check its log file." >&2
      return 1
    fi
    if curl -fsS "$url" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
    ((elapsed += 1))
  done

  echo "$service_name did not become ready within ${STARTUP_TIMEOUT_SECONDS}s. Check its log file." >&2
  return 1
}

if ! wait_for_ready "$RUNTIME/python-worker.pid" "http://127.0.0.1:8765/health" "Python Worker"; then
  "$ROOT/scripts/stop-linux.sh"
  exit 1
fi

(
  cd "$ROOT"
  nohup "$ROOT/bin/sunnyregister" >"$LOGS/backend.out.log" 2>"$LOGS/backend.err.log" &
  echo $! > "$RUNTIME/backend.pid"
)

if ! wait_for_ready "$RUNTIME/backend.pid" "http://127.0.0.1:${PORT}/api/ready" "SunnyRegister backend"; then
  "$ROOT/scripts/stop-linux.sh"
  echo "SunnyRegister failed to become ready. Check logs/backend.err.log and logs/python-worker.err.log." >&2
  exit 1
fi

echo "SunnyRegister is ready: http://127.0.0.1:${PORT}"
echo "Username: ${ADMIN_USERNAME:-admin}"
echo "Password: stored in ${DATA}/admin_password.txt or ADMIN_PASSWORD; it is not printed for security"
if [[ "${ENABLE_NOVNC:-false}" == "true" || "${ENABLE_NOVNC:-false}" == "1" ]]; then
  echo "noVNC: http://127.0.0.1:${NOVNC_PORT:-6080}/vnc.html"
fi
