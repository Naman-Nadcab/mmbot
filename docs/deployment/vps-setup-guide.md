# VPS Setup Guide

This guide prepares a VPS for automated production deployment from GitHub Actions.

## Operating Requirements

- Linux host with systemd.
- Docker Engine with Compose plugin.
- Git.
- Python 3.12 or compatible Python 3 runtime for environment validation.
- Outbound HTTPS access for GitHub, package registries, Telegram API, and exchange APIs.
- Inbound HTTP/HTTPS access only through NGINX or the configured reverse proxy.

## System Packages

Install packages using the VPS operating system package manager:

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl git gnupg python3 python3-venv openssh-server
```

Install Docker Engine and the Compose plugin from Docker's official repository for the VPS distribution. Confirm installation:

```bash
docker --version
docker compose version
```

## Deployment User

Create a dedicated deployment user:

```bash
sudo useradd --create-home --shell /bin/bash deploy
sudo usermod -aG docker deploy
sudo install -d -o deploy -g deploy -m 700 /home/deploy/.ssh
```

Add the public half of the deployment key to:

```text
/home/deploy/.ssh/authorized_keys
```

Set permissions:

```bash
sudo chown deploy:deploy /home/deploy/.ssh/authorized_keys
sudo chmod 600 /home/deploy/.ssh/authorized_keys
```

## Application Directory

Create the application directory:

```bash
sudo install -d -o deploy -g deploy -m 750 /opt/mmbot
```

Clone the repository as the deployment user:

```bash
sudo -iu deploy
cd /opt/mmbot
git clone git@github.com:Naman-Nadcab/mmbot.git .
git checkout main
```

The default GitHub Actions variable `VPS_APP_DIR` is `/opt/mmbot`. If another path is used, configure the GitHub repository variable `VPS_APP_DIR` with the exact path.

## Production Environment File

GitHub Actions writes `.env.production` to the VPS during deployment from GitHub Secrets. The file must be owned by the deployment user and mode `600`.

The deployment workflow writes the file to:

```text
/opt/mmbot/.env.production
```

Do not manually commit this file.

## First Deployment Readiness

Before enabling main-branch deployment:

```bash
sudo -iu deploy
cd /opt/mmbot
python3 scripts/validate_env.py --file .env.production
```

Confirm Docker Compose renders correctly:

```bash
docker compose --env-file .env.production -f docker-compose.yml -f docker-compose.production.yml config --quiet
```

## Runtime Ports

Default ports:

- NGINX: `80`
- Backend service inside Docker network: `8000`
- Prometheus: `9090`

Configure firewall rules so only required public ports are exposed. PostgreSQL and Redis must not be publicly reachable.

## Deployment Flow

On push to `main`, GitHub Actions will:

1. Run testing workflow.
2. Run security workflow.
3. SSH into the VPS.
4. Write `.env.production` from GitHub Secrets.
5. Fetch and reset the repository to `origin/main`.
6. Validate environment configuration.
7. Render Docker Compose config.
8. Start PostgreSQL and Redis.
9. Create a PostgreSQL backup.
10. Build Docker images.
11. Run database migrations and seeds.
12. Restart containers.
13. Verify API, PostgreSQL, Redis, Market Data Engine, Market Maker Engine, and Risk Engine health.
14. Send Telegram deployment notification.

## Rollback Flow

Rollback is automatic when deployment fails during:

- Environment validation.
- Docker Compose rendering.
- PostgreSQL/Redis startup.
- Database backup.
- Image build.
- Migration or seed execution.
- Container startup.
- Health checks.

Rollback behavior:

1. Reset repository to the previous commit SHA.
2. Optionally restore the latest PostgreSQL backup if `RESTORE_DATABASE_ON_ROLLBACK=true` is configured as a GitHub variable.
3. Rebuild containers from the previous commit.
4. Restart containers.
5. Run health checks.

Manual rollback command on the VPS:

```bash
sudo -iu deploy
cd /opt/mmbot
APP_DIR=/opt/mmbot PREVIOUS_SHA=$(cat .deploy/state/previous_sha) scripts/rollback_vps.sh
```

## Health Check Command

Run manually:

```bash
sudo -iu deploy
cd /opt/mmbot
APP_DIR=/opt/mmbot HEALTHCHECK_URL=http://127.0.0.1/health scripts/healthcheck_vps.sh
```

Health checks verify:

- API health endpoint.
- PostgreSQL readiness.
- Redis authenticated ping.
- Backend container health.
- NGINX container health.
- Market Data Engine container health.
- Market Maker Engine container health.
- Risk Engine import/initialization inside backend container.

## Operational Rules

- Do not deploy directly as `root`.
- Do not store production secrets in Git.
- Do not expose PostgreSQL or Redis publicly.
- Do not enable live trading until exchange sandbox certification, shadow mode, paper mode, and canary approval are complete.
- Keep rollback and emergency shutdown procedures available during every deployment.
