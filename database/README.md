# Database Design

The canonical schema draft is `database/schema.sql`.

## Principles

- PostgreSQL is the durable source of truth.
- Redis is for short-lived cache, pub/sub, queues, and counters.
- Critical entities include timestamps and status fields.
- Order, trade, risk, alert, and administrative activity must be auditable.
- Exchange-native identifiers are preserved with normalized identifiers.
- Secrets are stored only as ciphertext and key references.

## Core Tables

`users`, `roles`, `permissions`, `user_roles`, `role_permissions`, `bot_configs`, `exchange_accounts`, `trading_pairs`, `orders`, `trades`, `positions`, `inventory_snapshots`, `market_data`, `risk_events`, `alerts`, `audit_logs`, `system_health`, `pnl_history`, `liquidity_metrics`, `volatility_metrics`.

## Migration Strategy

Before runtime implementation, add a migration tool such as Alembic, Flyway, or Liquibase. Production migrations need forward scripts, rollback or compensating actions, lock assessment, and index strategy.
