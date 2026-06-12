import secrets
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from mmbot.core.config import Settings, default_runtime_config
from mmbot.db import models
from mmbot.db.models import Base
from mmbot.db.session import Database
from mmbot.engines.market_making.engine import QuoteEngine
from mmbot.engines.market_making.runtime import MarketMakerRuntime
from mmbot.execution.models import ExecutionOrderType, ExecutionSide, ExecutionVenue, OrderIntent, TimeInForce
from mmbot.exchanges.types import OrderBookLevel, OrderBookSnapshot
from mmbot.observability.metrics import RuntimeMetrics


class MemoryCache:
    def __init__(self):
        self.data = {}

    async def set_json(self, key, value, ttl_seconds=None):
        self.data[key] = value

    async def get_json(self, key):
        return self.data.get(key)


class MemoryPubSub:
    async def publish(self, channel, payload):
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
        MARKET_DATA_EXCHANGES=["binance"],
        MARKET_DATA_SYMBOLS=["BTC/USDT"],
        MARKET_DATA_STREAMS=["orderbook", "trades", "ticker", "kline"],
        MARKET_MAKER_REFRESH_SECONDS=0.01,
        RECONCILIATION_INTERVAL_SECONDS=0.01,
    )


async def _runtime():
    settings = _settings()
    database = Database(settings)
    async with database.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session = database.session_factory()
    config = default_runtime_config()
    runtime = MarketMakerRuntime(settings, session, MemoryBus(), QuoteEngine(config.spread, config.order_size, config.inventory, config.order_layers), RuntimeMetrics(), config)
    runtime.started = True
    payload = {"exchange": "binance", "symbol": "BTC/USDT", "bids": [{"price": 99000, "size": 5}], "asks": [{"price": 101000, "size": 5}], "source_timestamp": datetime.now(timezone.utc).isoformat(), "sequence": "1"}
    await runtime.bus.cache.set_json("latest:marketdata:orderbook:binance:BTC/USDT", payload)
    await runtime.bus.cache.set_json("latest:marketdata:analytics:binance:BTC/USDT", {"spread": {"spread_bps": 0.2}, "liquidity": {"imbalance_ratio": 0.0}, "realized_volatility": 0.0})
    return database, session, runtime


@pytest.mark.asyncio
async def test_paper_ladder_depth_remains_stable_across_ticks():
    database, session, runtime = await _runtime()
    try:
        for _ in range(5):
            await runtime.tick()
        lifecycle = runtime.health()["order_lifecycle"]
        assert lifecycle["open_orders_count"] == 6
        assert lifecycle["active_buy_orders"] == 3
        assert lifecycle["active_sell_orders"] == 3
        assert lifecycle["risk_rejections_last_hour"] == 0
        assert runtime.metrics.counters.get("risk.rejections", 0) == 0
    finally:
        await session.close()
        await database.close()


@pytest.mark.asyncio
async def test_stale_orders_are_cancelled_and_recreated_at_fixed_depth():
    database, session, runtime = await _runtime()
    try:
        await runtime.tick()
        for order in runtime.paper.open_orders.values():
            order.created_at = datetime.now(timezone.utc) - timedelta(seconds=120)
        await runtime.tick()
        lifecycle = runtime.health()["order_lifecycle"]
        assert lifecycle["open_orders_count"] == 6
        assert lifecycle["stale_orders_count"] >= 6
        assert lifecycle["cancelled_orders_count"] >= 6
        assert lifecycle["reconciliation_actions"] >= 6
    finally:
        await session.close()
        await database.close()


@pytest.mark.asyncio
async def test_reconciliation_marks_db_orphan_orders_cancelled():
    database, session, runtime = await _runtime()
    try:
        account_id = await runtime.paper._ensure_exchange_account()
        pair_id = await runtime.paper._ensure_trading_pair("BTC/USDT")
        orphan = models.Order(client_order_id="orphan-paper-order", exchange_order_id="paper-orphan", exchange_account_id=account_id, trading_pair_id=pair_id, side=models.OrderSide.buy, order_type=models.OrderType.limit, status=models.OrderStatus.open, price=Decimal("1"), quantity=Decimal("1"), filled_quantity=Decimal("0"), metadata_json={"mode": "paper"})
        session.add(orphan)
        await session.flush()
        actions = await runtime._reconcile_paper_order_state()
        row = (await session.execute(select(models.Order).where(models.Order.client_order_id == "orphan-paper-order"))).scalar_one()
        assert actions >= 1
        assert row.status == models.OrderStatus.cancelled
        assert row.metadata_json["orphan_cleanup"] is True
    finally:
        await session.close()
        await database.close()


@pytest.mark.asyncio
async def test_filled_orders_are_not_counted_as_open_lifecycle_orders():
    database, session, runtime = await _runtime()
    try:
        crossing_book = OrderBookSnapshot("binance", "BTC/USDT", bids=[OrderBookLevel(99999, 5)], asks=[OrderBookLevel(100, 5)], source_timestamp=datetime.now(timezone.utc))
        intent = OrderIntent(ExecutionVenue.binance, "BTC/USDT", ExecutionSide.buy, ExecutionOrderType.limit, Decimal("0.01"), Decimal("100"), "fillable-paper-order", TimeInForce.gtc, metadata={"symbol": "BTC/USDT", "side": "buy", "level": 1, "ladder_key": "BTC/USDT:buy:1"})
        await runtime.paper.place_order(intent, crossing_book)
        assert "fillable-paper-order" not in runtime.paper.open_orders
        assert runtime.health()["order_lifecycle"]["open_orders_count"] == 0
    finally:
        await session.close()
        await database.close()
