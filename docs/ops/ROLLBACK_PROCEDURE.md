# Rollback Procedure

## Purpose

Restore the VPS to the last known stable release if a future deployment breaks dashboard auth, operations APIs, paper trading, or infrastructure health.

## Rollback To Last Stable Tag

Recommended stable tag:

```text
DASHBOARD_AUTH_FIXED_2026_06_11
```

Rollback command:

```bash
cd /opt/mmbot
git fetch origin --tags
git checkout DASHBOARD_AUTH_FIXED_2026_06_11
```

If a branch is preferred:

```bash
git fetch origin stable/dashboard-auth-fixed-2026-06-11
git checkout stable/dashboard-auth-fixed-2026-06-11
```

## Docker Rollback Steps

Rebuild and restart from the checked-out stable state:

```bash
docker compose --env-file .env.production -f docker-compose.yml -f docker-compose.production.yml build
docker compose --env-file .env.production -f docker-compose.yml -f docker-compose.production.yml up -d --remove-orphans
```

If the deployment script state exists, rollback may also be performed with:

```bash
APP_DIR=/opt/mmbot PREVIOUS_SHA=$(cat /opt/mmbot/.deploy/state/previous_sha) /opt/mmbot/scripts/rollback_vps.sh
```

## Verification Checklist

After rollback, verify:

```bash
docker compose --env-file .env.production -f docker-compose.yml -f docker-compose.production.yml ps
curl -s http://127.0.0.1/health
curl -s http://127.0.0.1/api/version
```

Expected:

- backend healthy
- postgres healthy
- redis healthy
- nginx healthy
- dashboard healthy
- market-data-engine healthy
- market-maker-engine healthy
- `/api/version` returns `0.2.0`

Verify authenticated operations API:

```bash
curl -s -H "Authorization: Bearer $TOKEN" http://127.0.0.1/api/operations/engines | jq .
curl -s -H "Authorization: Bearer $TOKEN" http://127.0.0.1/api/operations/canary-limits | jq .
curl -s -H "Authorization: Bearer $ADMIN_TOKEN" http://127.0.0.1/api/admin/kill-switch/status | jq .
```

Expected:

- operations endpoints return HTTP 200 with valid operations token
- admin kill-switch status returns HTTP 200 with platform admin token
- dashboard loads and authenticated panels populate

## Rollback Abort Conditions

Stop and escalate if:

- PostgreSQL fails health check.
- Redis fails health check.
- Backend fails startup.
- Dashboard auth still fails after rollback.
- Engine containers restart repeatedly.
- Kill switch status cannot be read with admin token.
