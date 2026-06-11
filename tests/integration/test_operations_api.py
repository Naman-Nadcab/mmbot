import json
import secrets
import time
from datetime import datetime, timezone
from decimal import Decimal

import jwt
import pytest
from httpx import ASGITransport, AsyncClient

from mmbot.api.dependencies import get_database, get_redis, get_session
from mmbot.api.main import create_app
from mmbot.api.routes import _send_operation_events
from mmbot.core.config import Settings, get_settings
from mmbot.db import models
from mmbot.db.models import Base
from mmbot.db.session import Database


class MemoryRedisClient:
    def __init__(self):
        self.data = {}

    async def ping(self):
        return True

    async def get(self, key):
        return self.data.get(key)

    async def set(self, key, value, ex=None):
        self.data[key] = value
        return True

    async def delete(self, key):
        self.data.pop(key, None)
        return 1

    async def scan_iter(self, match=None):
        prefix = (match or "").replace("*", "")
        for key in list(self.data):
            if not match or key.startswith(prefix):
                yield key

    async def aclose(self):
        return None


class MemoryRedisManager:
    def __init__(self):
        self.client = MemoryRedisClient()

    async def health_check(self):
        return True

    async def close(self):
        return None


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


def _token(settings: Settings) -> str:
    return jwt.encode({"sub": "operator", "roles": ["read_only_analyst"], "permissions": ["operations:read"], "exp": int(time.time()) + 3600}, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def _admin_token(settings: Settings) -> str:
    return jwt.encode({"sub": "admin", "roles": ["platform_admin"], "permissions": ["config:write"], "exp": int(time.time()) + 3600}, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


@pytest.mark.asyncio
async def test_operations_endpoints_return_real_state():
    settings = _settings()
    database = Database(settings)
    redis = MemoryRedisManager()
    async with database.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = database.session_factory
    async with session_factory() as session:
        account = models.ExchangeAccount(exchange_name="paper", account_alias="paper", environment="sandbox", api_key_ciphertext=b"k", api_secret_ciphertext=b"s", encryption_key_id="test", permissions=[], is_enabled=True)
        pair = models.TradingPair(exchange_name="paper", base_asset="BTC", quote_asset="USDT", normalized_symbol="BTC/USDT", venue_symbol="BTCUSDT", price_precision=8, quantity_precision=8)
        session.add_all([account, pair])
        await session.flush()
        order = models.Order(client_order_id="cid", exchange_order_id="paper-1", exchange_account_id=account.id, trading_pair_id=pair.id, side=models.OrderSide.buy, order_type=models.OrderType.limit, status=models.OrderStatus.open, price=Decimal("100"), quantity=Decimal("1"), filled_quantity=Decimal("0"), metadata_json={})
        session.add(order)
        await session.flush()
        session.add(models.Trade(order_id=order.id, exchange_trade_id="tid", exchange_account_id=account.id, trading_pair_id=pair.id, side=models.OrderSide.buy, price=Decimal("100"), quantity=Decimal("1"), fee_amount=Decimal("0.1"), traded_at=datetime.now(timezone.utc), metadata_json={}))
        session.add(models.Position(exchange_account_id=account.id, trading_pair_id=pair.id, asset="BTC", side=models.PositionSide.long, quantity=Decimal("1"), realized_pnl=Decimal("2"), unrealized_pnl=Decimal("3"), mark_price=Decimal("100")))
        session.add(models.InventorySnapshot(exchange_account_id=account.id, asset="BTC", total_balance=Decimal("1"), available_balance=Decimal("1"), reserved_balance=Decimal("0"), valuation_asset="USDT", valuation_price=Decimal("100"), valuation_amount=Decimal("100"), captured_at=datetime.now(timezone.utc), metadata_json={}))
        session.add(models.RiskEvent(severity=models.RiskSeverity.low, event_type="TEST", source_component="test", message="risk", metadata_json={}))
        await session.commit()
    await redis.client.set("engine:health:market-maker-engine", json.dumps({"status": "healthy", "runtime": {"metrics": {"counters": {"reconciliation.runs": 1, "reconciliation.mismatches": 0}}}}))
    await redis.client.set("engine:health:market-data-engine", json.dumps({"status": "healthy", "runtime": {"last_message_timestamp": {"binance:BTC/USDT": "2026-06-11T09:00:00+00:00"}, "active_subscriptions": 4, "websocket_state": "active"}}))

    app = create_app()
    app.dependency_overrides[get_database] = lambda: database
    app.dependency_overrides[get_redis] = lambda: redis
    app.dependency_overrides[get_settings] = lambda: settings

    async def override_session():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_session

    auth_headers = {"Authorization": f"Bearer {_token(settings)}"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get("/operations/orders")).status_code == 401
        assert (await client.get("/admin/kill-switch/status")).status_code == 401
        assert (await client.get("/operations/engines", headers=auth_headers)).json()["engines"]["market-maker-engine"]["status"] == "healthy"
        assert len((await client.get("/operations/orders", headers=auth_headers)).json()["items"]) == 1
        assert len((await client.get("/operations/trades", headers=auth_headers)).json()["items"]) == 1
        assert len((await client.get("/operations/positions", headers=auth_headers)).json()["items"]) == 1
        assert len((await client.get("/operations/inventory", headers=auth_headers)).json()["items"]) == 1
        assert (await client.get("/operations/pnl", headers=auth_headers)).json()["total"] == 5.0
        assert len((await client.get("/operations/risk-events", headers=auth_headers)).json()["items"]) == 1
        assert (await client.get("/operations/reconciliation", headers=auth_headers)).json()["status"] == "ok"
        assert (await client.get("/operations/exchanges", headers=auth_headers)).json()["exchanges"]["binance"]["status"] == "connected"
        canary_limits = (await client.get("/operations/canary-limits", headers=auth_headers)).json()
        assert canary_limits["max_canary_notional"] > 0
        assert canary_limits["max_canary_position"] > 0
        admin_headers = {"Authorization": f"Bearer {_admin_token(settings)}"}
        kill_state = (await client.post("/admin/kill-switch/enable", headers=admin_headers, json={"reason": "test"})).json()
        assert kill_state["active"] is True
        assert (await client.get("/admin/kill-switch/status", headers=admin_headers)).json()["active"] is True
        assert (await client.post("/admin/kill-switch/disable", headers=admin_headers)).json()["active"] is False

        infrastructure = (await client.get("/operations/infrastructure", headers=auth_headers)).json()
        assert infrastructure["database"] == "healthy"
        assert infrastructure["redis"] == "healthy"
        assert infrastructure["database_latency_ms"] >= 0
        assert infrastructure["redis_latency_ms"] >= 0
    await database.close()


class MemoryWebSocket:
    def __init__(self):
        self.messages = []

    async def send_text(self, value):
        self.messages.append(json.loads(value))


@pytest.mark.asyncio
async def test_operations_websocket_event_producer_streams_existing_state():
    database = Database(_settings())
    redis = MemoryRedisManager()
    try:
        async with database.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with database.session() as session:
            account = models.ExchangeAccount(exchange_name="paper", account_alias="paper", environment="sandbox", api_key_ciphertext=b"k", api_secret_ciphertext=b"s", encryption_key_id="test", permissions=[], is_enabled=True)
            pair = models.TradingPair(exchange_name="paper", base_asset="BTC", quote_asset="USDT", normalized_symbol="BTC/USDT", venue_symbol="BTCUSDT", price_precision=8, quantity_precision=8)
            session.add_all([account, pair])
            await session.flush()
            session.add(models.Position(exchange_account_id=account.id, trading_pair_id=pair.id, asset="BTC", side=models.PositionSide.long, quantity=Decimal("1"), realized_pnl=Decimal("2"), unrealized_pnl=Decimal("3"), mark_price=Decimal("100")))
        await redis.client.set("engine:health:market-maker-engine", json.dumps({"status": "healthy", "runtime": {"metrics": {"counters": {"reconciliation.runs": 1, "reconciliation.mismatches": 0, "risk.approvals": 1}}}}))
        await redis.client.set("engine:health:market-data-engine", json.dumps({"status": "healthy", "runtime": {"last_message_timestamp": {"binance:BTC/USDT": "2026-06-11T09:00:00+00:00"}, "active_subscriptions": 4, "websocket_state": "active", "metrics": {"counters": {"market_data.reconnect_count": 1}}}}))
        websocket = MemoryWebSocket()
        async with database.session() as session:
            await _send_operation_events(websocket, session, redis, {"orders": set(), "trades": set(), "risk_events": set(), "risk_approvals": 0, "risk_rejections": 0, "reconciliation_runs": 0, "reconnect_count": 0})
        assert any(message["type"] == "engine_health" for message in websocket.messages)
        assert any(message["type"] == "exchange_connectivity" for message in websocket.messages)
        assert any(message["type"] == "risk_approved" for message in websocket.messages)
        assert any(message["type"] == "positions" for message in websocket.messages)
    finally:
        await database.close()
