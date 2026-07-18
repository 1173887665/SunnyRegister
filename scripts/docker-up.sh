#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if docker compose version >/dev/null 2>&1; then
  COMPOSE=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE=(docker-compose)
else
  echo "Docker Compose was not found. Install Docker Engine with Compose v2." >&2
  exit 1
fi

if ! docker info >/dev/null 2>&1; then
  echo "Docker daemon is not running or the current user cannot access it." >&2
  exit 1
fi

if [[ ! -f .env ]]; then
  cp .env.production.example .env
fi

random_secret() {
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex "${1:-24}"
  else
    od -An -N "${1:-24}" -tx1 /dev/urandom | tr -d ' \n'
  fi
}

env_value() {
  sed -n "s/^$1=//p" .env | tail -n 1
}

set_env() {
  local key="$1" value="$2"
  if grep -q "^${key}=" .env; then
    sed -i "s|^${key}=.*|${key}=${value}|" .env
  else
    printf '\n%s=%s\n' "$key" "$value" >> .env
  fi
}

admin_password="$(env_value ADMIN_PASSWORD)"
if [[ -z "$admin_password" || "$admin_password" == change-me-* ]]; then
  admin_password="$(random_secret 16)"
  set_env ADMIN_PASSWORD "$admin_password"
fi
worker_token="$(env_value PYTHON_WORKER_TOKEN)"
if [[ -z "$worker_token" || "$worker_token" == change-me-* ]]; then
  set_env PYTHON_WORKER_TOKEN "$(random_secret 24)"
fi

build_args=(up -d --remove-orphans --build)
if [[ "${1:-}" == "--no-build" ]]; then
  build_args=(up -d --remove-orphans)
fi
"${COMPOSE[@]}" "${build_args[@]}"

port="$(env_value SUNNYREGISTER_PORT)"
port="${port:-8000}"
ready=0
for _ in $(seq 1 90); do
  if curl -fsS "http://127.0.0.1:${port}/api/ready" >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 2
done

"${COMPOSE[@]}" ps
if [[ "$ready" -ne 1 ]]; then
  echo "Services started but readiness timed out. Run: ${COMPOSE[*]} logs -f" >&2
  exit 1
fi

echo
echo "SunnyRegister is ready: http://127.0.0.1:${port}"
echo "Username: $(env_value ADMIN_USERNAME)"
echo "Password: stored in .env (ADMIN_PASSWORD); it is not printed for security"
echo "noVNC: http://127.0.0.1:$(env_value NOVNC_PORT)/vnc.html"
