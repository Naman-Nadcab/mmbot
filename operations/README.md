# Operations Foundation

## Health Checks

Runtime services must expose liveness, readiness, dependency health, and version metadata.

## Centralized Logging

Planned requirements: structured JSON logs, correlation IDs, secret redaction, component/environment/host/version fields, and central shipping to OpenSearch, Loki, Datadog, Splunk, CloudWatch, or equivalent.

## Metrics Collection

Prometheus foundation is in `monitoring/prometheus.yml`. Planned metrics include request latency/errors, order lifecycle latency, market data staleness, exchange reconnects, risk event counts, circuit breaker state, inventory exposure, PnL, liquidity, PostgreSQL, and Redis health.

## Performance Monitoring

Track latency from market data ingestion to publication, quote input to order command creation, order submission to acknowledgement, and exchange WebSocket receive to persistence/publication.

## Error Tracking

Group errors by component, exception type, route/operation, dependency, venue, and correlation ID.

## Alert Routing

Severity levels: `info`, `warning`, `critical`, `emergency`. Telegram is available as a bootstrap channel; production alerting should have redundant routes.

## Incident Response

Preserve timeline, affected services/venues, risk and audit event IDs, operator actions, kill switch state, recovery approval, and corrective actions.
