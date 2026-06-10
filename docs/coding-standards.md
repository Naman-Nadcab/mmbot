# Coding Standards

These standards apply once runtime code is introduced.

## General

- Prefer explicit, testable code for risk-sensitive paths.
- Use typed interfaces for external contracts and monetary quantities.
- Treat exchange responses, market data, and user input as untrusted.
- Separate orchestration from decision logic.

## Financial Data

- Do not use binary floating point for money, prices, sizes, or PnL.
- Preserve exchange-native identifiers and timestamps.
- Store normalized symbols separately from venue-native symbols.
- Record state transitions for orders, positions, risk events, and admin controls.

## Errors and Reliability

- Fail closed for auth, authorization, and risk-control failures.
- Include correlation IDs in logs, metrics, and audit records.
- Never log secrets, API keys, signatures, JWTs, or raw credentials.
- Use idempotency keys for order and administrative commands.
- Bound retries and protect external side effects with circuit breakers.
