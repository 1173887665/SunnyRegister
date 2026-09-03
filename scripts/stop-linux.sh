#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME="$ROOT/.runtime"

for name in backend python-worker link-workbench-worker novnc x11vnc xvfb; do
  pid_file="$RUNTIME/${name}.pid"
  [[ -f "$pid_file" ]] || continue
  pid="$(cat "$pid_file")"
  if [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" >/dev/null 2>&1; then
    kill "$pid" >/dev/null 2>&1 || true
  fi
  rm -f "$pid_file"
done
echo "SunnyRegister native Linux services stopped."
