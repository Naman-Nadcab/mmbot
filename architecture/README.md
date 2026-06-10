# Enterprise Architecture

This is a design foundation, not an implementation of trading behavior.

## Goals

- Keep risk controls independent from strategy execution.
- Normalize market data before downstream use.
- Isolate exchange-specific behavior behind adapters.
- Make privileged actions auditable.
- Support low-latency paths without sacrificing safety.

## Logical Model

```text
Admin Dashboard -> API Gateway -> Auth/RBAC
       |               |
Websocket Gateway      +--> Alert Engine / Analytics / Operations
       |               |
       +-------- Event and Command Bus --------+
                    |          |               |
            Market Data -> Market Making <-> Risk Engine
                    |          |               |
             Exchange Adapters |        Inventory Engine
                    |          |
              External Venues  +-> Liquidity Engine

PostgreSQL: durable state and audit logs
Redis: cache, queues, pub/sub, rate counters, ephemeral state
Monitoring: metrics, logs, health, alerts
```

## Component Boundaries

### Market Making Engine
Owns future quote lifecycle orchestration. It must never bypass risk checks, kill switch state, or audit logging. No algorithms are implemented in this bootstrap.

### Inventory Engine
Tracks inventory by account, venue, asset, and pair. Produces exposure and imbalance views and persists snapshots.

### Risk Engine
Evaluates limits, circuit breaker state, kill switch state, and emergency shutdown. Emits risk events and alerts.

### Liquidity Engine
Computes spread, order book depth, venue quality, slippage, and imbalance metrics.

### Market Data Engine
Ingests and normalizes future venue data. Publishes validated state to Redis and persists selected records to PostgreSQL.

### Exchange Adapters
Encapsulate venue REST, WebSocket, signing, throttling, and error behavior. Not implemented.

### Alert Engine
Routes risk, operational, health, and security alerts. Telegram configuration is supported by environment variables.

### Analytics Engine
Provides PnL, volatility, liquidity, and execution-quality analytics from durable state.

### Admin Dashboard
Provides operational views and privileged controls. Requires RBAC, audit logging, IP restrictions, and MFA before production.

### Websocket Gateway
Streams health, alerts, risk, inventory, analytics, and market summaries with authentication and authorization.

### API Gateway
Handles authentication, authorization, rate limiting, request validation, correlation IDs, and audit context.

## Runtime Safety Rules

- Risk Engine availability is required before order placement is enabled.
- Kill switch state is checked before future external order actions.
- Exchange credentials are decrypted only in the minimum signing boundary.
- Administrative and automated trading state changes emit audit logs.
