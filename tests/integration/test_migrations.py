from pathlib import Path

import pytest
from sqlalchemy import text

from mmbot.core.config import Settings
from mmbot.db.migrations import INITIAL_MIGRATION, INITIAL_SCHEMA_TABLES, MigrationRunner
from mmbot.db.session import Database


def _settings() -> Settings:
    return Settings(
        APP_ENV="test",
        DATABASE_URL="sqlite+aiosqlite:///:memory:",
        REDIS_URL="redis://localhost:6379/0",
        JWT_SECRET="x" * 48,
        TELEGRAM_BOT_TOKEN="token",
        TELEGRAM_CHAT_ID="chat",
        EXCHANGE_API_KEYS={"binance": "key"},
        EXCHANGE_API_SECRETS={"binance": "secret"},
    )


@pytest.mark.asyncio
async def test_migration_runner_bootstraps_existing_initial_schema(tmp_path: Path):
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    (migrations_dir / INITIAL_MIGRATION).write_text("CREATE TABLE should_not_run (id INTEGER PRIMARY KEY);", encoding="utf-8")
    (migrations_dir / "0002_runtime_events_and_rbac.sql").write_text("CREATE TABLE runtime_events (id INTEGER PRIMARY KEY);", encoding="utf-8")
    database = Database(_settings())
    try:
        async with database.engine.begin() as conn:
            for table in INITIAL_SCHEMA_TABLES:
                await conn.execute(text(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY)"))
        applied = await MigrationRunner(database.engine, migrations_dir).migrate()
        async with database.engine.connect() as conn:
            versions = [row[0] for row in (await conn.execute(text("SELECT version FROM schema_migrations ORDER BY version"))).all()]
            runtime_events = (await conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='runtime_events'"))).scalar_one_or_none()
            should_not_run = (await conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='should_not_run'"))).scalar_one_or_none()
        assert applied == ["0002_runtime_events_and_rbac.sql"]
        assert versions == [INITIAL_MIGRATION, "0002_runtime_events_and_rbac.sql"]
        assert runtime_events == "runtime_events"
        assert should_not_run is None
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_migration_runner_remains_idempotent_after_bootstrap(tmp_path: Path):
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    (migrations_dir / INITIAL_MIGRATION).write_text("CREATE TABLE should_not_run (id INTEGER PRIMARY KEY);", encoding="utf-8")
    (migrations_dir / "0002_runtime_events_and_rbac.sql").write_text("CREATE TABLE runtime_events (id INTEGER PRIMARY KEY);", encoding="utf-8")
    database = Database(_settings())
    try:
        async with database.engine.begin() as conn:
            for table in INITIAL_SCHEMA_TABLES:
                await conn.execute(text(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY)"))
        first = await MigrationRunner(database.engine, migrations_dir).migrate()
        second = await MigrationRunner(database.engine, migrations_dir).migrate()
        assert first == ["0002_runtime_events_and_rbac.sql"]
        assert second == []
    finally:
        await database.close()
