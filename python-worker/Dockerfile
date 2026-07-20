# syntax=docker/dockerfile:1.7

FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    ACCOUNT_MANAGER_DATABASE_URL=sqlite:////app/data/account_manager.db \
    DISPLAY=:99 \
    ENABLE_XVFB=false \
    ENABLE_NOVNC=false \
    NOVNC_PORT=6080 \
    TZ=Asia/Shanghai

RUN for source in /etc/apt/sources.list /etc/apt/sources.list.d/*.sources; do \
      if [ -f "$source" ]; then \
        sed -i 's|http://deb.debian.org|https://deb.debian.org|g; s|http://security.debian.org|https://security.debian.org|g' "$source"; \
      fi; \
    done \
    && apt-get -o Acquire::Retries=5 -o Acquire::ForceIPv4=true update \
    && apt-get -o Acquire::Retries=5 -o Acquire::ForceIPv4=true install -y --no-install-recommends \
      ca-certificates curl tzdata \
      xvfb x11vnc novnc websockify \
      x11-utils \
      fonts-liberation fonts-noto-cjk \
      libgtk-3-0 libx11-xcb1 libasound2 \
      libdbus-glib-1-2 libxt6 \
      libnss3 libnspr4 libgbm1 \
      libxcomposite1 libxdamage1 libxfixes3 libxrandr2 \
      libatk1.0-0 libatk-bridge2.0-0 libcairo2 libpango-1.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY python-worker/requirements.txt /app/requirements.txt
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --upgrade pip \
    && pip install -r /app/requirements.txt \
    && python -m playwright install --with-deps chromium \
    && python -m camoufox fetch \
    && CAMOUFOX_BIN="$(python -c 'from camoufox.pkgman import launch_path; print(launch_path())')" \
    && test -x "$CAMOUFOX_BIN" \
    && "$CAMOUFOX_BIN" --version \
    && ! ldd "$CAMOUFOX_BIN" | grep -q 'not found' \
    && rm -rf /var/lib/apt/lists/* /tmp/*

COPY python-worker/worker.py /app/worker.py
COPY python-worker/sunny_runner.py /app/sunny_runner.py
COPY python-worker/sunny_core/ /app/sunny_core/
COPY python-worker/docker-entrypoint.sh /usr/local/bin/sunny-worker-entrypoint
RUN chmod +x /usr/local/bin/sunny-worker-entrypoint

EXPOSE 8765 6080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8765/health >/dev/null || exit 1
ENTRYPOINT ["/usr/local/bin/sunny-worker-entrypoint"]
CMD ["uvicorn", "worker:app", "--host", "0.0.0.0", "--port", "8765"]
