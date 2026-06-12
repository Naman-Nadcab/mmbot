# Institutional Cryptocurrency Market Making Platform

Enterprise foundation for an institutional cryptocurrency market making platform.

**Current scope:** repository bootstrap only. This branch intentionally does not implement trading algorithms, order placement, live exchange integrations, or market making business logic.

## Foundation Objectives

- Establish a professional repository architecture.
- Define coding, development, naming, security, operations, and documentation standards.
- Standardize environment configuration and validation.
- Design the PostgreSQL data model for auditability and future trading workflows.
- Provide Docker and Compose foundations for backend, engines, dashboard, PostgreSQL, Redis, NGINX, and monitoring.
- Document the architecture and roadmap before business logic begins.

## Repository Layout

```text
.
├── api/                         # API and gateway contracts
├── architecture/                # Enterprise system architecture
├── database/                    # PostgreSQL schema and data design
├── deployment/                  # Deployment topology and runbooks
├── docker/                      # Dockerfiles and runtime config
├── docs/                        # Standards, roadmap, bootstrap report
├── monitoring/                  # Metrics/observability config
├── operations/                  # Health checks, incident response, SLOs
├── scripts/                     # Safe operational scripts
├── security/                    # Security model and controls
├── services/                    # Runtime service packages and dashboard assets
├── Dockerfile                   # Backend foundation container
├── docker-compose.yml           # Local/integration stack
└── docker-compose.production.yml# Production override foundation
```

## Planned Platform Components

| Component | Responsibility | Status |
| --- | --- | --- |
| Market Making Engine | Quote lifecycle orchestration after approval | Boundary only |
| Inventory Engine | Positions, balances, exposure, snapshots | Schema/boundary only |
| Risk Engine | Limits, circuit breakers, kill switch | Planned and documented |
| Liquidity Engine | Spread, depth, slippage, venue quality | Schema/boundary only |
| Market Data Engine | Normalized market data ingestion | Boundary only |
| Exchange Adapters | Venue-specific APIs/WebSockets/signing | Not implemented |
| Alert Engine | Alert routing and escalation | Schema/planning only |
| Analytics Engine | PnL, volatility, liquidity analytics | Schema/planning only |
| Admin Dashboard | RBAC administrative console | Containerized admin asset shell; backend admin APIs implemented |
| Websocket Gateway | Realtime operational streams | Planned |
| API Gateway | Auth, RBAC, rate limiting, API edge | FastAPI health and admin configuration APIs implemented |
| Database Layer | Durable PostgreSQL source of truth | Schema designed |
| Redis Layer | Cache, queues, pub/sub, counters | Compose foundation |
| Monitoring Layer | Metrics, logs, health, alerts | Foundation config |

## Environment Management

All secrets and deployment-specific values must be supplied through environment variables or a managed secret store. Never hardcode credentials, API keys, JWT material, Telegram tokens, server addresses, or exchange secrets.

Required variables:

- `DATABASE_URL`
- `REDIS_URL`
- `JWT_SECRET`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- `EXCHANGE_API_KEYS`
- `EXCHANGE_API_SECRETS`
- `SERVER_IP`
- `SERVER_PORT`
- `LOG_LEVEL`
- `APP_ENV`

Environment files:

- `.env.example`
- `.env.development`
- `.env.staging`
- `.env.production`

Validate with:

```bash
python3 scripts/validate_env.py --file .env.development
python3 scripts/validate_env.py --file .env.example --allow-template-values
python3 scripts/validate_env.py --file .env.production --release-checks --repo-root .
```

For production Compose deployments, replace every `CHANGE_ME` value in `.env.production` before running Docker Compose. The production override derives container `DATABASE_URL` and `REDIS_URL` from `POSTGRES_*` and `REDIS_PASSWORD` values so the stack can be recreated from the checked-in compose files.

## Local Bootstrap Stack

```bash
python3 scripts/validate_env.py --file .env.development
docker compose --env-file .env.development up --build
```

The stack starts infrastructure, the FastAPI backend, engine command runtime, dashboard assets, NGINX, and monitoring.

## Documentation Index

- [Repository Structure](docs/repository-structure.md)
- [Development Standards](docs/development-standards.md)
- [Coding Standards](docs/coding-standards.md)
- [Naming Conventions](docs/naming-conventions.md)
- [Architecture](architecture/README.md)
- [API Foundation](api/README.md)
- [Database Design](database/README.md)
- [Deployment Foundation](deployment/README.md)
- [Security Foundation](security/README.md)
- [Operations Foundation](operations/README.md)
- [Roadmap](docs/roadmap.md)
- [Bootstrap Report](docs/bootstrap-report.md)

## Approval Gate

Review and approve architecture, security controls, environment policy, schema direction, and deployment topology before generating business logic, trading algorithms, or exchange integrations.
