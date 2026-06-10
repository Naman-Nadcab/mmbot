# Security Foundation

## Secrets Management

- All secrets load from environment variables or approved secret stores.
- Never commit real credentials, JWT material, private keys, Telegram tokens, or exchange secrets.
- Production should use AWS Secrets Manager, GCP Secret Manager, HashiCorp Vault, Kubernetes external secrets, or equivalent.
- Service identities must use least privilege.

## API Key Encryption

Exchange credentials are stored only as ciphertext:

- `exchange_accounts.api_key_ciphertext`
- `exchange_accounts.api_secret_ciphertext`
- `exchange_accounts.passphrase_ciphertext`
- `exchange_accounts.encryption_key_id`

Required controls: envelope encryption through KMS/Vault, key rotation, decrypt only in adapter signing boundary, minimize memory lifetime, and redact logs/traces/errors.

## RBAC

Foundation tables: `users`, `roles`, `permissions`, `user_roles`, `role_permissions`.

Planned roles: `platform_admin`, `risk_manager`, `trader_operator`, `read_only_analyst`, `incident_responder`, `service_account`.

Privileged actions require MFA, permission checks, audit logs, and correlation IDs.

## Rate Limiting and IP Restrictions

Use NGINX/API gateway throttles, Redis distributed counters, exchange adapter venue limits, stricter admin limits, corporate VPN or zero-trust access, CIDR allowlists, and separate public/internal/admin zones.

## Audit Logging

Audit records must include actor, action, resource, before/after state where safe, request/correlation IDs, IP address, user agent, and structured metadata.

## Circuit Breakers

Planned breakers: venue connectivity, market data freshness, order rejection rate, inventory imbalance, PnL drawdown, latency, database, and Redis dependency health.

## Kill Switch

The kill switch must stop order creation, cancel open orders where safe, freeze strategy transitions, emit critical alerts/audit logs, require privileged authorization to disable, and preserve incident evidence.

## Emergency Shutdown

Emergency shutdown sequence: stop order creation, cancel outstanding orders where possible, disable affected configs, persist risk/audit events, notify operators, snapshot inventory/positions/health, and require controlled recovery approval.
