import asyncio
import secrets

import pytest

from mmbot.core.config import Settings, default_runtime_config
from mmbot.engines.market_data.engine import MarketDataEngine
from mmbot.engines.market_data.runtime import MarketDataRuntime
from mmbot.execution.models import ExecutionVenue
from mmbot.observability.metrics import RuntimeMetrics


class MemoryCache:
    async def set_json(self, key, value, ttl_seconds=None):
        return None


class MemoryPubSub:
    async def publish(self, channel, payload):
        return 1


class MemoryBus:
    cache = MemoryCache()
    pubsub = MemoryPubSub()


class ConnectorState:
    def __init__(self, connected, messages_received=0):
        self.connected = connected
        self.messages_received = messages_received
        self.venue = ExecutionVenue.coinstore


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
        MARKET_DATA_CONNECT_ON_START=True,
        MARKET_DATA_EXCHANGES=["coinstore"],
        MARKET_DATA_SYMBOLS=["BTC/USDT"],
        MARKET_DATA_STREAMS=["orderbook", "trades", "ticker", "kline"],
    )


@pytest.mark.asyncio
async def test_validate_health_accepts_connected_websocket_with_raw_messages():
    runtime = MarketDataRuntime(_settings(), None, MemoryBus(), MarketDataEngine(default_runtime_config().liquidity), RuntimeMetrics())
    task = asyncio.create_task(asyncio.sleep(60))
    try:
        runtime.active_subscriptions = 4
        runtime.connectors = [ConnectorState(connected=True, messages_received=5)]
        runtime.tasks = [task]
        runtime._record_websocket_message(ExecutionVenue.coinstore)

        runtime.validate_health()

        health = runtime.health()
        assert health["websocket_messages_received"] == 1
        assert health["last_websocket_message_timestamp"] is not None
        assert health["last_websocket_message_by_venue"]["coinstore"]
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_validate_health_does_not_require_normalized_publish_for_raw_messages():
    runtime = MarketDataRuntime(_settings(), None, MemoryBus(), MarketDataEngine(default_runtime_config().liquidity), RuntimeMetrics())
    task = asyncio.create_task(asyncio.sleep(60))
    try:
        runtime.active_subscriptions = 4
        runtime.connectors = [ConnectorState(connected=False, messages_received=5)]
        runtime.tasks = [task]
        runtime._record_websocket_message(ExecutionVenue.coinstore)
        runtime.last_message_at = {}
        runtime.redis_publish_count = 0

        runtime.validate_health()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
