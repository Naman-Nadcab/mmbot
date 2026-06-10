from __future__ import annotations

from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from mmbot.db.migrations import _split_sql


class SeedRunner:
    def __init__(self, engine: AsyncEngine, seeds_path: Path):
        self.engine = engine
        self.seeds_path = seeds_path

    async def seed(self) -> list[str]:
        applied: list[str] = []
        async with self.engine.begin() as conn:
            await conn.execute(text("CREATE TABLE IF NOT EXISTS seed_history (name TEXT PRIMARY KEY, applied_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP)"))
            existing = {row[0] for row in (await conn.execute(text("SELECT name FROM seed_history"))).all()}
            for path in sorted(self.seeds_path.glob("*.sql")):
                name = path.name
                if name in existing:
                    continue
                for statement in _split_sql(path.read_text(encoding="utf-8")):
                    await conn.execute(text(statement))
                await conn.execute(text("INSERT INTO seed_history(name) VALUES (:name)"), {"name": name})
                applied.append(name)
        return applied
