# Deployment Foundation

Docker and Compose foundations are provided for local and production-oriented deployments. Business logic is intentionally absent.

## Containers

| Service | Purpose |
| --- | --- |
| `backend` | FastAPI backend with health, exchange capability, and dynamic admin configuration APIs. |
| `market-maker-engine` | Python engine runtime containing quote, ladder, spread, replacement, and protection logic. |
| `market-data-engine` | Python engine runtime containing stream analytics, spread, volatility, liquidity, and distribution logic. |
| `dashboard` | Static dashboard asset container served behind NGINX. |
| `postgres` | Durable PostgreSQL state. |
| `redis` | Ephemeral cache, queue, pub/sub, counters. |
| `nginx` | Reverse proxy foundation. |
| `monitoring` | Prometheus foundation. |

## Local

```bash
python3 scripts/validate_env.py --file .env.development
docker compose --env-file .env.development up --build
```

## Production Pattern

```bash
python3 scripts/validate_env.py --file .env.production
docker compose --env-file .env.production -f docker-compose.yml -f docker-compose.production.yml up --build -d
```

## Required Before Production Business Logic

Add CI/CD, vulnerability scans, migration automation, TLS, network segmentation, backup/restore, secret manager integration, image signing, and deployment rollback procedures.
