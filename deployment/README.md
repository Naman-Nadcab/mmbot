# Deployment Foundation

Docker and Compose foundations are provided for local and production-oriented deployments. Business logic is intentionally absent.

## Containers

| Service | Purpose |
| --- | --- |
| `backend` | API/backend boundary placeholder. |
| `market-maker-engine` | Engine boundary placeholder; no algorithms. |
| `market-data-engine` | Market data boundary placeholder; no integrations. |
| `dashboard` | Static admin dashboard placeholder. |
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
