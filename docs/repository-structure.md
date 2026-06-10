# Repository Structure

The repository is organized around enterprise ownership boundaries.

| Directory | Purpose |
| --- | --- |
| `architecture/` | Logical/runtime architecture and component contracts. |
| `api/` | API gateway, WebSocket gateway, and service contract definitions. |
| `database/` | PostgreSQL schema, indexes, constraints, retention, audit model. |
| `deployment/` | Deployment topology, Compose usage, container strategy. |
| `docker/` | Dockerfiles and runtime configuration. |
| `docs/` | Cross-cutting standards, conventions, roadmap, reports. |
| `monitoring/` | Metrics and observability configuration. |
| `operations/` | Health checks, incident response, runbooks, SLOs. |
| `scripts/` | Operational scripts with no business logic. |
| `security/` | Secrets, RBAC, encryption, rate limits, emergency controls. |
| `services/` | Service boundary placeholders and future application modules. |

## Boundary Rules

- Services communicate through documented APIs, queues, pub/sub, or database contracts.
- No service may access another service's private implementation modules.
- Market making, exchange adapters, and risk decisions must be separately testable once approved.
- Shared libraries are introduced only for stable cross-service abstractions.
