import json
import secrets
import time

import jwt
import pytest
from httpx import ASGITransport, AsyncClient
from decimal import Decimal

from mmbot.api.dependencies import get_database, get_redis, get_session
from mmbot.api.main import create_app
from mmbot.core.config import Settings, get_settings
from mmbot.execution.models import Balance, ExecutionVenue
from mmbot.db.models import Base
from mmbot.db.session import Database


class MemoryRedisClient:
    def __init__(self):
        self.data = {}
        self.published = []

    async def ping(self):
        return True

    async def get(self, key):
        return self.data.get(key)

    async def set(self, key, value, ex=None):
        self.data[key] = value
        return True

    async def publish(self, channel, payload):
        self.published.append((channel, json.loads(payload)))
        return 1

    async def scan_iter(self, match=None):
        return
        yield


class MemoryRedisManager:
    def __init__(self):
        self.client = MemoryRedisClient()

    async def health_check(self):
        return True


class FakeCoinstoreClient:
    async def sync_balances(self):
        return [
            Balance(ExecutionVenue.coinstore, "USDT", Decimal("1000"), Decimal("900"), Decimal("100"), {"asset": "USDT", "available": "900", "total": "1000"}),
            Balance(ExecutionVenue.coinstore, "BTC", Decimal("0.5"), Decimal("0.4"), Decimal("0.1"), {"asset": "BTC", "available": "0.4", "total": "0.5"}),
        ]


async def fake_service_client(self):
    return FakeCoinstoreClient()


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
    return jwt.encode({"sub": "admin", "roles": ["platform_admin"], "permissions": ["operations:read", "config:write"], "exp": int(time.time()) + 3600}, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


@pytest.mark.asyncio
async def test_exchange_management_connect_status_test_and_remove(monkeypatch):
    settings = _settings()
    database = Database(settings)
    redis = MemoryRedisManager()
    async with database.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async def fake_test(row, settings):
        return {"status": "connected", "rest_status": "connected", "websocket_status": "connected", "private_ws_status": "connected", "last_tested_at": "2026-06-13T00:00:00+00:00", "error": None}

    monkeypatch.setattr("mmbot.api.routes._test_exchange_connection", fake_test)
    monkeypatch.setattr("mmbot.execution.coinstore.CoinstoreExecutionService.client", fake_service_client)
    app = create_app()
    app.dependency_overrides[get_database] = lambda: database
    app.dependency_overrides[get_redis] = lambda: redis
    app.dependency_overrides[get_settings] = lambda: settings

    async def override_session():
        async with database.session(actor_service="api") as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    headers = {"Authorization": f"Bearer {_token(settings)}"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        connect = await client.post("/exchanges/connect", headers=headers, json={"exchange_name": "coinstore", "account_alias": "primary", "environment": "production", "api_key": "abcd1234secret", "api_secret": "secret", "passphrase": "pass", "permissions": ["read", "trade"], "enabled": True})
        assert connect.status_code == 200
        payload = connect.json()
        assert payload["has_api_key"] is True
        assert payload["has_api_secret"] is True
        assert payload["api_key_masked"] == "abcd********cret"
        assert "api_secret" not in payload

        update = await client.post("/exchanges/connect", headers=headers, json={"exchange_name": "coinstore", "account_alias": "primary", "environment": "production", "api_key": "wxyz9876secret", "api_secret": "new-secret", "passphrase": "new-pass", "permissions": ["read"], "enabled": True})
        assert update.status_code == 200
        assert update.json()["api_key_masked"] == "wxyz********cret"

        listing = await client.get("/exchanges", headers=headers)
        assert listing.status_code == 200
        coinstore = next(item for item in listing.json()["items"] if item["exchange_name"] == "coinstore")
        assert coinstore["accounts"][0]["api_key_masked"] == "wxyz********cret"

        tested = await client.post("/exchanges/test", headers=headers, json={"exchange_name": "coinstore", "account_alias": "primary", "environment": "production"})
        assert tested.status_code == 200
        assert tested.json()["connection_status"] == "connected"
        assert tested.json()["rest_status"] == "connected"
        assert tested.json()["private_ws_status"] == "connected"

        status = await client.get("/exchanges/status", headers=headers)
        assert status.status_code == 200
        assert status.json()["items"][0]["connection_status"] == "connected"

        sync = await client.post("/exchanges/coinstore/sync", headers=headers, json={"account_alias": "primary", "environment": "production"})
        assert sync.status_code == 200
        assert sync.json()["rows_written"] == 2
        assert len(sync.json()["balances"]) == 2

        balances = await client.get("/exchanges/coinstore/balances?account_alias=primary&environment=production", headers=headers)
        assert balances.status_code == 200
        assert {item["asset"] for item in balances.json()["balances"]} == {"USDT", "BTC"}

        removed = await client.request("DELETE", "/exchanges/remove", headers=headers, json={"exchange_name": "coinstore", "account_alias": "primary", "environment": "production", "confirmation": "remove"})
        assert removed.status_code == 200
        assert removed.json()["removed"] is True

        after = await client.get("/exchanges/status", headers=headers)
        assert after.json()["items"] == []
    await database.close()
