#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

for command in python3 node npm go; do
  if ! command -v "$command" >/dev/null 2>&1; then
    echo "$command was not found. Install Python 3.12+, Node.js 22+ and Go 1.23+." >&2
    exit 1
  fi
done

if [[ "${SKIP_SYSTEM_DEPS:-0}" != "1" ]] && command -v apt-get >/dev/null 2>&1; then
  if [[ "$(id -u)" -eq 0 ]]; then
    SUDO=()
  elif command -v sudo >/dev/null 2>&1; then
    SUDO=(sudo)
  else
    echo "sudo is required to install Xvfb/noVNC dependencies." >&2
    exit 1
  fi
  "${SUDO[@]}" apt-get update
  "${SUDO[@]}" apt-get install -y --no-install-recommends xvfb x11vnc novnc websockify x11-utils fonts-liberation fonts-noto-cjk curl ca-certificates
fi

if [[ ! -x python-worker/.venv/bin/python ]] || ! python-worker/.venv/bin/python -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)' >/dev/null 2>&1; then
  python3 -m venv --clear python-worker/.venv
fi
python-worker/.venv/bin/python -m pip install --upgrade pip
python-worker/.venv/bin/python -m pip install -r python-worker/requirements.txt

if [[ "${SKIP_SYSTEM_DEPS:-0}" == "1" ]]; then
  python-worker/.venv/bin/python -m playwright install chromium
else
  python-worker/.venv/bin/python -m playwright install --with-deps chromium
fi
python-worker/.venv/bin/python -m camoufox fetch

(
  cd frontend
  npm ci
  npm run build
)

mkdir -p bin
(
  cd backend
  go build -trimpath -ldflags="-s -w" -o ../bin/sunnyregister .
)

echo "SunnyRegister native Linux runtime is ready."
