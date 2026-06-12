import asyncio
import secrets
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from mmbot.core.config import Settings, default_runtime_config
from mmbot.db.models import Base
from mmbot.db.session import Database
from mmbot.engines.market_making.engine import Quote, QuoteEngine
from mmbot.engines.market_making.runtime import MarketMakerRuntime
from mmbot.execution.coinstore_reconciliation import CoinstoreReconciliationReport
from mmbot.execution.models import ExecutionOrder, ExecutionOrderType, ExecutionSide, ExecutionVenue, NormalizedOrderStatus
from mmbot.exchanges.types import OrderBookLevel, OrderBookSnapshot
from mmbot.observability.metrics import RuntimeMetrics
from mmbot.reconciliation.engine import ReconciliationMismatch, ReconciliationSeverity, ReconciliationSnapshot


class MemoryBus:
    cache = None
    pubsub = None


class RecordingCoinstoreService:
    def __init__(self):
        self.orders = []
        self.private_messages = []

    async def place_order(self, intent):
        self.orders.append(intent)
        return ExecutionOrder(ExecutionVenue.coinstore, intent.symbol, intent.client_order_id, "live-1", NormalizedOrderStatus.open, intent.side, intent.order_type, intent.price, intent.quantity, Decimal("0"), None, None, {})

    async def client(self):
        return type("RestClient", (), {"credentials": type("Credentials", (), {"api_key": "key", "api_secret": "secret"})()})()

    async def handle_private_message(self, message):
        self.private_messages.append(message)
        return {"orders": 1, "trades": 1, "fills": 1, "balances": 1}

    async def reconcile_live(self, symbols):
        return CoinstoreReconciliationReport(
            ReconciliationSnapshot(),
            ReconciliationSnapshot(),
            [ReconciliationMismatch("order", "orphan", ReconciliationSeverity.critical, "missing", "missing", "present")],
            ["stale"],
            ["orphan"],
        )


class OneShotPrivateWebSocket:
    def __init__(self, settings, credentials):
        self.settings = settings
        self.credentials = credentials
        self.stopped = False

    def stop(self):
        self.stopped = True

    async def run(self, handler):
        await handler({"messageType": 3004, "clientOrderId": "cid", "orderId": "oid"})


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


@pytest.mark.asyncio
async def test_canary_runtime_starts_coinstore_private_stream(monkeypatch):
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
        monkeypatch.setattr("mmbot.engines.market_making.runtime.CoinstorePrivateWebSocketClient", OneShotPrivateWebSocket)

        await runtime._start_coinstore_private_stream()
        await asyncio.wait_for(runtime.coinstore_private_task, timeout=1)

        assert coinstore.private_messages == [{"messageType": 3004, "clientOrderId": "cid", "orderId": "oid"}]
        assert runtime.metrics.counters["coinstore.private_order_updates"] == 1
        assert runtime.metrics.counters["coinstore.private_trade_updates"] == 1
        assert runtime.metrics.counters["coinstore.private_fill_updates"] == 1
        assert runtime.metrics.counters["coinstore.private_balance_updates"] == 1
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_canary_runtime_uses_live_coinstore_reconciliation():
    settings = _settings()
    database = Database(settings)
    try:
        async with database.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        session = database.session_factory()
        config = default_runtime_config()
        runtime = MarketMakerRuntime(settings, session, MemoryBus(), QuoteEngine(config.spread, config.order_size, config.inventory, config.order_layers), RuntimeMetrics(), config)
        runtime.coinstore = RecordingCoinstoreService()
        runtime.last_reconciliation_at = datetime.now(timezone.utc) - timedelta(seconds=settings.RECONCILIATION_INTERVAL_SECONDS + 1)

        await runtime._maybe_reconcile()

        assert runtime.metrics.counters["reconciliation.runs"] == 1
        assert runtime.metrics.counters["reconciliation.mismatches"] == 1
        assert runtime.metrics.counters["coinstore.reconciliation.stale_orders"] == 1
        assert runtime.metrics.counters["coinstore.reconciliation.orphan_orders"] == 1
    finally:
        await database.close()
