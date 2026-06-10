#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="${APP_DIR:-$(pwd)}"
TARGET_SHA="${1:-${PREVIOUS_SHA:-}}"
RESTORE_DATABASE_ON_ROLLBACK="${RESTORE_DATABASE_ON_ROLLBACK:-false}"
COMPOSE_FILES=(-f docker-compose.yml -f docker-compose.production.yml)
COMPOSE=(docker compose --env-file .env.production "${COMPOSE_FILES[@]}")

cd "$APP_DIR"

if [ -z "$TARGET_SHA" ]; then
  echo "Rollback target SHA is required" >&2
  exit 1
fi

if [ ! -f .env.production ]; then
  echo "Missing .env.production in $APP_DIR" >&2
  exit 1
fi

read_env_var() {
  python3 - "$1" <<'PYENV'
from pathlib import Path
import sys
key = sys.argv[1]
for raw in Path(".env.production").read_text(encoding="utf-8").splitlines():
    line = raw.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    name, value = line.split("=", 1)
    if name.strip() == key:
        print(value.strip().strip('"').strip("'"))
        raise SystemExit(0)
raise SystemExit(1)
PYENV
}

POSTGRES_USER="$(read_env_var POSTGRES_USER)"
POSTGRES_DB="$(read_env_var POSTGRES_DB)"
export POSTGRES_USER POSTGRES_DB

echo "rollback_target_sha=$TARGET_SHA"
git fetch origin --prune
git reset --hard "$TARGET_SHA"

if [ "$RESTORE_DATABASE_ON_ROLLBACK" = "true" ]; then
  latest_backup="$(ls -1t .deploy/backups/*.dump 2>/dev/null | head -n 1 || true)"
  if [ -n "$latest_backup" ]; then
    echo "restoring_database_backup=$latest_backup"
    ${COMPOSE[@]} up -d postgres
    for attempt in $(seq 1 30); do
      if ${COMPOSE[@]} exec -T postgres pg_isready -U "${POSTGRES_USER:?POSTGRES_USER is required}" -d "${POSTGRES_DB:?POSTGRES_DB is required}" >/dev/null 2>&1; then
        break
      fi
      sleep 2
      if [ "$attempt" -eq 30 ]; then
        echo "PostgreSQL did not become ready for rollback restore" >&2
        exit 1
      fi
    done
    cat "$latest_backup" | ${COMPOSE[@]} exec -T postgres pg_restore --clean --if-exists -U "$POSTGRES_USER" -d "$POSTGRES_DB"
  else
    echo "Database rollback restore requested but no backup file exists" >&2
    exit 1
  fi
fi

${COMPOSE[@]} config --quiet
${COMPOSE[@]} build
${COMPOSE[@]} up -d --remove-orphans
APP_DIR="$APP_DIR" scripts/healthcheck_vps.sh

echo "rollback=completed"
