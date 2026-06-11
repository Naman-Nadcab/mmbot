# Live Readiness Checklist

## Required Before Live Trading

- [ ] Canary checklist passes.
- [ ] Exchange sandbox certification passes for target venue.
- [ ] Private REST order placement certified.
- [ ] Order cancellation certified.
- [ ] Order replacement certified.
- [ ] User-data WebSocket certified.
- [ ] Balance synchronization certified.
- [ ] Position synchronization certified.
- [ ] Reconciliation against exchange state certified.
- [ ] Kill switch tested against live-mode runtime gate.
- [ ] Capital limits approved.
- [ ] Incident response plan assigned.
- [ ] Rollback plan approved.
- [ ] Alert escalation verified.

## Live Trading NO-GO Conditions

- any untested exchange adapter
- missing exchange permissions review
- withdrawal permission enabled without approval
- reconciliation mismatch unresolved
- kill switch unavailable
- operations dashboard unavailable
- unauthenticated operations endpoint exposed
- Redis/PostgreSQL unhealthy
- no operator on watch
