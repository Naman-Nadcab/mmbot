import secrets
from decimal import Decimal

import pytest

from mmbot.core.config import Settings
from mmbot.db import models
from mmbot.db.models import Base
from mmbot.db.session import Database
from mmbot.execution.coinstore import CoinstoreExecutionService
from mmbot.execution.coinstore_safety import CoinstoreSafetyError
from mmbot.execution.coinstore_validation import CoinstoreValidationError
from mmbot.execution.models import Balance, CancelIntent, ExecutionOrder, ExecutionOrderType, ExecutionSide, ExecutionVenue, NormalizedOrderStatus, OrderIntent, SymbolPrecision
from mmbot.execution.precision import PrecisionError
from mmbot.execution.signing import ExecutionCredentials
from mmbot.security.secrets import SecretCipher


class RecordingCoinstoreClient:
    def __init__(self):
        self.placed = []
        self.cancelled = []
        self.cancel_all_symbols = []
        self.status_requests = []
        self.credentials = ExecutionCredentials("key", "secret")

    async def place_order(self, intent, precision):
        self.placed.append((intent, precision))
        return ExecutionOrder(ExecutionVenue.coinstore, intent.symbol, intent.client_order_id, "coinstore-1", NormalizedOrderStatus.open, intent.side, intent.order_type, intent.price, intent.quantity, Decimal("0"), None, None, {"orderId": "coinstore-1", "status": "SUBMITTED"})

    async def cancel_order(self, intent):
        self.cancelled.append(intent)
        return ExecutionOrder(ExecutionVenue.coinstore, intent.symbol, intent.client_order_id, intent.exchange_order_id, NormalizedOrderStatus.cancelled, ExecutionSide.buy, ExecutionOrderType.limit, Decimal("100"), Decimal("0.1"), Decimal("0"), None, None, {"orderId": intent.exchange_order_id, "status": "CANCELED"})

    async def cancel_all_orders(self, symbol=None):
        self.cancel_all_symbols.append(symbol)
        return [ExecutionOrder(ExecutionVenue.coinstore, symbol or "BTC/USDT", "cid", "coinstore-1", NormalizedOrderStatus.cancelled, ExecutionSide.buy, ExecutionOrderType.limit, Decimal("100"), Decimal("0.1"), Decimal("0"), None, None, {"orderId": "coinstore-1", "status": "CANCELED"})]

    async def get_order_status(self, intent):
        self.status_requests.append(intent)
        return ExecutionOrder(ExecutionVenue.coinstore, intent.symbol, intent.client_order_id, intent.exchange_order_id, NormalizedOrderStatus.partially_filled, ExecutionSide.buy, ExecutionOrderType.limit, Decimal("100"), Decimal("0.1"), Decimal("0.05"), Decimal("100"), Decimal("0.01"), {"orderId": intent.exchange_order_id, "status": "PARTIAL_FILLED"})

    async def sync_balances(self):
        return [Balance(ExecutionVenue.coinstore, "USDT", Decimal("1000"), Decimal("900"), Decimal("100"), {"asset": "USDT", "available": "900", "total": "1000"})]

    async def sync_open_orders(self, symbol=None):
        return [ExecutionOrder(ExecutionVenue.coinstore, symbol or "BTC/USDT", "live-cid", "live-1", NormalizedOrderStatus.open, ExecutionSide.buy, ExecutionOrderType.limit, Decimal("100.00"), Decimal("0.100"), Decimal("0"), None, None, {"orderId": "live-1"})]

    async def sync_trade_fills(self, symbol=None):
        return [{"symbol": "BTCUSDT", "orderId": "live-1", "clientOrderId": "live-cid", "price": "100.00", "quantity": "0.100", "side": 1, "orderType": 1, "orderStatus": 4, "matchQty": "0.100", "matchId": "m-1", "tradeId": "t-1", "fee": "0.01", "matchTime": 1700000000000}]


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
    )


async def _seed_account_and_pair(session, settings):
    cipher = SecretCipher(settings)
    account = models.ExchangeAccount(exchange_name="coinstore", account_alias="primary", environment="production", api_key_ciphertext=cipher.encrypt("key"), api_secret_ciphertext=cipher.encrypt("secret"), encryption_key_id=cipher.key_id, permissions=["trade"], is_enabled=True)
    pair = models.TradingPair(exchange_name="coinstore", base_asset="BTC", quote_asset="USDT", normalized_symbol="BTC/USDT", venue_symbol="BTCUSDT", price_precision=2, quantity_precision=3, min_order_size=Decimal("0.001"), min_notional=Decimal("10"), tick_size=Decimal("0.01"), lot_size=Decimal("0.001"), is_enabled=True)
    session.add_all([account, pair])
    await session.flush()


@pytest.mark.asyncio
async def test_coinstore_place_order_validates_precision_and_persists_order():
    settings = _settings()
    database = Database(settings)
    try:
        async with database.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with database.session() as session:
            await _seed_account_and_pair(session, settings)
            service = CoinstoreExecutionService(settings, session)
            client = RecordingCoinstoreClient()
            service._client = client
            service._precision = {"BTC/USDT": SymbolPrecision("BTC/USDT", "BTCUSDT", Decimal("0.01"), Decimal("0.001"), Decimal("0.001"), Decimal("10"), 2, 3)}

            intent = OrderIntent(ExecutionVenue.coinstore, "BTC/USDT", ExecutionSide.buy, ExecutionOrderType.limit, Decimal("0.100"), Decimal("100.00"), "cid")
            order = await service.place_order(intent)

            assert order.exchange_order_id == "coinstore-1"
            assert len(client.placed) == 1
            saved = await session.get(models.Order, (await service.persist_order(order, intent)).id)
            assert saved.exchange_order_id == "coinstore-1"
            assert saved.status == models.OrderStatus.open
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_coinstore_place_order_rejects_invalid_tick_lot_and_min_notional():
    settings = _settings()
    database = Database(settings)
    try:
        async with database.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with database.session() as session:
            await _seed_account_and_pair(session, settings)
            service = CoinstoreExecutionService(settings, session)
            service._client = RecordingCoinstoreClient()
            service._precision = {"BTC/USDT": SymbolPrecision("BTC/USDT", "BTCUSDT", Decimal("0.01"), Decimal("0.001"), Decimal("0.001"), Decimal("10"), 2, 3)}

            bad_tick = OrderIntent(ExecutionVenue.coinstore, "BTC/USDT", ExecutionSide.buy, ExecutionOrderType.limit, Decimal("0.100"), Decimal("100.001"), "bad-tick")
            bad_lot = OrderIntent(ExecutionVenue.coinstore, "BTC/USDT", ExecutionSide.buy, ExecutionOrderType.limit, Decimal("0.1005"), Decimal("100.00"), "bad-lot")
            bad_notional = OrderIntent(ExecutionVenue.coinstore, "BTC/USDT", ExecutionSide.buy, ExecutionOrderType.limit, Decimal("0.001"), Decimal("100.00"), "bad-notional")

            with pytest.raises(PrecisionError):
                await service.place_order(bad_tick)
            with pytest.raises(PrecisionError):
                await service.place_order(bad_lot)
            with pytest.raises(PrecisionError):
                await service.place_order(bad_notional)
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_coinstore_cancel_cancel_all_and_status_update_persist_orders():
    settings = _settings()
    database = Database(settings)
    try:
        async with database.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with database.session() as session:
            await _seed_account_and_pair(session, settings)
            service = CoinstoreExecutionService(settings, session)
            client = RecordingCoinstoreClient()
            service._client = client
            service._precision = {"BTC/USDT": SymbolPrecision("BTC/USDT", "BTCUSDT", Decimal("0.01"), Decimal("0.001"), Decimal("0.001"), Decimal("10"), 2, 3)}

            cancel = await service.cancel_order(CancelIntent(ExecutionVenue.coinstore, "BTCUSDT", "cid", "coinstore-1"))
            cancelled = await service.cancel_all_orders("BTC/USDT")
            status = await service.get_order_status(CancelIntent(ExecutionVenue.coinstore, "BTCUSDT", "cid", "coinstore-1"))

            assert cancel.status == NormalizedOrderStatus.cancelled
            assert cancelled[0].status == NormalizedOrderStatus.cancelled
            assert status.status == NormalizedOrderStatus.partially_filled
            assert client.cancel_all_symbols == ["BTC/USDT"]
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_coinstore_execution_rejects_duplicate_and_self_crossing_orders():
    settings = _settings()
    database = Database(settings)
    try:
        async with database.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with database.session() as session:
            await _seed_account_and_pair(session, settings)
            service = CoinstoreExecutionService(settings, session)
            service._client = RecordingCoinstoreClient()
            service._precision = {"BTC/USDT": SymbolPrecision("BTC/USDT", "BTCUSDT", Decimal("0.01"), Decimal("0.001"), Decimal("0.001"), Decimal("10"), 2, 3)}
            await service.place_order(OrderIntent(ExecutionVenue.coinstore, "BTC/USDT", ExecutionSide.sell, ExecutionOrderType.limit, Decimal("0.100"), Decimal("100.00"), "existing-sell"))

            with pytest.raises(CoinstoreSafetyError, match="duplicate_client_order_id"):
                await service.place_order(OrderIntent(ExecutionVenue.coinstore, "BTC/USDT", ExecutionSide.sell, ExecutionOrderType.limit, Decimal("0.100"), Decimal("100.00"), "existing-sell"))
            with pytest.raises(CoinstoreSafetyError, match="self_trade_prevention"):
                await service.place_order(OrderIntent(ExecutionVenue.coinstore, "BTC/USDT", ExecutionSide.buy, ExecutionOrderType.limit, Decimal("0.100"), Decimal("100.00"), "crossing-buy"))
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_coinstore_live_reconciliation_reports_stale_and_orphan_orders():
    settings = _settings()
    database = Database(settings)
    try:
        async with database.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with database.session() as session:
            await _seed_account_and_pair(session, settings)
            service = CoinstoreExecutionService(settings, session)
            service._client = RecordingCoinstoreClient()
            service._precision = {"BTC/USDT": SymbolPrecision("BTC/USDT", "BTCUSDT", Decimal("0.01"), Decimal("0.001"), Decimal("0.001"), Decimal("10"), 2, 3)}
            account = await service.account()
            pair = await service._trading_pair("BTC/USDT")
            stale = models.Order(client_order_id="stale-orphan", exchange_order_id="orphan-1", exchange_account_id=account.id, trading_pair_id=pair.id, side=models.OrderSide.buy, order_type=models.OrderType.limit, status=models.OrderStatus.open, price=Decimal("99"), quantity=Decimal("0.1"), filled_quantity=Decimal("0"), metadata_json={})
            session.add(stale)
            await session.flush()
            stale.updated_at = __import__("datetime").datetime(2020, 1, 1, tzinfo=__import__("datetime").timezone.utc)
            report = await service.reconcile_live(["BTC/USDT"])

            assert "stale-orphan" in report.stale_order_ids
            assert "stale-orphan" in report.orphan_order_ids
            assert any(mismatch.category == "order" for mismatch in report.mismatches)
    finally:
        await database.close()


def test_coinstore_validation_layer_rejects_invalid_order_contract():
    service = CoinstoreExecutionService(_settings(), None)  # type: ignore[arg-type]
    service.validation.verify_endpoints()
    service.validation.verify_signing(ExecutionCredentials("key", "secret"))
    service.validation.verify_order_response({"data": {"orderId": "ok"}})
    service.validation.verify_balance_response([{"asset": "USDT", "available": "1", "total": "1"}])
    service.validation.verify_trade_response([{"tradeId": "t-1", "execQty": "1"}])
    with pytest.raises(CoinstoreValidationError):
        service.validation.verify_order_response({"status": "ok"})
