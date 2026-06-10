# Development Standards

## Principles

1. Safety before throughput.
2. Explicit service and data boundaries.
3. Configuration through environment variables or secret stores.
4. Reproducible deployments, migrations, and rollbacks.
5. Observability by default.

## Review Expectations

- Keep branches focused on one logical change.
- Require review for database, security, deployment, and risk-control changes.
- Include documentation updates with architecture-affecting work.
- Include tests or a clear non-runtime rationale.

## Definition of Done

A production-facing change includes documentation, configuration impact, security/auditability analysis, observability impact, and migration/rollback guidance where applicable.

## Testing Expectations

- Unit tests for future domain logic.
- Contract tests for APIs and exchange adapters.
- Integration tests for PostgreSQL, Redis, and service boundaries.
- Replay/simulation tests before live trading behavior.
- Failure-mode tests for circuit breakers, kill switch, and emergency shutdown.
