# Stable State Report - 2026-06-11

## Release Tag Recommendation

Recommended tag:

```bash
git tag -a DASHBOARD_AUTH_FIXED_2026_06_11 -m "Stable dashboard auth and paper trading baseline - 2026-06-11"
git push origin DASHBOARD_AUTH_FIXED_2026_06_11
```

## Stable Commit

Current stable commit:

```text
e260184 Log backend auth request evidence
```

Recommended stable branch:

```bash
git checkout -b stable/dashboard-auth-fixed-2026-06-11
git push -u origin stable/dashboard-auth-fixed-2026-06-11
```

## Commit Hashes Involved

```text
e260184 Log backend auth request evidence
e7c7b52 Use read-only operations auth for dashboard status
9d6775c Instrument JWT authentication diagnostics
94bcf27 Instrument dashboard request evidence
9a3c1c4 Resync dashboard auth state before requests
9e1b402 Add dashboard JWT diagnostics
d80f1d1 Fix dashboard protected endpoint token handling
5b37a98 Add canary kill switch controls
cd438ab Close operations dashboard monitoring gaps
f851971 Improve operations dashboard monitoring widgets
f3c6961 Add real-time operations monitoring dashboard data
e7a6c2c Add operations monitoring APIs and dashboard wiring
```

## What Was Fixed

- Operations dashboard connects to protected backend APIs using JWT.
- Dashboard normalizes stored/pasted JWT values before REST and WebSocket use.
- Backend logs JWT decode and authorization evidence for REST and WebSocket paths.
- Operations API routes expose runtime state for dashboard panels.
- Kill switch status and admin kill-switch controls exist.
- Canary limits are exposed read-only.
- Market maker runtime respects Redis-backed kill switch before creating new orders.
- Paper trading runtime is operational and observable through Redis/DB-backed endpoints.

## Root Cause Timeline

1. Dashboard initially displayed unavailable/unauthorized states because protected operations endpoints required JWT.
2. JWT propagation was added to dashboard REST and WebSocket paths.
3. Browser-side evidence showed Authorization headers were present.
4. Backend diagnostics were added to verify token metadata, roles, permissions, and decode results.
5. Dashboard startup was adjusted to use read-only operations endpoints instead of admin-only endpoints where appropriate.
6. Final state: dashboard auth, operations APIs, WebSocket auth, kill switch status, canary limits, and paper trading monitoring are considered stable.

## Final Verified State

Known-good state as reported by operator:

- Dashboard auth working.
- Operations APIs returning HTTP 200.
- JWT validation working.
- WebSocket auth working.
- Kill switch working.
- Canary limits working.
- Paper trading working.
- Backend, PostgreSQL, Redis, NGINX, dashboard, market-data-engine, and market-maker-engine healthy.

## Known Remaining Work

- Create and push the recommended stable tag.
- Create and push the recommended stable branch.
- Perform longer VPS soak before canary.
- Complete sandbox exchange certification before live trading.
- Add formal operator runbook for canary promotion.
- Add final access-control policy for who may generate JWTs.
- Rotate temporary diagnostic logging out after the stabilization window.

## Deployment Commands

Deploy current stable main:

```bash
cd /opt/mmbot
git fetch origin main
git reset --hard origin/main
docker compose --env-file .env.production -f docker-compose.yml -f docker-compose.production.yml build backend dashboard market-data-engine market-maker-engine
docker compose --env-file .env.production -f docker-compose.yml -f docker-compose.production.yml up -d --remove-orphans
```

Check status:

```bash
docker compose --env-file .env.production -f docker-compose.yml -f docker-compose.production.yml ps
curl -s http://127.0.0.1/health
curl -s http://127.0.0.1/api/version
```

## Rollback Procedure

See:

```text
docs/ops/ROLLBACK_PROCEDURE.md
```
