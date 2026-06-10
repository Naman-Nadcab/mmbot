# GitHub Secrets and Variables Configuration Guide

This repository deploys to a VPS through GitHub Actions. All credentials must be stored as GitHub Secrets or GitHub environment secrets. Non-sensitive deployment settings may be stored as GitHub Variables.

## Required GitHub Secrets

Configure these in the repository or production environment secrets:

| Secret | Purpose |
| --- | --- |
| `VPS_HOST` | DNS name or IP address of the production VPS. |
| `VPS_USER` | SSH deployment user on the VPS. |
| `VPS_SSH_KEY` | Private SSH key authorized for `VPS_USER`. |
| `DATABASE_URL` | Async SQLAlchemy PostgreSQL URL used by the backend. |
| `REDIS_URL` | Redis URL used by the backend. |
| `REDIS_PASSWORD` | Redis password used by production Redis. |
| `JWT_SECRET` | JWT signing secret with at least 32 characters. |
| `TELEGRAM_BOT_TOKEN` | Telegram bot token for app and deployment notifications. |
| `TELEGRAM_CHAT_ID` | Telegram chat ID for app and deployment notifications. |
| `EXCHANGE_API_KEYS` | JSON object of exchange API keys keyed by venue alias. |
| `EXCHANGE_API_SECRETS` | JSON object of exchange API secrets keyed by venue alias. |
| `POSTGRES_DB` | PostgreSQL database name. |
| `POSTGRES_USER` | PostgreSQL application user. |
| `POSTGRES_PASSWORD` | PostgreSQL application password. |

## Optional GitHub Secrets

| Secret | Purpose |
| --- | --- |
| `VPS_SSH_KNOWN_HOSTS` | Pinned SSH known_hosts entry for the VPS. Strongly recommended. |
| `EXCHANGE_API_PASSPHRASES` | JSON object of exchange passphrases keyed by venue alias. Required for venues that use passphrases. |
| `EXCHANGE_API_MEMOS` | JSON object of exchange memo values keyed by venue alias. Required for venues that use memo-based signing. |

## Required Secret Formats

### Exchange Credential JSON

`EXCHANGE_API_KEYS`, `EXCHANGE_API_SECRETS`, `EXCHANGE_API_PASSPHRASES`, and `EXCHANGE_API_MEMOS` must be valid JSON objects.

The keys must match venue aliases used by the platform:

Required venue alias keys are `binance`, `coinstore`, `mexc`, `gate`, `bitmart`, and `kucoin`. The JSON values must be the actual secret values stored in GitHub Secrets. Use `{}` for optional passphrase or memo maps when no venue requires those values.

### Database URL

`DATABASE_URL` must use the async PostgreSQL driver format:

```text
postgresql+asyncpg://user:password@postgres:5432/database
```

When using the Compose PostgreSQL service, the hostname is `postgres`.

### Redis URL

`REDIS_URL` should reference the Redis service:

```text
redis://redis:6379/0
```

The password is provided separately through `REDIS_PASSWORD` and injected into both Redis and application containers.

## Recommended GitHub Variables

Configure these as repository or environment variables:

| Variable | Default | Purpose |
| --- | --- | --- |
| `VPS_APP_DIR` | `/opt/mmbot` | Absolute repository path on the VPS. |
| `HEALTHCHECK_URL` | `http://127.0.0.1/health` | URL used by deployment health checks. |
| `RESTORE_DATABASE_ON_ROLLBACK` | `false` | Set to `true` only after database restore procedure is approved. |
| `NGINX_PORT` | `80` | Public NGINX port. |
| `PROMETHEUS_PORT` | `9090` | Prometheus port. |
| `SERVER_PORT` | `8000` | Backend container port. |

## GitHub Environment Protection

Create a GitHub environment named `production` and configure:

- Required reviewers.
- Deployment branch restriction to `main`.
- Environment-specific secrets where possible.
- Deployment history retention.

## SSH Known Hosts

Generate a known_hosts entry from a trusted workstation:

```bash
ssh-keyscan -H "$VPS_HOST"
```

Store the output in `VPS_SSH_KNOWN_HOSTS`. If this secret is absent, the workflow will use `ssh-keyscan` during deployment.

## Telegram Deployment Notifications

The deployment workflow sends:

- Success notification.
- Failure notification.
- Rollback-attempt notification.

Both `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` are required. If notification delivery fails, the deployment job fails so operators know the notification channel is unhealthy.

## Secret Rotation Procedure

1. Add new secret value in GitHub Secrets.
2. Rotate the corresponding VPS or exchange credential.
3. Trigger a manual production deployment from GitHub Actions.
4. Verify health checks and deployment notification.
5. Revoke the old credential.
6. Record the rotation in the audit log or deployment ticket.

## Required Main Branch Behavior

Every push to `main` triggers:

1. Testing workflow.
2. Security workflow.
3. Deployment workflow only after testing and security gates pass.
4. VPS deployment through SSH.
5. Automatic rollback on deployment failure.
6. Telegram notification.
