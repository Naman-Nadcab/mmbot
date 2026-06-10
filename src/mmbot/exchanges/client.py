from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import httpx
import websockets
from tenacity import retry, stop_after_attempt, wait_exponential

from mmbot.core.exceptions import ExchangeError
from mmbot.exchanges.auth import HmacSigner
from mmbot.exchanges.rate_limit import AsyncRateLimiter
from mmbot.exchanges.types import ExchangeDefinition

logger = logging.getLogger(__name__)


class RestClient:
    def __init__(self, definition: ExchangeDefinition, timeout_seconds: float, signer: HmacSigner | None = None):
        self.definition = definition
        self.signer = signer
        self.rate_limiter = AsyncRateLimiter(definition.rate_limit)
        self.client = httpx.AsyncClient(base_url=definition.rest_base_url, timeout=timeout_seconds)

    async def close(self) -> None:
        await self.client.aclose()

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=0.2, max=2))
    async def request(self, method: str, path: str, *, params: dict[str, Any] | None = None, json_body: dict[str, Any] | None = None, signed: bool = False) -> Any:
        await self.rate_limiter.acquire()
        headers: dict[str, str] = {}
        if signed:
            if self.signer is None:
                raise ExchangeError("signed request requires credentials")
            payload = json.dumps(json_body or params or {}, separators=(",", ":"), sort_keys=True)
            headers.update(self.signer.headers(payload))
        response = await self.client.request(method, path, params=params, json=json_body, headers=headers)
        if response.status_code >= 400:
            raise ExchangeError(f"{self.definition.name.value} REST error {response.status_code}: {response.text[:300]}")
        if not response.content:
            return None
        content_type = response.headers.get("content-type", "")
        return response.json() if "json" in content_type else response.text

    async def health(self) -> bool:
        await self.request("GET", self.definition.health_path)
        return True


class WebSocketClient:
    def __init__(self, definition: ExchangeDefinition, max_reconnect_delay_seconds: float, heartbeat_interval_seconds: float):
        self.definition = definition
        self.max_reconnect_delay_seconds = max_reconnect_delay_seconds
        self.heartbeat_interval_seconds = heartbeat_interval_seconds
        self._stopped = asyncio.Event()

    def stop(self) -> None:
        self._stopped.set()

    async def stream(self, subscribe_payload: dict[str, Any] | list[dict[str, Any]] | None = None) -> AsyncIterator[dict[str, Any]]:
        delay = 1.0
        while not self._stopped.is_set():
            try:
                async with websockets.connect(self.definition.websocket_url, ping_interval=self.heartbeat_interval_seconds, ping_timeout=self.heartbeat_interval_seconds) as ws:
                    if subscribe_payload is not None:
                        payloads = subscribe_payload if isinstance(subscribe_payload, list) else [subscribe_payload]
                        for payload in payloads:
                            await ws.send(json.dumps(payload, separators=(",", ":")))
                    delay = 1.0
                    async for message in ws:
                        yield json.loads(message) if isinstance(message, str) else {"binary": message.hex()}
            except Exception as exc:
                logger.warning("websocket_reconnect", extra={"exchange": self.definition.name.value, "error": str(exc), "delay": delay})
                await asyncio.sleep(delay)
                delay = min(self.max_reconnect_delay_seconds, delay * 2)

    async def run(self, subscribe_payload: dict[str, Any] | list[dict[str, Any]] | None, handler: Callable[[dict[str, Any]], Awaitable[None]]) -> None:
        async for message in self.stream(subscribe_payload):
            await handler(message)
