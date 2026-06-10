#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="${APP_DIR:-$(pwd)}"
DEPLOY_BRANCH="${DEPLOY_BRANCH:-main}"
SKIP_GIT_UPDATE="${SKIP_GIT_UPDATE:-false}"
PREVIOUS_SHA="${PREVIOUS_SHA:-}"
RESTORE_DATABASE_ON_ROLLBACK="${RESTORE_DATABASE_ON_ROLLBACK:-false}"
HEALTHCHECK_URL="${HEALTHCHECK_URL:-http://127.0.0.1/health}"
COMPOSE_FILES=(-f docker-compose.yml -f docker-compose.production.yml)
COMPOSE=(docker compose --env-file .env.production "${COMPOSE_FILES[@]}")

cd "$APP_DIR"
mkdir -p .deploy/backups .deploy/state

if [ -z "$PREVIOUS_SHA" ]; then
  PREVIOUS_SHA="$(git rev-parse HEAD)"
fi
printf '%s
' "$PREVIOUS_SHA" > .deploy/state/previous_sha

rollback_on_error() {
  local exit_code=$?
  echo "deployment_failed exit_code=$exit_code previous_sha=$PREVIOUS_SHA" >&2
  if [ -x scripts/rollback_vps.sh ]; then
    PREVIOUS_SHA="$PREVIOUS_SHA" RESTORE_DATABASE_ON_ROLLBACK="$RESTORE_DATABASE_ON_ROLLBACK" APP_DIR="$APP_DIR" scripts/rollback_vps.sh "$PREVIOUS_SHA" || true
  fi
  exit "$exit_code"
}
trap rollback_on_error ERR

if [ ! -f .env.production ]; then
  echo "Missing .env.production in $APP_DIR" >&2
  exit 1
fi

if [ "$SKIP_GIT_UPDATE" != "true" ]; then
  git fetch origin "$DEPLOY_BRANCH"
  git reset --hard "origin/$DEPLOY_BRANCH"
fi

CURRENT_SHA="$(git rev-parse HEAD)"
printf '%s
' "$CURRENT_SHA" > .deploy/state/current_sha

echo "deploy_previous_sha=$PREVIOUS_SHA"
echo "deploy_current_sha=$CURRENT_SHA"

python3 scripts/validate_env.py --file .env.production
${COMPOSE[@]} config --quiet

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

${COMPOSE[@]} up -d postgres redis

for service in postgres redis; do
  container_id="$(${COMPOSE[@]} ps -q "$service")"
  if [ -z "$container_id" ]; then
    echo "Dependency service $service did not create a container" >&2
    exit 1
  fi
  for attempt in $(seq 1 60); do
    health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container_id")"
    if [ "$health" = "healthy" ] || [ "$health" = "running" ]; then
      echo "dependency=$service health=$health"
      break
    fi
    sleep 2
    if [ "$attempt" -eq 60 ]; then
      echo "Dependency service $service did not become healthy" >&2
      exit 1
    fi
  done
done

backup_file=".deploy/backups/postgres-${CURRENT_SHA}-$(date -u +%Y%m%dT%H%M%SZ).dump"
${COMPOSE[@]} exec -T postgres pg_dump -Fc -U "${POSTGRES_USER:?POSTGRES_USER is required}" "${POSTGRES_DB:?POSTGRES_DB is required}" > "$backup_file"
chmod 600 "$backup_file"
printf '%s
' "$backup_file" > .deploy/state/latest_backup

echo "database_backup=$backup_file"

${COMPOSE[@]} build
${COMPOSE[@]} run --rm backend python -m mmbot.cli.database migrate
${COMPOSE[@]} run --rm backend python -m mmbot.cli.database seed
${COMPOSE[@]} up -d --remove-orphans
APP_DIR="$APP_DIR" HEALTHCHECK_URL="$HEALTHCHECK_URL" scripts/healthcheck_vps.sh

trap - ERR
echo "deployment=completed sha=$CURRENT_SHA"
