# Deployment and Certification Playbook

This playbook is the production validation gate for the institutional market making platform. It is documentation-only and does not authorize live trading by itself.

## Freeze Rules

- No new trading features during certification.
- No dashboard work during certification.
- No cosmetic work during certification.
- Only deployment validation, exchange sandbox certification, shadow/canary procedures, audit procedures, rollback, and emergency shutdown are in scope.
- Live trading remains blocked until every required checklist item is complete, evidenced, reviewed, and approved.

## Certification Evidence Standard

Every checklist item must produce evidence before it can be marked complete:

- Operator name and timestamp.
- Environment name and deployed commit SHA.
- Venue/account identifier where applicable.
- Command or procedure executed.
- Logs, screenshots, structured output, or database/audit record IDs.
- Expected result and observed result.
- PASS/FAIL decision.
- Reviewer approval for production-impacting steps.

## Required Environments

| Environment | Purpose | External Order Execution |
| --- | --- | --- |
| Local | Developer validation only | Prohibited |
| CI | Automated test/compile validation | Prohibited |
| Sandbox | Exchange credentialed certification | Sandbox only |
| Shadow | Real market data, no external orders | Prohibited |
| Paper | Simulated order lifecycle and PnL | Prohibited |
| Canary | Limited real capital and strict limits | Allowed after approval |
| Production | Full deployment target | Allowed only after all gates pass |

---

# Phase A: Deployment Certification

## A1. Production Deployment Checklist

Preconditions:

- [ ] Release branch is approved and immutable.
- [ ] Commit SHA is recorded.
- [ ] Production `.env.production` values are injected by secret manager or deployment platform.
- [ ] No production secret exists in Git history or committed files.
- [ ] `python3 scripts/validate_env.py --file .env.production --allow-template-values` passes for templates.
- [ ] Real production environment validation passes without template values.
- [ ] Database migration plan is approved.
- [ ] Rollback plan is approved.
- [ ] Emergency shutdown contacts are confirmed.

Deployment procedure:

1. Record release metadata.
2. Validate environment variables.
3. Pull the approved image or build from approved commit SHA.
4. Run database backup before migration.
5. Apply migrations in maintenance or controlled rollout window.
6. Start services in dependency order: PostgreSQL, Redis, backend, engines, NGINX, monitoring.
7. Verify health endpoints and dependency health.
8. Verify structured logs are shipping.
9. Verify audit records are written.
10. Keep platform in read-only mode until exchange and canary gates pass.

PASS criteria:

- [ ] All services report healthy or intentionally disabled.
- [ ] Database migration succeeded.
- [ ] Redis health check succeeded.
- [ ] Backend health/readiness succeeded.
- [ ] Logs, metrics, and audit chain are operational.
- [ ] Read-only mode is active by default.

FAIL criteria:

- Any required dependency fails health checks.
- Any migration fails or produces unapproved schema drift.
- Any secret is missing, exposed, or incorrectly scoped.
- Audit logging is unavailable.

## A2. Docker Deployment Checklist

Build validation:

- [ ] Docker engine version is recorded.
- [ ] Build uses approved commit SHA.
- [ ] `docker compose --env-file .env.production -f docker-compose.yml -f docker-compose.production.yml config` succeeds.
- [ ] Images build without network or dependency errors.
- [ ] Runtime containers use non-root users where configured.
- [ ] Container health checks are enabled.
- [ ] Image vulnerability scan is reviewed.

Runtime validation:

- [ ] `backend` starts and exposes `/health`, `/ready`, and `/version`.
- [ ] `market-maker-engine` starts with environment validation.
- [ ] `market-data-engine` starts with environment validation.
- [ ] `postgres` health check passes.
- [ ] `redis` health check passes.
- [ ] `nginx` routes traffic to backend.
- [ ] `monitoring` starts and loads configuration.

Evidence:

- `docker compose ps`
- `docker compose logs --since 10m`
- Health endpoint responses.
- Image digests.
- Vulnerability scan report.

## A3. PostgreSQL Deployment Checklist

Pre-migration:

- [ ] Production backup completed and restore-tested reference exists.
- [ ] Current schema version recorded.
- [ ] Pending migrations reviewed.
- [ ] Locking risk assessed.
- [ ] Rollback or compensating migration documented.

Migration:

- [ ] Apply migrations using approved migration runner.
- [ ] Confirm `schema_migrations` has expected version.
- [ ] Confirm RBAC seed state exists.
- [ ] Confirm indexes exist for orders, trades, market data, audit logs, risk events, and configs.
- [ ] Confirm encrypted exchange credential columns exist and contain no plaintext.

Health and performance:

- [ ] Connection pool opens and closes cleanly.
- [ ] `SELECT 1` health check passes.
- [ ] Slow query logging threshold configured.
- [ ] Backup schedule configured.
- [ ] Restore procedure tested in non-production.

## A4. Redis Deployment Checklist

- [ ] Redis version recorded.
- [ ] Authentication required in production.
- [ ] Network access restricted to platform services.
- [ ] Persistence policy is documented.
- [ ] Memory limit and eviction policy are configured.
- [ ] Health check returns `PONG`.
- [ ] Cache manager read/write verified.
- [ ] Pub/Sub publish/subscribe verified.
- [ ] Distributed lock acquire/release verified.
- [ ] Rate-limit counter behavior verified.
- [ ] Session create/read/revoke verified.

Failure validation:

- [ ] Redis disconnect produces degraded health and no unsafe order execution.
- [ ] Redis reconnect restores cache/pubsub/lock operations.
- [ ] Stale locks expire within configured TTL.

## A5. NGINX Deployment Checklist

- [ ] TLS termination configured for production.
- [ ] HTTP redirects to HTTPS.
- [ ] Security headers configured.
- [ ] Server tokens disabled.
- [ ] Access logs use structured format.
- [ ] Rate limiting is enabled for API routes.
- [ ] Backend proxy headers are set.
- [ ] Admin routes are network restricted.
- [ ] WebSocket upgrade headers are configured when WebSocket gateway is enabled.
- [ ] Health route is reachable through NGINX.

Evidence:

- NGINX rendered configuration.
- TLS certificate metadata.
- `curl -I` output for health and security headers.
- Access log sample.

## A6. Security Deployment Checklist

- [ ] JWT secret length and rotation policy verified.
- [ ] Admin tokens require RBAC role/permission.
- [ ] MFA policy is enabled for privileged operations.
- [ ] IP allowlists are configured for admin paths.
- [ ] Request-level rate limiting is enabled.
- [ ] Audit chain signing key is sourced from secret manager.
- [ ] Tamper-evident logs are written to durable storage.
- [ ] No secret material appears in logs.
- [ ] Exchange API keys are scoped to required permissions only.
- [ ] Exchange withdrawal permissions are disabled unless explicitly approved.
- [ ] Emergency contacts and escalation routes are tested.

## A7. Vault Deployment Checklist

- [ ] Vault or equivalent provider is deployed and reachable from runtime services.
- [ ] Vault authentication method is configured for service identities.
- [ ] Secret paths are separated by scope: exchange, alerting, JWT, database.
- [ ] Policies enforce least privilege per service.
- [ ] Secret versioning is enabled.
- [ ] Rotation procedure is documented and tested.
- [ ] Audit device/logging is enabled in Vault.
- [ ] Break-glass access is documented and approved.
- [ ] Runtime services can fetch required secrets without exposing values.
- [ ] Secret revocation test completed in non-production.

---

# Phase B: Exchange Sandbox Certification

Each venue must pass the same workflow in sandbox before canary. Use the venue checklist in `docs/certification/live-order-lifecycle-checklists.md` as the sign-off artifact.

## Common Sandbox Preconditions

- [ ] Sandbox credentials are provisioned.
- [ ] Credentials are stored in Vault or approved secret store.
- [ ] Withdrawal permission is disabled.
- [ ] API key permissions are limited to read/trade only.
- [ ] Sandbox account has sufficient test balances.
- [ ] Venue rate limits and account tier are documented.
- [ ] Symbol selected for certification is liquid in sandbox.
- [ ] Precision, tick size, lot size, and minimum notional are fetched from venue metadata.
- [ ] Platform launch mode is not live; use sandbox certification mode.

## Common Venue Certification Procedure

Run this procedure independently for Coinstore, MEXC, Gate, Bitmart, Kucoin, and Binance.

### 1. Authentication

Steps:

1. Load venue credentials from secret provider.
2. Create signed private REST request to account endpoint.
3. Verify response code and signature acceptance.

PASS:

- [ ] Private endpoint accepts signed request.
- [ ] No credential is logged.
- [ ] Failed signature test returns normalized authentication error.

### 2. Balance Retrieval

Steps:

1. Request account balances.
2. Normalize free, locked/reserved, and total balances.
3. Compare against venue UI or sandbox account view.

PASS:

- [ ] All expected assets are returned.
- [ ] Free + reserved equals total within tolerance.
- [ ] Internal balance record is generated.

### 3. Order Placement

Steps:

1. Fetch symbol precision metadata.
2. Submit a post-only or conservative limit order outside immediate execution.
3. Validate request respects price tick, lot size, and min notional.

PASS:

- [ ] Venue accepts order.
- [ ] Client order ID is preserved.
- [ ] Exchange order ID is captured.
- [ ] Order acknowledgement is normalized.

### 4. Order Acknowledgement

Steps:

1. Record acknowledgement payload.
2. Confirm status maps to internal state.
3. Confirm audit log contains order intent and exchange response metadata without secrets.

PASS:

- [ ] Acknowledgement latency is recorded.
- [ ] Internal order status is `open`, `new`, or venue-equivalent.
- [ ] Audit record exists.

### 5. Partial Fills

Steps:

1. Place an order likely to partially fill in sandbox.
2. Observe REST status and WebSocket execution report.
3. Verify cumulative filled quantity and remaining quantity.

PASS:

- [ ] Partial fill is detected.
- [ ] Fill quantity is reconciled.
- [ ] Fees are captured when venue provides them.

### 6. Full Fills

Steps:

1. Place a small order expected to fully fill.
2. Verify REST status reaches filled state.
3. Verify WebSocket execution update matches REST.

PASS:

- [ ] Filled status is normalized.
- [ ] Average fill price is captured.
- [ ] Balance and position changes reconcile.

### 7. Order Cancellation

Steps:

1. Place a resting order.
2. Submit cancellation by exchange order ID or client order ID.
3. Query status after cancellation.

PASS:

- [ ] Cancel request succeeds.
- [ ] Final state is cancelled.
- [ ] No stale open order remains internally.

### 8. Order Replacement

Steps:

1. Place a resting order.
2. Submit native replace if venue supports it; otherwise execute cancel-then-place workflow.
3. Verify old order is cancelled and replacement is acknowledged.

PASS:

- [ ] Replacement workflow preserves audit trail.
- [ ] Only one active intended order remains.
- [ ] Replacement respects precision and min notional.

### 9. WebSocket Execution Updates

Steps:

1. Subscribe to user-data/order/execution channels.
2. Place, fill, cancel, and replace orders.
3. Compare WebSocket events to REST status.

PASS:

- [ ] Order updates arrive.
- [ ] Balance updates arrive where venue supports them.
- [ ] Execution reports arrive.
- [ ] Sequence handling does not detect unrecovered gaps.

### 10. Disconnect Recovery

Steps:

1. Establish WebSocket subscription.
2. Force disconnect.
3. Confirm reconnect, resubscription, and snapshot recovery.

PASS:

- [ ] Reconnect occurs automatically.
- [ ] Subscriptions are restored.
- [ ] Orderbook snapshot recovery occurs after detected gap.
- [ ] User-data state is reconciled after reconnect.

### 11. Rate-Limit Behavior

Steps:

1. Execute controlled request bursts below venue limit.
2. Execute controlled bursts at local limit boundary.
3. Verify local limiter blocks or slows excess requests.
4. Verify remote 429/limit responses normalize to retryable errors.

PASS:

- [ ] No uncontrolled rate-limit breach occurs.
- [ ] Retry behavior is bounded.
- [ ] Rate-limit errors do not trigger duplicate orders.

### 12. Precision Handling

Steps:

1. Submit intentionally over-precise price and quantity in dry-run validator.
2. Submit adjusted order in sandbox.
3. Confirm accepted order uses venue-compliant values.

PASS:

- [ ] Price is rounded down to tick size.
- [ ] Quantity is rounded down to lot size.
- [ ] Invalid quantity below minimum is rejected before exchange submission.

### 13. Minimum Notional Handling

Steps:

1. Attempt order below minimum notional in validator.
2. Confirm local rejection.
3. Submit order at or above minimum notional.

PASS:

- [ ] Below-minimum order is blocked locally.
- [ ] Valid order is accepted by venue.

## Venue-Specific Certification Notes

### Coinstore

- Confirm exact private REST signing headers in sandbox.
- Confirm user-data WebSocket authentication flow.
- Confirm symbol format expected by order and market data endpoints.

### MEXC

- Confirm Binance-like query signing compatibility.
- Confirm listen/user stream behavior for spot private updates.
- Confirm precision fields from exchange info.

### Gate

- Confirm Gate v4 signature canonical string.
- Confirm currency pair underscore symbol format.
- Confirm order status endpoint path by exchange order ID.

### Bitmart

- Confirm memo/passphrase handling for signatures.
- Confirm WebSocket private subscription authentication requirements.
- Confirm batch cancellation endpoint behavior.

### Kucoin

- Confirm passphrase signing version.
- Acquire WebSocket bullet token where required.
- Confirm private channel subscription requirements.

### Binance

- Confirm account permissions and IP restrictions.
- Confirm listen-key lifecycle and keepalive process.
- Confirm cancel-replace mode behavior in sandbox/testnet.

---

# Phase C: Shadow Mode Certification

Shadow mode proves the strategy and operations stack without external order execution.

## C1. Market Data Only Mode

- [ ] Connect market data WebSockets only.
- [ ] Persist ticker/orderbook/trade samples.
- [ ] Validate spread, volatility, liquidity, and imbalance metrics.
- [ ] Confirm no private order endpoints are called.

PASS: market data flows continuously and no order intent is emitted externally.

## C2. Signal Generation Mode

- [ ] Enable strategy decision generation.
- [ ] Record regime, spread, skew, and liquidity decisions.
- [ ] Compare decisions against market conditions.
- [ ] Confirm all outputs are logged and auditable.

PASS: signals are deterministic for replayed inputs and remain within configured bounds.

## C3. Decision Logging Mode

- [ ] Log every would-be order decision.
- [ ] Include reason, risk decision, market snapshot, inventory snapshot, and config version.
- [ ] Verify logs contain no secrets.

PASS: every decision can be reconstructed from logs and persisted data.

## C4. Order Simulation Mode

- [ ] Convert decisions into simulated order lifecycle events.
- [ ] Simulate acknowledgements, partial fills, full fills, cancellations, and replacements.
- [ ] Reconcile simulated order state.

PASS: simulated order state has no unresolved inconsistencies.

## C5. PnL Simulation Mode

- [ ] Run PnL simulation over replayed or live-shadow market events.
- [ ] Include fees and slippage assumptions.
- [ ] Produce realized/unrealized PnL report.

PASS: PnL report reconciles with simulated fills and inventory.

## C6. Inventory Simulation Mode

- [ ] Track simulated balances and positions.
- [ ] Validate target inventory and skew behavior.
- [ ] Trigger alerts on simulated inventory threshold breaches.

PASS: inventory limits, alerts, and neutralization decisions behave as configured.

---

# Phase D: Canary Mode Certification

Canary mode is the first permitted external execution stage. It must use strict limits, real-time monitoring, and immediate rollback authority.

## Common Canary Requirements

- [ ] Canary mode explicitly enabled.
- [ ] Read-only, shadow, paper, and dry-run modes tested before canary.
- [ ] Kill switch tested immediately before launch.
- [ ] Emergency shutdown runbook is open and assigned.
- [ ] Reconciliation interval is configured.
- [ ] Alerts are verified on all configured channels.
- [ ] Daily loss limit is configured below approved capital loss tolerance.
- [ ] Max order count is configured.
- [ ] Max position and inventory limits are configured.
- [ ] Operator is actively monitoring during entire canary window.

## D1. $1,000 Capital Canary

Recommended limits:

- Maximum position notional: $250
- Maximum inventory notional: $500
- Maximum single order notional: $25
- Maximum daily loss: $25
- Maximum open orders: 10
- Maximum daily order count: 100
- Kill switch trigger: any unreconciled mismatch over $5 or any critical venue error burst

PASS:

- [ ] No critical reconciliation mismatches.
- [ ] No unhandled venue errors.
- [ ] No daily loss breach.
- [ ] Kill switch can be activated and verified.

## D2. $2,500 Capital Canary

Recommended limits:

- Maximum position notional: $625
- Maximum inventory notional: $1,250
- Maximum single order notional: $50
- Maximum daily loss: $50
- Maximum open orders: 15
- Maximum daily order count: 250
- Kill switch trigger: any unreconciled mismatch over $10 or repeated critical venue errors

PASS:

- [ ] $1,000 canary passed first.
- [ ] Reconciliation remains clean.
- [ ] Order lifecycle metrics are stable.
- [ ] Alerting and audit trail are complete.

## D3. $5,000 Capital Canary

Recommended limits:

- Maximum position notional: $1,250
- Maximum inventory notional: $2,500
- Maximum single order notional: $100
- Maximum daily loss: $100
- Maximum open orders: 25
- Maximum daily order count: 500
- Kill switch trigger: any unreconciled mismatch over $25, daily loss breach, or dependency failure during active orders

PASS:

- [ ] $2,500 canary passed first.
- [ ] No unresolved reconciliation defects.
- [ ] No abnormal cancel/fill/manipulation alerts.
- [ ] No order duplication across reconnect/retry events.

## Canary Promotion Rule

Do not increase capital unless all lower canary levels passed with:

- Zero critical reconciliation mismatches.
- Zero unresolved order lifecycle defects.
- Zero kill-switch failures.
- Zero missing audit records.
- Reviewer approval.

---

# Phase E: Production Audit

## E1. Pre-Launch Checklist

- [ ] All deployment checklists pass.
- [ ] All exchange sandbox certifications pass.
- [ ] Shadow mode passes.
- [ ] Paper mode passes.
- [ ] Canary level approval is signed.
- [ ] Vault/secret manager is operational.
- [ ] MFA enforcement is active.
- [ ] IP allowlists are active.
- [ ] Alert delivery is verified.
- [ ] Audit chain is verified.
- [ ] Kill switch tested.
- [ ] Emergency shutdown tested.
- [ ] Rollback tested.
- [ ] On-call operators assigned.

## E2. Launch-Day Checklist

- [ ] Confirm deployed commit SHA.
- [ ] Confirm active launch mode and capital level.
- [ ] Confirm exchange connectivity.
- [ ] Confirm market data freshness.
- [ ] Confirm balances before trading.
- [ ] Confirm no stale open orders.
- [ ] Confirm risk limits loaded.
- [ ] Confirm reconciliation baseline.
- [ ] Start canary with operator monitoring.
- [ ] Review first order acknowledgement.
- [ ] Review first fill and reconciliation.
- [ ] Review first alert test.

## E3. Incident Checklist

- [ ] Classify incident severity.
- [ ] Assign incident commander.
- [ ] Freeze new deployments.
- [ ] Capture current mode, positions, open orders, balances, and PnL.
- [ ] Activate kill switch if capital or execution integrity is at risk.
- [ ] Stop order creation.
- [ ] Cancel open orders where safe.
- [ ] Snapshot audit logs and reconciliation state.
- [ ] Notify stakeholders through configured alert routes.
- [ ] Record timeline and remediation actions.

## E4. Rollback Checklist

- [ ] Confirm rollback target commit/image.
- [ ] Stop external order creation.
- [ ] Cancel or preserve open orders according to incident commander decision.
- [ ] Snapshot database state.
- [ ] Roll back application containers.
- [ ] Confirm migrations do not require data rollback; apply compensating migration if approved.
- [ ] Verify health/readiness.
- [ ] Verify reconciliation after rollback.
- [ ] Keep canary disabled until post-rollback review passes.

## E5. Emergency Shutdown Checklist

- [ ] Activate global kill switch.
- [ ] Stop all order generation.
- [ ] Stop all replacement loops.
- [ ] Cancel all open orders by venue where safe.
- [ ] Disable venue adapters if required.
- [ ] Snapshot balances, positions, open orders, fills, PnL, risk events, and audit logs.
- [ ] Confirm no new external order requests are emitted.
- [ ] Confirm alert escalation reached operators.
- [ ] Preserve logs for post-incident review.
- [ ] Require written approval before recovery.

---

# Final Production Validation Gate

The system may proceed to limited canary only if every statement below is true:

- [ ] Deployment certification passed.
- [ ] PostgreSQL and Redis production checks passed.
- [ ] NGINX, security, and Vault checks passed.
- [ ] Every target exchange passed sandbox certification.
- [ ] Shadow mode passed.
- [ ] Paper mode passed.
- [ ] Reconciliation passed under simulated and sandbox conditions.
- [ ] Failover and stress testing passed in production-like infrastructure.
- [ ] Capital protection review passed.
- [ ] Launch-day operators approved the canary plan.

If any item fails, launch status is **NO-GO**.
