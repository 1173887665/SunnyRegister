# syntax=docker/dockerfile:1.7

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    ACCOUNT_MANAGER_DATABASE_URL=sqlite:////app/data/account_manager.db \
    DISPLAY=:99 \
    ENABLE_XVFB=true \
    ENABLE_NOVNC=true \
    NOVNC_PORT=6080 \
    TZ=Asia/Shanghai

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
      ca-certificates curl tzdata \
      xvfb x11vnc novnc websockify \
      fonts-liberation fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY python-worker/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r /app/requirements.txt \
    && python -m playwright install --with-deps chromium \
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
