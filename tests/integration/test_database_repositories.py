import secrets

import pytest

from mmbot.core.config import Settings, default_runtime_config
from mmbot.db.models import Base
from mmbot.db.repositories import ConfigRepository
from mmbot.db.session import Database


@pytest.mark.asyncio
async def test_config_repository_versions_and_runtime_config():
    settings = Settings(
        APP_ENV="test",
        DATABASE_URL="sqlite+aiosqlite:///:memory:",
        REDIS_URL="redis://localhost:6379/0",
        JWT_SECRET=secrets.token_urlsafe(48),
        TELEGRAM_BOT_TOKEN="token",
        TELEGRAM_CHAT_ID="chat",
        EXCHANGE_API_KEYS={"binance": "key"},
        EXCHANGE_API_SECRETS={"binance": "secret"},
    )
    database = Database(settings)
    async with database.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with database.session(actor_service="test") as session:
        repo = ConfigRepository(session)
        spread = default_runtime_config().spread.model_dump()
        spread["base_spread_bps"] = 30
        row = await repo.upsert_domain("spread", spread)
        runtime = await repo.runtime_config()
        assert row.version == 1
        assert runtime.spread.base_spread_bps == 30
    await database.close()
