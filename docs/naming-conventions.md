# Naming Conventions

- Directories: lowercase kebab-case.
- Python modules, SQL files, scripts: lowercase snake_case.
- Documentation files: lowercase kebab-case except conventional `README.md`.
- Services: lowercase kebab-case, for example `market-maker-engine`.
- Environment variables: uppercase snake case.
- Database tables/columns: lowercase snake_case.
- Indexes: `idx_<table>_<columns>`.
- Unique constraints: `uq_<table>_<columns>`.
- Check constraints: `chk_<table>_<rule>`.
- Audit event actions: uppercase snake case, for example `KILL_SWITCH_ENABLED`.
- Metrics: lowercase snake case with units where applicable, for example `order_latency_seconds`.
