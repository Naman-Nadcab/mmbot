from __future__ import annotations

from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


class MigrationRunner:
    def __init__(self, engine: AsyncEngine, migrations_path: Path):
        self.engine = engine
        self.migrations_path = migrations_path

    async def migrate(self) -> list[str]:
        applied: list[str] = []
        async with self.engine.begin() as conn:
            await conn.execute(text("CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY, applied_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP)"))
            existing = {row[0] for row in (await conn.execute(text("SELECT version FROM schema_migrations"))).all()}
            for path in sorted(self.migrations_path.glob("*.sql")):
                version = path.name
                if version in existing:
                    continue
                for statement in _split_sql(path.read_text(encoding="utf-8")):
                    await conn.execute(text(statement))
                await conn.execute(text("INSERT INTO schema_migrations(version) VALUES (:version)"), {"version": version})
                applied.append(version)
        return applied


def _split_sql(sql: str) -> list[str]:
    statements: list[str] = []
    current: list[str] = []
    in_function = False
    for line in sql.splitlines():
        stripped = line.strip()
        if "$$" in stripped:
            in_function = not in_function
        current.append(line)
        if stripped.endswith(";") and not in_function:
            statement = "\n".join(current).strip()
            if statement:
                statements.append(statement)
            current = []
    trailing = "\n".join(current).strip()
    if trailing:
        statements.append(trailing)
    return statements
