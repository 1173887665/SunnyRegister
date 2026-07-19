#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
chmod +x "$ROOT"/scripts/*.sh 2>/dev/null || true
TARGET="${1:-origin/main}"
LOCK_FILE="${TMPDIR:-/tmp}/sunnyregister-update.lock"
exec 9>"$LOCK_FILE"
flock -n 9 || { echo "Another SunnyRegister update is running." >&2; exit 1; }

if ! git -c core.fileMode=false diff --quiet || ! git -c core.fileMode=false diff --cached --quiet; then
  echo "The deployment checkout has local changes. Refusing to overwrite them." >&2
  git status --short >&2
  exit 1
fi

previous="$(git rev-parse HEAD)"
git fetch --tags --prune origin
git rev-parse --verify "${TARGET}^{commit}" >/dev/null

timestamp="$(date +%Y%m%d_%H%M%S)"
if docker inspect sunnyregister-python-worker >/dev/null 2>&1; then
  docker compose -f docker-compose.production.yml --env-file .env exec -T python-worker \
    python -c "import os,sqlite3; p='/app/data/account_manager.db'; b=f'/app/data/backup_${timestamp}.db'; s=sqlite3.connect(p); d=sqlite3.connect(b); s.backup(d); d.close(); s.close(); print(b)"
fi

git checkout --detach "$TARGET"
chmod +x "$ROOT"/scripts/*.sh 2>/dev/null || true
if bash ./scripts/deploy-production.sh; then
  echo "Updated SunnyRegister: ${previous} -> $(git rev-parse HEAD)"
  exit 0
fi

echo "Deployment health check failed; rolling back to ${previous}." >&2
git checkout --detach "$previous"
chmod +x "$ROOT"/scripts/*.sh 2>/dev/null || true
bash ./scripts/deploy-production.sh
exit 1
