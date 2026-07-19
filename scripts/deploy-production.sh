#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
COMPOSE=(docker compose -f docker-compose.production.yml --env-file .env)

if ! docker compose version >/dev/null 2>&1 || ! docker info >/dev/null 2>&1; then
  echo "Docker Engine and Docker Compose v2 are required." >&2
  exit 1
fi

if [[ ! -f .env ]]; then
  cp .env.production.example .env
  echo "Created .env. Set SUNNYREGISTER_DOMAIN, then run this command again." >&2
  exit 1
fi

env_value() {
  sed -n "s/^$1=//p" .env | tail -n 1 | tr -d '\r'
}

domain="$(env_value SUNNYREGISTER_DOMAIN)"
if [[ -z "$domain" ]]; then
  echo "Set SUNNYREGISTER_DOMAIN in .env to the Cloudflare public hostname." >&2
  exit 1
fi

mkdir -p secrets
chmod 700 secrets
generate_secret() {
  local path="$1" bytes="$2"
  if [[ ! -s "$path" ]]; then
    umask 077
    openssl rand -hex "$bytes" >"$path"
  fi
  chmod 600 "$path"
}
generate_secret secrets/admin_password 24
generate_secret secrets/python_worker_token 32

"${COMPOSE[@]}" config --quiet

dump_failure_diagnostics() {
  echo "=== Container status ===" >&2
  "${COMPOSE[@]}" ps >&2 || true
  echo "=== Python Worker logs ===" >&2
  "${COMPOSE[@]}" logs --no-color --tail=200 python-worker >&2 || true
  echo "=== Python Worker health ===" >&2
  docker inspect --format '{{json .State.Health}}' sunnyregister-python-worker >&2 || true
}

if ! "${COMPOSE[@]}" up -d --build --remove-orphans; then
  dump_failure_diagnostics
  exit 1
fi

ready=0
for _ in $(seq 1 120); do
  status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' sunnyregister-go 2>/dev/null || true)"
  if [[ "$status" == "healthy" ]]; then
    ready=1
    break
  fi
  sleep 2
done

"${COMPOSE[@]}" ps
if [[ "$ready" -ne 1 ]]; then
  dump_failure_diagnostics
  echo "SunnyRegister did not become healthy. Run: docker compose -f docker-compose.production.yml logs --tail=200" >&2
  exit 1
fi

port="$(env_value SUNNYREGISTER_PORT)"
port="${port:-8000}"
if ! curl -fsS "http://127.0.0.1:${port}/api/ready" >/dev/null; then
  echo "The container is healthy, but the host cannot reach 127.0.0.1:${port}. Check SUNNYREGISTER_BIND and Docker port mappings." >&2
  exit 1
fi

if [[ "$(env_value SUNNYREGISTER_PUBLIC_CHECK)" == "true" ]]; then
  public_ready=0
  for _ in $(seq 1 60); do
    if curl -fsS "https://${domain}/api/ready" >/dev/null 2>&1; then
      public_ready=1
      break
    fi
    sleep 2
  done
  if [[ "$public_ready" -ne 1 ]]; then
    echo "Local health passed, but https://${domain} is unreachable. Check the Cloudflare Tunnel or existing reverse proxy route." >&2
    exit 1
  fi
fi

echo "SunnyRegister origin is ready: http://127.0.0.1:${port}"
echo "Cloudflare hostname: https://${domain} (route it through the existing Tunnel or reverse proxy)"
echo "Admin username: $(env_value ADMIN_USERNAME)"
echo "Admin password file: ${ROOT}/secrets/admin_password"
