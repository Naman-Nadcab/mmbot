import secrets
from decimal import Decimal

import pytest
from sqlalchemy import select

from mmbot.core.config import Settings, default_runtime_config
from mmbot.db import models
from mmbot.db.models import Base
from mmbot.db.session import Database
from mmbot.engines.market_making.engine import QuoteEngine
from mmbot.engines.market_making.runtime import MarketMakerRuntime
from mmbot.observability.metrics import RuntimeMetrics


class MemoryCache:
    def __init__(self):
        self.data = {}

    async def set_json(self, key, value, ttl_seconds=None):
        self.data[key] = value

    async def get_json(self, key):
        return self.data.get(key)


class MemoryPubSub:
    def __init__(self):
        self.published = []

    async def publish(self, channel, payload):
        self.published.append((channel, payload))
        return 1


class MemoryBus:
    def __init__(self):
        self.cache = MemoryCache()
        self.pubsub = MemoryPubSub()


def _settings() -> Settings:
    return Settings(
        APP_ENV="test",
        DATABASE_URL="sqlite+aiosqlite:///:memory:",
        REDIS_URL="redis://localhost:6379/0",
        JWT_SECRET=secrets.token_urlsafe(48),
        TELEGRAM_BOT_TOKEN="token",
        TELEGRAM_CHAT_ID="chat",
        EXCHANGE_API_KEYS={"binance": "key"},
        EXCHANGE_API_SECRETS={"binance": "secret"},
        MARKET_DATA_CONNECT_ON_START=False,
    )


@pytest.mark.asyncio
async def test_market_maker_runtime_reloads_config_and_persists_ack():
    database = Database(_settings())
    try:
        async with database.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        session = database.session_factory()
        config = default_runtime_config()
        bus = MemoryBus()
        runtime = MarketMakerRuntime(
            _settings(),
            session,
            bus,
            QuoteEngine(config.spread, config.order_size, config.inventory, config.order_layers),
            RuntimeMetrics(),
            config,
        )
        payload = config.model_dump()
        payload["spread"]["base_spread_bps"] = 40
        payload["order_size"]["base_order_size"] = 0.02
        payload["inventory"]["target_base_ratio"] = 0.4
        payload["risk"]["max_open_orders"] = 50
        payload["volume"]["enabled"] = True

        await runtime._apply_config_payload({"runtime_config": payload, "command_id": "cfg-1"}, command_id="cfg-1")

        assert runtime.quote_engine.spread_settings.base_spread_bps == 40
        assert runtime.quote_engine.order_size_settings.base_order_size == 0.02
        assert runtime.inventory_engine.settings.target_base_ratio == 0.4
        assert runtime.risk_engine.settings.max_open_orders == 50
        assert runtime.volume_engine.settings.enabled is True
        assert bus.cache.data["runtime:ack:cfg-1:market-maker-engine"]["status"] == "acknowledged"
        result = await session.execute(select(models.RuntimeEvent).where(models.RuntimeEvent.command_id == "cfg-1"))
        assert result.scalar_one().event_type == "runtime_config_reload_ack"
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_market_maker_runtime_command_ack_is_persisted():
    database = Database(_settings())
    try:
        async with database.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        session = database.session_factory()
        config = default_runtime_config()
        bus = MemoryBus()
        runtime = MarketMakerRuntime(
            _settings(),
            session,
            bus,
            QuoteEngine(config.spread, config.order_size, config.inventory, config.order_layers),
            RuntimeMetrics(),
            config,
        )

        await runtime._handle_command({"command_id": "cmd-1", "command_type": "STRATEGY_COMMAND", "payload": {"action": "pause"}})

        assert runtime.trading_enabled is False
        assert runtime.quoting_enabled is False
        assert bus.cache.data["runtime:ack:cmd-1:market-maker-engine"]["payload"]["command_type"] == "STRATEGY_COMMAND"
        result = await session.execute(select(models.RuntimeEvent).where(models.RuntimeEvent.command_id == "cmd-1"))
        assert result.scalar_one().event_type == "runtime_command_ack"
    finally:
        await database.close()
