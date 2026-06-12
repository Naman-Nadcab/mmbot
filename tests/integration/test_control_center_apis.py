import json
import secrets
import time

import jwt
import pytest
from httpx import ASGITransport, AsyncClient

from mmbot.api.dependencies import get_database, get_redis, get_session
from mmbot.api.main import create_app
from mmbot.core.config import Settings, get_settings
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

    async def delete(self, key):
        self.data.pop(key, None)
        return 1

    async def publish(self, channel, payload):
        self.published.append((channel, json.loads(payload)))
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


def _token(settings: Settings, roles: list[str], permissions: list[str]) -> str:
    return jwt.encode({"sub": "admin", "roles": roles, "permissions": permissions, "exp": int(time.time()) + 3600}, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


@pytest.mark.asyncio
async def test_control_apis_persist_runtime_events_and_publish_commands():
    settings = _settings()
    database = Database(settings)
    redis = MemoryRedisManager()
    async with database.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    app = create_app()
    app.dependency_overrides[get_database] = lambda: database
    app.dependency_overrides[get_redis] = lambda: redis
    app.dependency_overrides[get_settings] = lambda: settings

    async def override_session():
        async with database.session(actor_service="api") as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    headers = {"Authorization": f"Bearer {_token(settings, ['platform_admin'], ['config:write', 'operations:read', 'risk:write', 'incident:write'])}"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        config_response = await client.put("/admin/config/spread", headers=headers, json={"config": {"base_spread_bps": 35}})
        assert config_response.status_code == 200
        assert config_response.json()["config"]["base_spread_bps"] == 35
        assert config_response.json()["config"]["min_spread_bps"] > 0

        strategy_response = await client.post("/admin/strategy/command", headers=headers, json={"command": "pause", "confirmation": "pause", "reason": "operator pause"})
        assert strategy_response.status_code == 200
        assert strategy_response.json()["event"]["command_id"]

        emergency_response = await client.post("/admin/emergency/disable-trading", headers=headers, json={"confirmation": "disable", "reason": "operator disable"})
        assert emergency_response.status_code == 200
        assert emergency_response.json()["event"]["command_id"]

        events = (await client.get("/operations/runtime-events", headers=headers)).json()["items"]
        assert any(item["event_type"] == "runtime_config_updated" for item in events)
        assert any(item["event_type"] == "runtime_command" and item["status"] == "published" for item in events)
        assert any(channel == "engine.commands.market-maker-engine" for channel, _ in redis.client.published)
    await database.close()
