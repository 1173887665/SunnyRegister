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
export PYTHONUTF8=1
export ACCOUNT_MANAGER_DATABASE_URL="$DATA/account_manager.db"
export PYTHON_WORKER_URL="http://127.0.0.1:8765"
export PYTHON_TASK_TYPES="sunny_register,sunny_login,sunny_refresh_session"
export PORT="${SUNNYREGISTER_PORT:-8000}"
export DISPLAY="${WORKER_DISPLAY:-${DISPLAY:-:99}}"

if ! xdpyinfo -display "$DISPLAY" >/dev/null 2>&1; then
  if [[ "${ENABLE_XVFB:-true}" != "true" && "${ENABLE_XVFB:-true}" != "1" ]]; then
    echo "DISPLAY $DISPLAY is unavailable and ENABLE_XVFB is disabled." >&2
    exit 1
  fi
  Xvfb "$DISPLAY" -screen 0 "${XVFB_WHD:-1600x900x24}" -nolisten tcp -ac >"$LOGS/xvfb.log" 2>&1 &
  echo $! > "$RUNTIME/xvfb.pid"
  for _ in $(seq 1 50); do
    xdpyinfo -display "$DISPLAY" >/dev/null 2>&1 && break
    sleep 0.1
  done
  if ! xdpyinfo -display "$DISPLAY" >/dev/null 2>&1; then
    echo "Xvfb failed to start. Check logs/xvfb.log." >&2
    exit 1
  fi
fi

if [[ "${ENABLE_NOVNC:-true}" == "true" || "${ENABLE_NOVNC:-true}" == "1" ]]; then
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

worker_ready=0
for _ in $(seq 1 60); do
  if ! is_running "$RUNTIME/python-worker.pid"; then
    break
  fi
  if curl -fsS "http://127.0.0.1:8765/health" >/dev/null 2>&1; then
    worker_ready=1
    break
  fi
  sleep 1
done
if [[ "$worker_ready" -ne 1 ]]; then
  "$ROOT/scripts/stop-linux.sh"
  echo "Python Worker failed to become ready. Check logs/python-worker.err.log." >&2
  exit 1
fi

(
  cd "$ROOT"
  nohup "$ROOT/bin/sunnyregister" >"$LOGS/backend.out.log" 2>"$LOGS/backend.err.log" &
  echo $! > "$RUNTIME/backend.pid"
)

ready=0
for _ in $(seq 1 60); do
  if curl -fsS "http://127.0.0.1:${PORT}/api/ready" >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 1
done
if [[ "$ready" -ne 1 ]]; then
  "$ROOT/scripts/stop-linux.sh"
  echo "SunnyRegister failed to become ready. Check logs/backend.err.log and logs/python-worker.err.log." >&2
  exit 1
fi

echo "SunnyRegister is ready: http://127.0.0.1:${PORT}"
echo "Username: ${ADMIN_USERNAME:-admin}"
echo "Password: stored in ${DATA}/admin_password.txt or ADMIN_PASSWORD; it is not printed for security"
if [[ "${ENABLE_NOVNC:-true}" == "true" || "${ENABLE_NOVNC:-true}" == "1" ]]; then
  echo "noVNC: http://127.0.0.1:${NOVNC_PORT:-6080}/vnc.html"
fi
