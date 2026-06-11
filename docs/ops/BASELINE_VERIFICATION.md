# Baseline Verification

## Operations Endpoints Expected Status

With a valid operations token:

```text
GET /api/operations/engines              -> 200
GET /api/operations/infrastructure      -> 200
GET /api/operations/exchanges           -> 200
GET /api/operations/orders              -> 200
GET /api/operations/trades              -> 200
GET /api/operations/positions           -> 200
GET /api/operations/inventory           -> 200
GET /api/operations/pnl                 -> 200
GET /api/operations/risk-events         -> 200
GET /api/operations/reconciliation      -> 200
GET /api/operations/canary-limits       -> 200
```

With a valid admin token:

```text
GET  /api/admin/kill-switch/status      -> 200
POST /api/admin/kill-switch/enable      -> 200
POST /api/admin/kill-switch/disable     -> 200
```

## Dashboard Expected Status

- Dashboard loads from `/`.
- API pill shows healthy after `/api/health` succeeds.
- JWT token can be saved in the dashboard token field.
- Authenticated panels populate from `/api/operations/*`.
- WebSocket connects to `/api/ws/operations?token=<jwt>`.
- Runtime event log receives operation events.

## Engine Health Expectations

Redis keys expected:

```text
engine:health:market-data-engine
engine:health:market-maker-engine
```

Expected status:

```text
status=healthy
runtime metrics present
heartbeat timestamp updating
```

## Infrastructure Expectations

- PostgreSQL healthy.
- Redis healthy.
- Backend healthy.
- NGINX running.
- Dashboard running.
- Prometheus running.

## Kill Switch Expectations

- Default state is inactive unless operator enabled it.
- `GET /api/admin/kill-switch/status` returns current Redis-backed state.
- Enabling kill switch writes `risk:kill_switch` in Redis.
- Market maker runtime stops creating new orders while kill switch is active.
- Existing reconciliation and health checks continue.

## Reconciliation Expectations

- `GET /api/operations/reconciliation` returns status and run counters.
- Healthy state shows `status=ok` when runs are greater than zero and mismatches are zero.
- Mismatch counts must be investigated before canary promotion.
