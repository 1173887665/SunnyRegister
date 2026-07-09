#!/bin/sh
set -eu

export DISPLAY="${DISPLAY:-:99}"
export XVFB_WHD="${XVFB_WHD:-1600x900x24}"
export NOVNC_PORT="${NOVNC_PORT:-6080}"

if [ "${ENABLE_XVFB:-true}" = "true" ] || [ "${ENABLE_XVFB:-true}" = "1" ]; then
  echo "[entrypoint] starting Xvfb on ${DISPLAY} (${XVFB_WHD})"
  Xvfb "${DISPLAY}" -screen 0 "${XVFB_WHD}" -nolisten tcp -ac >/tmp/xvfb.log 2>&1 &
fi

if [ "${ENABLE_NOVNC:-true}" = "true" ] || [ "${ENABLE_NOVNC:-true}" = "1" ]; then
  echo "[entrypoint] starting x11vnc and noVNC on ${NOVNC_PORT}"
  x11vnc -display "${DISPLAY}" -forever -shared -nopw -quiet -listen 0.0.0.0 -rfbport 5900 >/tmp/x11vnc.log 2>&1 &
  websockify --web=/usr/share/novnc/ "0.0.0.0:${NOVNC_PORT}" localhost:5900 >/tmp/novnc.log 2>&1 &
fi

exec "$@"
