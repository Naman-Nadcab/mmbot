#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="${APP_DIR:-$(pwd)}"
HEALTHCHECK_URL="${HEALTHCHECK_URL:-http://127.0.0.1/health}"
COMPOSE_FILES=(-f docker-compose.yml -f docker-compose.production.yml)
COMPOSE=(docker compose --env-file .env.production "${COMPOSE_FILES[@]}")
REQUIRED_SERVICES=(postgres redis backend nginx market-data-engine market-maker-engine)

cd "$APP_DIR"

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
REDIS_PASSWORD="$(read_env_var REDIS_PASSWORD)"
export POSTGRES_USER POSTGRES_DB REDIS_PASSWORD

check_service_running() {
  local service="$1"
  local container_id
  container_id="$(${COMPOSE[@]} ps -q "$service")"
  if [ -z "$container_id" ]; then
    echo "Service $service has no container" >&2
    return 1
  fi
  local state
  state="$(docker inspect --format '{{.State.Status}}' "$container_id")"
  if [ "$state" != "running" ]; then
    echo "Service $service is not running: $state" >&2
    return 1
  fi
  local health
  health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$container_id")"
  if [ "$health" != "none" ] && [ "$health" != "healthy" ]; then
    echo "Service $service health is $health" >&2
    return 1
  fi
  echo "service=$service state=$state health=$health"
}

for service in "${REQUIRED_SERVICES[@]}"; do
  check_service_running "$service"
done

mkdir -p .deploy/state
curl --fail --silent --show-error --max-time 10 "$HEALTHCHECK_URL" > .deploy/state/latest-health-response.json
printf 'api_health_url=%s
' "$HEALTHCHECK_URL"

${COMPOSE[@]} exec -T backend python - <<'PYENGINE'
from mmbot.core.config import default_runtime_config
from mmbot.engines.risk.engine import RiskEngine
from mmbot.engines.market_data.engine import MarketDataEngine
from mmbot.engines.market_making.engine import QuoteEngine
config = default_runtime_config()
RiskEngine(config.risk)
MarketDataEngine(config.liquidity)
QuoteEngine(config.spread, config.order_size, config.inventory)
print('engine_imports=healthy')
PYENGINE

${COMPOSE[@]} exec -T postgres pg_isready -U "${POSTGRES_USER:?POSTGRES_USER is required}" -d "${POSTGRES_DB:?POSTGRES_DB is required}"
${COMPOSE[@]} exec -T redis redis-cli -a "${REDIS_PASSWORD:?REDIS_PASSWORD is required}" ping | grep -q PONG

echo "vps_healthcheck=passed"
