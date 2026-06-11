# Canary Readiness Checklist

## Required Before Canary

- [ ] Stable state tag exists.
- [ ] Dashboard auth works.
- [ ] Operations APIs return HTTP 200 with valid token.
- [ ] Kill switch status endpoint works.
- [ ] Kill switch enable/disable endpoints work with admin token.
- [ ] Market maker respects kill switch.
- [ ] Canary limits are configured and visible.
- [ ] Paper trading orders are generated.
- [ ] Paper fills are generated.
- [ ] Positions update.
- [ ] Inventory snapshots update.
- [ ] Reconciliation runs.
- [ ] No critical reconciliation mismatch exists.
- [ ] Alert channel is verified.
- [ ] Operator is assigned.
- [ ] Rollback procedure is open and ready.

## Canary Limits To Confirm

- [ ] `MAX_CANARY_NOTIONAL`
- [ ] `MAX_CANARY_POSITION`
- [ ] max daily loss
- [ ] max open orders
- [ ] max order notional
- [ ] max inventory exposure

## Go / No-Go

Canary is NO-GO if any of the following are true:

- operations auth fails
- kill switch fails
- reconciliation mismatch is critical
- engine containers restart repeatedly
- Redis or PostgreSQL is degraded
- dashboard cannot show runtime state
