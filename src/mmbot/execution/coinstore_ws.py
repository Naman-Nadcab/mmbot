from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

import websockets

from mmbot.core.config import Settings
from mmbot.execution.signing import ExecutionCredentials, coinstore_signature
from mmbot.exchanges.registry import get_exchange_definition

logger = logging.getLogger(__name__)


class CoinstorePrivateWebSocketClient:
    def __init__(self, settings: Settings, credentials: ExecutionCredentials):
        definition = get_exchange_definition("coinstore")
        self.websocket_url = definition.websocket_url
        self.max_reconnect_delay_seconds = settings.EXCHANGE_RECONNECT_MAX_DELAY_SECONDS
        self.heartbeat_interval_seconds = settings.exchange.heartbeat_interval_seconds if hasattr(settings, "exchange") else 20
        self.credentials = credentials
        self._stopped = asyncio.Event()
        self.connected = False
        self.connection_attempts = 0
        self.subscriptions_sent = 0
        self.messages_received = 0

    def stop(self) -> None:
        self._stopped.set()

    def auth_payload(self, expires: int | None = None) -> list[Any]:
        expires = expires or int(time.time() * 1000)
        signature = self._signature(expires)
        return [
            "auth",
            {
                "header": {"type": 1001},
                "body": {
                    "apiKey": self.credentials.api_key,
                    "expires": str(expires),
                    "signature": signature,
                },
            },
        ]

    def subscribe_payload(self) -> list[Any]:
        return [
            "subscribe",
            {
                "header": {"type": 1003},
                "body": {"topics": [{"topic": "match"}]},
            },
        ]

    async def run(self, handler: Callable[[dict[str, Any]], Awaitable[None]]) -> None:
        delay = 1.0
        while not self._stopped.is_set():
            try:
                self.connection_attempts += 1
                async with websockets.connect(self.websocket_url, ping_interval=self.heartbeat_interval_seconds, ping_timeout=self.heartbeat_interval_seconds) as ws:
                    self.connected = True
                    await self._authenticate_and_subscribe(ws)
                    delay = 1.0
                    async for raw in ws:
                        message = json.loads(raw) if isinstance(raw, str) else {"binary": raw.hex()}
                        self.messages_received += 1
                        if self._is_heartbeat(message):
                            await self._heartbeat_response(ws, message)
                            continue
                        await handler(message)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.connected = False
                logger.warning("coinstore_private_websocket_reconnect", extra={"error": str(exc), "delay": delay, "attempt": self.connection_attempts})
                await asyncio.sleep(delay)
                delay = min(self.max_reconnect_delay_seconds, delay * 2)

    async def _authenticate_and_subscribe(self, ws: Any) -> None:
        await ws.send(json.dumps(self.auth_payload(), separators=(",", ":")))
        await ws.send(json.dumps(self.subscribe_payload(), separators=(",", ":")))
        self.subscriptions_sent += 1
        logger.info("coinstore_private_websocket_subscribed", extra={"topic": "match", "subscriptions_sent": self.subscriptions_sent})

    async def _heartbeat_response(self, ws: Any, message: dict[str, Any]) -> None:
        if str(message.get("op") or message.get("event") or message.get("type") or "").lower() == "ping":
            await ws.send(json.dumps({"op": "pong", "ts": message.get("ts") or int(time.time() * 1000)}, separators=(",", ":")))

    def _is_heartbeat(self, message: dict[str, Any]) -> bool:
        token = str(message.get("op") or message.get("event") or message.get("type") or message.get("T") or "").lower()
        return token in {"ping", "pong", "heartbeat"}

    def _signature(self, expires: int) -> str:
        payload = str(expires)
        return coinstore_signature(self.credentials.api_secret, expires, payload)
