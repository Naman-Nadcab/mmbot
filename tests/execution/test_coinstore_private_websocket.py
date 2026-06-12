import secrets
from decimal import Decimal

import pytest
from sqlalchemy import select

from mmbot.core.config import Settings
from mmbot.db import models
from mmbot.db.models import Base
from mmbot.db.session import Database
from mmbot.execution.coinstore import CoinstoreExecutionService
from mmbot.execution.coinstore_ws import CoinstorePrivateWebSocketClient
from mmbot.execution.signing import ExecutionCredentials
from mmbot.security.secrets import SecretCipher


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


async def _seed(session, settings):
    cipher = SecretCipher(settings)
    account = models.ExchangeAccount(exchange_name="coinstore", account_alias="primary", environment="production", api_key_ciphertext=cipher.encrypt("key"), api_secret_ciphertext=cipher.encrypt("secret"), encryption_key_id=cipher.key_id, permissions=["trade"], is_enabled=True)
    pair = models.TradingPair(exchange_name="coinstore", base_asset="BTC", quote_asset="USDT", normalized_symbol="BTC/USDT", venue_symbol="BTCUSDT", price_precision=2, quantity_precision=3, min_order_size=Decimal("0.001"), min_notional=Decimal("10"), tick_size=Decimal("0.01"), lot_size=Decimal("0.001"), is_enabled=True)
    session.add_all([account, pair])
    await session.flush()


def test_coinstore_private_websocket_auth_and_subscribe_payloads():
    client = CoinstorePrivateWebSocketClient(_settings(), ExecutionCredentials("key", "secret"))
    auth = client.auth_payload(expires=1700000000000)
    sub = client.subscribe_payload()

    assert auth[0] == "auth"
    assert auth[1]["header"]["type"] == 1001
    assert auth[1]["body"]["apiKey"] == "key"
    assert auth[1]["body"]["expires"] == "1700000000000"
    assert len(auth[1]["body"]["signature"]) == 64
    assert sub[0] == "subscribe"
    assert sub[1]["body"]["topics"] == [{"topic": "match"}]


@pytest.mark.asyncio
async def test_coinstore_private_messages_persist_order_fill_and_balance_updates():
    settings = _settings()
    database = Database(settings)
    try:
        async with database.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with database.session() as session:
            await _seed(session, settings)
            service = CoinstoreExecutionService(settings, session)

            counts = await service.handle_private_message(
                {
                    "body": {
                        "data": [
                            {"messageType": 3002, "asset": "USDT", "totalBalance": "1000", "available": "900", "reserved": "100"},
                            {"messageType": 3004, "symbol": "BTCUSDT", "orderId": "o-1", "clientOrderId": "cid-1", "price": "100.00", "quantity": "0.100", "leftQuantity": "0.050", "side": 1, "orderType": 1, "orderStatus": 3, "matchQty": "0.050"},
                            {"symbol": "BTCUSDT", "orderId": "o-1", "clientOrderId": "cid-1", "price": "100.00", "quantity": "0.100", "side": 1, "orderType": 1, "orderStatus": 3, "matchQty": "0.050", "matchId": "m-1", "tradeId": "t-1", "fee": "0.01", "matchTime": 1700000000000},
                        ]
                    }
                }
            )

            assert counts == {"orders": 2, "trades": 1, "fills": 1, "balances": 1}
            order = (await session.execute(select(models.Order).where(models.Order.client_order_id == "cid-1"))).scalar_one()
            trade = (await session.execute(select(models.Trade).where(models.Trade.exchange_trade_id == "t-1"))).scalar_one()
            inventory = (await session.execute(select(models.InventorySnapshot).where(models.InventorySnapshot.asset == "USDT"))).scalar_one()
            assert order.status == models.OrderStatus.partially_filled
            assert abs(trade.quantity - Decimal("0.050")) < Decimal("0.00000001")
            assert inventory.total_balance == Decimal("1000")
    finally:
        await database.close()
