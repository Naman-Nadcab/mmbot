# API Foundation

API gateway and WebSocket gateway planning artifacts. The current contract is skeletal and limited to health/readiness/version plus planned administrative controls.

## Gateway Principles

- Authenticate every non-public request.
- Authorize state-changing requests through RBAC.
- Rate limit by principal, IP, and route sensitivity.
- Propagate correlation IDs.
- Validate payloads at the edge.
- Fail closed for auth, authorization, and risk-control errors.

## Planned Domains

- Health and readiness.
- Auth, sessions, token refresh, MFA.
- Admin controls and bot configuration.
- Risk events, breakers, kill switch status.
- Inventory, positions, PnL, liquidity, volatility.
- Alert routing and acknowledgement.

No WebSocket or API business logic is implemented in this bootstrap.
