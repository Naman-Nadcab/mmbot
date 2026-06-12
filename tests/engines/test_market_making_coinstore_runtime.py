import secrets
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from mmbot.core.config import Settings, default_runtime_config
from mmbot.db.models import Base
from mmbot.db.session import Database
from mmbot.engines.market_making.engine import Quote, QuoteEngine
from mmbot.engines.market_making.runtime import MarketMakerRuntime
from mmbot.execution.models import ExecutionOrder, ExecutionOrderType, ExecutionSide, ExecutionVenue, NormalizedOrderStatus
from mmbot.exchanges.types import OrderBookLevel, OrderBookSnapshot
from mmbot.observability.metrics import RuntimeMetrics


class MemoryBus:
    cache = None
    pubsub = None


class RecordingCoinstoreService:
    def __init__(self):
        self.orders = []

    async def place_order(self, intent):
        self.orders.append(intent)
        return ExecutionOrder(ExecutionVenue.coinstore, intent.symbol, intent.client_order_id, "live-1", NormalizedOrderStatus.open, intent.side, intent.order_type, intent.price, intent.quantity, Decimal("0"), None, None, {})


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
        TRADING_MODE="canary",
    )


@pytest.mark.asyncio
async def test_canary_runtime_submits_quotes_to_coinstore_execution():
    settings = _settings()
    database = Database(settings)
    try:
        async with database.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        session = database.session_factory()
        config = default_runtime_config()
        runtime = MarketMakerRuntime(settings, session, MemoryBus(), QuoteEngine(config.spread, config.order_size, config.inventory, config.order_layers), RuntimeMetrics(), config)
        coinstore = RecordingCoinstoreService()
        runtime.coinstore = coinstore
        orderbook = OrderBookSnapshot("coinstore", "BTC/USDT", [OrderBookLevel(99, 5)], [OrderBookLevel(101, 5)], datetime.now(timezone.utc))
        quote = Quote("buy", 100.0, 0.01, 1, "live-cid")

        await runtime._submit_quotes("BTC/USDT", [quote], orderbook)

        assert len(coinstore.orders) == 1
        assert coinstore.orders[0].venue is ExecutionVenue.coinstore
        assert runtime.metrics.counters["coinstore.orders_created"] == 1
        assert runtime.paper.open_orders == {}
    finally:
        await database.close()
