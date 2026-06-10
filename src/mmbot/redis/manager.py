from __future__ import annotations

import json
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import redis.asyncio as redis
from tenacity import retry, stop_after_attempt, wait_exponential

from mmbot.core.config import Settings


class RedisManager:
    def __init__(self, settings: Settings):
        self.client = redis.from_url(settings.REDIS_URL, password=settings.REDIS_PASSWORD, socket_timeout=settings.REDIS_SOCKET_TIMEOUT_SECONDS, decode_responses=True)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=0.2, max=2))
    async def health_check(self) -> bool:
        return bool(await self.client.ping())

    async def close(self) -> None:
        await self.client.aclose()


class CacheManager:
    def __init__(self, client: redis.Redis):
        self.client = client

    async def set_json(self, key: str, value: Any, ttl_seconds: int | None = None) -> None:
        await self.client.set(key, json.dumps(value, default=str, separators=(",", ":")), ex=ttl_seconds)

    async def get_json(self, key: str) -> Any | None:
        value = await self.client.get(key)
        return None if value is None else json.loads(value)

    async def delete(self, key: str) -> None:
        await self.client.delete(key)


class PubSubFramework:
    def __init__(self, client: redis.Redis):
        self.client = client

    async def publish(self, channel: str, payload: Any) -> int:
        return int(await self.client.publish(channel, json.dumps(payload, default=str, separators=(",", ":"))))

    @asynccontextmanager
    async def subscribe(self, *channels: str) -> AsyncIterator[redis.client.PubSub]:
        pubsub = self.client.pubsub()
        await pubsub.subscribe(*channels)
        try:
            yield pubsub
        finally:
            await pubsub.unsubscribe(*channels)
            await pubsub.close()


class DistributedLockManager:
    RELEASE_SCRIPT = """
    if redis.call('get', KEYS[1]) == ARGV[1] then
        return redis.call('del', KEYS[1])
    end
    return 0
    """

    def __init__(self, client: redis.Redis):
        self.client = client

    async def acquire(self, key: str, ttl_ms: int) -> str | None:
        token = str(uuid.uuid4())
        acquired = await self.client.set(key, token, nx=True, px=ttl_ms)
        return token if acquired else None

    async def release(self, key: str, token: str) -> bool:
        return bool(await self.client.eval(self.RELEASE_SCRIPT, 1, key, token))


class RateLimitStorage:
    def __init__(self, client: redis.Redis):
        self.client = client

    async def allow(self, key: str, limit: int, window_seconds: int) -> tuple[bool, int]:
        bucket = f"rate:{key}:{int(time.time() // window_seconds)}"
        count = int(await self.client.incr(bucket))
        if count == 1:
            await self.client.expire(bucket, window_seconds)
        return count <= limit, max(0, limit - count)


class SessionStorage:
    def __init__(self, client: redis.Redis):
        self.client = client

    async def create(self, session_id: str, payload: dict[str, Any], ttl_seconds: int) -> None:
        await self.client.set(f"session:{session_id}", json.dumps(payload, default=str), ex=ttl_seconds)

    async def get(self, session_id: str) -> dict[str, Any] | None:
        data = await self.client.get(f"session:{session_id}")
        return None if data is None else json.loads(data)

    async def revoke(self, session_id: str) -> None:
        await self.client.delete(f"session:{session_id}")


class EngineCommunicationLayer:
    def __init__(self, pubsub: PubSubFramework, cache: CacheManager):
        self.pubsub = pubsub
        self.cache = cache

    async def publish_event(self, engine: str, event_type: str, payload: dict[str, Any]) -> int:
        event = {"engine": engine, "event_type": event_type, "payload": payload, "published_at": time.time()}
        await self.cache.set_json(f"engine:last_event:{engine}", event, ttl_seconds=86400)
        return await self.pubsub.publish(f"engine.events.{engine}", event)

    async def publish_command(self, engine: str, command_type: str, payload: dict[str, Any]) -> int:
        command = {"engine": engine, "command_type": command_type, "payload": payload, "published_at": time.time()}
        return await self.pubsub.publish(f"engine.commands.{engine}", command)
