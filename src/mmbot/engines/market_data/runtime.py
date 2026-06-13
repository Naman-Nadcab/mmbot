from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError, PendingRollbackError
from sqlalchemy.ext.asyncio import AsyncSession

from mmbot.core.config import RuntimeConfig, Settings, default_runtime_config
from mmbot.db import models
from mmbot.engines.market_data.engine import MarketDataEngine
from mmbot.execution.models import ExecutionVenue
from mmbot.exchanges.types import Kline, OrderBookLevel, OrderBookSnapshot, Ticker, TradeTick
from mmbot.observability.metrics import RuntimeMetrics
from mmbot.redis.manager import EngineCommunicationLayer
from mmbot.runtime.events import publish_runtime_ack
from mmbot.websocket.connectors import StreamKind, StreamSubscription, VenueWebSocketConnector

logger = logging.getLogger(__name__)


class MarketDataNormalizer:
    def normalize(self, venue: ExecutionVenue, symbol: str, message: dict[str, Any]) -> tuple[str, Ticker | TradeTick | OrderBookSnapshot | Kline] | None:
        if venue is ExecutionVenue.coinstore:
            return self._normalize_coinstore(symbol, message)
        event = str(message.get("e") or message.get("event") or message.get("channel") or message.get("type") or "").lower()
        payload = message.get("data") if isinstance(message.get("data"), dict) else message.get("result") if isinstance(message.get("result"), dict) else message
        if any(token in event for token in ("depth", "order_book", "level2")) or "bids" in payload or "asks" in payload:
            return "orderbook", self._orderbook(venue, symbol, payload)
        if any(token in event for token in ("ticker", "tickers")) or self._has_scalar_ticker_fields(message):
            return "ticker", self._ticker(venue, symbol, payload)
        if any(token in event for token in ("trade", "match", "deals")) or "price" in payload and "quantity" in payload and "trade_id" in payload:
            return "trade", self._trade(venue, symbol, payload)
        if any(token in event for token in ("kline", "candle", "candlestick")):
            return "kline", self._kline(venue, symbol, payload)
        return None

    def _normalize_coinstore(self, symbol: str, message: dict[str, Any]) -> tuple[str, Ticker | TradeTick | OrderBookSnapshot | Kline] | None:
        if self._is_coinstore_control_message(message):
            return None
        payload = self._coinstore_payload(message)
        event = str(message.get("T") or message.get("type") or message.get("event") or message.get("channel") or payload.get("T") or payload.get("type") or payload.get("event") or payload.get("channel") or "").lower()
        normalized_symbol = self._coinstore_symbol(payload, symbol)
        if self._coinstore_is_orderbook(event, payload):
            return "orderbook", self._coinstore_orderbook(normalized_symbol, payload)
        if self._coinstore_is_trade(event, payload):
            return "trade", self._coinstore_trade(normalized_symbol, payload)
        if self._coinstore_is_ticker(event, payload):
            return "ticker", self._coinstore_ticker(normalized_symbol, payload)
        if self._coinstore_is_kline(event, payload):
            return "kline", self._coinstore_kline(normalized_symbol, payload)
        return None

    def _ticker(self, venue: ExecutionVenue, symbol: str, payload: dict[str, Any]) -> Ticker:
        now = datetime.now(timezone.utc)
        return Ticker(
            exchange=venue.value,
            symbol=symbol,
            bid_price=self._float(payload.get("b") or payload.get("bid") or payload.get("best_bid") or payload.get("bidPrice")),
            bid_size=self._float(payload.get("B") or payload.get("bidSize") or payload.get("best_bid_size")),
            ask_price=self._float(payload.get("a") or payload.get("ask") or payload.get("best_ask") or payload.get("askPrice")),
            ask_size=self._float(payload.get("A") or payload.get("askSize") or payload.get("best_ask_size")),
            last_price=self._float(payload.get("c") or payload.get("last") or payload.get("lastPrice") or payload.get("price")),
            volume_24h=self._float(payload.get("v") or payload.get("volume") or payload.get("baseVolume")),
            source_timestamp=self._timestamp(payload.get("E") or payload.get("time") or payload.get("ts")) or now,
        )

    def _coinstore_ticker(self, symbol: str, payload: dict[str, Any]) -> Ticker:
        now = datetime.now(timezone.utc)
        bid = payload.get("bid") or payload.get("bestBid") or payload.get("best_bid") or payload.get("bidPrice") or payload.get("b")
        ask = payload.get("ask") or payload.get("bestAsk") or payload.get("best_ask") or payload.get("askPrice") or payload.get("a")
        last = payload.get("last") or payload.get("lastPrice") or payload.get("close") or payload.get("price") or payload.get("c")
        return Ticker(
            exchange=ExecutionVenue.coinstore.value,
            symbol=symbol,
            bid_price=self._float(bid),
            bid_size=self._float(payload.get("bidSize") or payload.get("bid_size") or payload.get("B")),
            ask_price=self._float(ask),
            ask_size=self._float(payload.get("askSize") or payload.get("ask_size") or payload.get("A")),
            last_price=self._float(last),
            volume_24h=self._float(payload.get("volume") or payload.get("volume24h") or payload.get("baseVolume") or payload.get("v")),
            source_timestamp=self._timestamp(payload.get("time") or payload.get("ts") or payload.get("E")) or now,
        )

    def _trade(self, venue: ExecutionVenue, symbol: str, payload: dict[str, Any]) -> TradeTick:
        price = self._float(payload.get("p") or payload.get("price") or payload.get("trade_price")) or 0.0
        quantity = self._float(payload.get("q") or payload.get("quantity") or payload.get("size") or payload.get("amount")) or 0.0
        side_raw = str(payload.get("side") or payload.get("S") or ("sell" if payload.get("m") else "buy")).lower()
        return TradeTick(
            exchange=venue.value,
            symbol=symbol,
            trade_id=str(payload.get("t") or payload.get("trade_id") or payload.get("id") or uuid.uuid4()),
            price=price,
            quantity=quantity,
            side="sell" if side_raw in {"sell", "s"} else "buy",
            traded_at=self._timestamp(payload.get("T") or payload.get("time") or payload.get("ts")) or datetime.now(timezone.utc),
            metadata=payload,
        )

    def _coinstore_trade(self, symbol: str, payload: dict[str, Any]) -> TradeTick:
        side_raw = str(payload.get("takerSide") or payload.get("side") or payload.get("S") or "").lower()
        return TradeTick(
            exchange=ExecutionVenue.coinstore.value,
            symbol=symbol,
            trade_id=str(payload.get("tradeId") or payload.get("trade_id") or payload.get("id") or uuid.uuid4()),
            price=self._float(payload.get("price") or payload.get("p")) or 0.0,
            quantity=self._float(payload.get("volume") or payload.get("quantity") or payload.get("size") or payload.get("amount") or payload.get("q")) or 0.0,
            side="sell" if side_raw in {"sell", "s", "-1"} else "buy",
            traded_at=self._timestamp(payload.get("time") or payload.get("ts") or payload.get("T")) or datetime.now(timezone.utc),
            metadata=payload,
        )

    def _orderbook(self, venue: ExecutionVenue, symbol: str, payload: dict[str, Any]) -> OrderBookSnapshot:
        bids = payload.get("b") or payload.get("bids") or []
        asks = payload.get("a") or payload.get("asks") or []
        return OrderBookSnapshot(
            exchange=venue.value,
            symbol=symbol,
            bids=[OrderBookLevel(float(price), float(size)) for price, size, *_ in bids],
            asks=[OrderBookLevel(float(price), float(size)) for price, size, *_ in asks],
            source_timestamp=self._timestamp(payload.get("E") or payload.get("time") or payload.get("ts")) or datetime.now(timezone.utc),
            sequence=str(payload.get("u") or payload.get("sequence") or payload.get("lastUpdateId")) if (payload.get("u") or payload.get("sequence") or payload.get("lastUpdateId")) is not None else None,
        )

    def _coinstore_orderbook(self, symbol: str, payload: dict[str, Any]) -> OrderBookSnapshot:
        bids = payload.get("bids") or payload.get("b") or payload.get("bid") or []
        asks = payload.get("asks") or payload.get("a") or payload.get("ask") or []
        return OrderBookSnapshot(
            exchange=ExecutionVenue.coinstore.value,
            symbol=symbol,
            bids=[OrderBookLevel(float(price), float(size)) for price, size, *_ in bids],
            asks=[OrderBookLevel(float(price), float(size)) for price, size, *_ in asks],
            source_timestamp=self._timestamp(payload.get("time") or payload.get("ts") or payload.get("E")) or datetime.now(timezone.utc),
            sequence=str(payload.get("sequence") or payload.get("seq") or payload.get("u")) if (payload.get("sequence") or payload.get("seq") or payload.get("u")) is not None else None,
        )

    def _kline(self, venue: ExecutionVenue, symbol: str, payload: dict[str, Any]) -> Kline:
        k = payload.get("k") if isinstance(payload.get("k"), dict) else payload
        now = datetime.now(timezone.utc)
        return Kline(
            exchange=venue.value,
            symbol=symbol,
            interval=str(k.get("i") or k.get("interval") or "1m"),
            open_time=self._timestamp(k.get("t") or k.get("open_time")) or now,
            close_time=self._timestamp(k.get("T") or k.get("close_time")) or now,
            open_price=float(k.get("o") or k.get("open") or 0),
            high_price=float(k.get("h") or k.get("high") or 0),
            low_price=float(k.get("l") or k.get("low") or 0),
            close_price=float(k.get("c") or k.get("close") or 0),
            volume=float(k.get("v") or k.get("volume") or 0),
        )

    def _coinstore_kline(self, symbol: str, payload: dict[str, Any]) -> Kline:
        k = payload.get("k") if isinstance(payload.get("k"), dict) else payload
        now = datetime.now(timezone.utc)
        return Kline(
            exchange=ExecutionVenue.coinstore.value,
            symbol=symbol,
            interval=str(k.get("interval") or k.get("range") or "1m"),
            open_time=self._timestamp(k.get("open_time") or k.get("openTime") or k.get("t") or k.get("time") or k.get("ts")) or now,
            close_time=self._timestamp(k.get("close_time") or k.get("closeTime") or k.get("T") or k.get("time") or k.get("ts")) or now,
            open_price=float(k.get("open") or k.get("o") or 0),
            high_price=float(k.get("high") or k.get("h") or 0),
            low_price=float(k.get("low") or k.get("l") or 0),
            close_price=float(k.get("close") or k.get("c") or k.get("price") or 0),
            volume=float(k.get("volume") or k.get("v") or 0),
        )

    def _is_coinstore_control_message(self, message: dict[str, Any]) -> bool:
        tokens = {str(message.get(key) or "").lower() for key in ("T", "M", "op", "event", "type")}
        return any(token in {"ping", "pong", "heartbeat", "resp", "echo", "command.received", "sub.channel.success", "established"} for token in tokens)

    def _coinstore_payload(self, message: dict[str, Any]) -> dict[str, Any]:
        for key in ("data", "result", "tick", "payload", "body", "D"):
            value = message.get(key)
            if isinstance(value, dict):
                return value | {k: v for k, v in message.items() if k not in value}
            if isinstance(value, list) and value and isinstance(value[0], dict):
                return value[0] | {k: v for k, v in message.items() if k != key}
        return message

    def _coinstore_symbol(self, payload: dict[str, Any], default: str) -> str:
        raw = str(payload.get("symbol") or payload.get("currencyPair") or payload.get("channel") or default or "")
        if "@" in raw:
            raw = raw.split("@", 1)[0]
        raw = raw.upper().replace("-", "/").replace("_", "/")
        if "/" in raw:
            return raw
        default_compact = default.replace("/", "").upper()
        if raw and raw == default_compact:
            return default
        for quote in ("USDT", "USDC", "USD", "BTC", "ETH"):
            if raw.endswith(quote) and len(raw) > len(quote):
                return f"{raw[:-len(quote)]}/{quote}"
        return default

    def _coinstore_is_orderbook(self, event: str, payload: dict[str, Any]) -> bool:
        return any(token in event for token in ("depth", "orderbook", "order_book", "snapshot_depth")) or any(key in payload for key in ("bids", "asks"))

    def _coinstore_is_trade(self, event: str, payload: dict[str, Any]) -> bool:
        return any(token in event for token in ("trade", "deal", "future_tick")) or "tradeId" in payload or ("price" in payload and any(key in payload for key in ("volume", "quantity", "size")))

    def _coinstore_is_ticker(self, event: str, payload: dict[str, Any]) -> bool:
        return any(token in event for token in ("ticker", "indicator")) or any(key in payload for key in ("last", "lastPrice", "bid", "ask", "bestBid", "bestAsk", "close"))

    def _coinstore_is_kline(self, event: str, payload: dict[str, Any]) -> bool:
        return any(token in event for token in ("kline", "candle")) or {"open", "high", "low", "close"}.issubset(payload)

    def _timestamp(self, value: Any) -> datetime | None:
        if value is None:
            return None
        number = float(value)
        if number > 10_000_000_000:
            number /= 1000
        return datetime.fromtimestamp(number, tz=timezone.utc)

    def _float(self, value: Any) -> float | None:
        if value is None or value == "":
            return None
        return float(value)

    def _has_scalar_ticker_fields(self, message: dict[str, Any]) -> bool:
        for key in ("b", "a", "bid", "ask", "last", "lastPrice"):
            value = message.get(key)
            if value is not None and not isinstance(value, list):
                return True
        return False


class MarketDataRuntime:
    def __init__(self, settings: Settings, session: AsyncSession | None, bus: EngineCommunicationLayer, engine: MarketDataEngine, metrics: RuntimeMetrics):
        self.settings = settings
        self.session = session
        self.bus = bus
        self.engine = engine
        self.metrics = metrics
        self.normalizer = MarketDataNormalizer()
        self.connectors: list[VenueWebSocketConnector] = []
        self.tasks: list[asyncio.Task[None]] = []
        self.command_task: asyncio.Task[None] | None = None
        self.pubsub: Any | None = None
        self.websocket_messages_received = 0
        self.runtime_handle_message_enter_count = 0
        self.normalization_attempt_count = 0
        self.normalization_success_count = 0
        self.redis_publish_success_count = 0
        self.raw_messages_logged = 0
        self.last_websocket_message_at: datetime | None = None
        self.last_websocket_message_by_venue: dict[str, datetime] = {}
        self.last_message_at: dict[str, datetime] = {}
        self.active_subscriptions = 0
        self.reconnect_count = 0
        self.sequence_gaps = 0
        self.redis_publish_count = 0
        self.db_insert_count = 0
        self.market_data_rows_written = 0
        self.liquidity_rows_written = 0
        self.volatility_rows_written = 0
        self.last_publish_at: datetime | None = None
        self.last_db_insert_at: datetime | None = None
        self._persist_counter = 0
        self._started = False
        self._pair_ids: dict[tuple[str, str], uuid.UUID] = {}
        self.configured_trading_pairs_validated = False
        self.configured_trading_pairs_count = 0

    async def ensure_started(self) -> None:
        if self._started:
            return
        await self._validate_configured_trading_pairs()
        self._started = True
        if not self.settings.MARKET_DATA_CONNECT_ON_START:
            logger.info("market_data_connections_disabled", extra={"component_name": "market-data-engine"})
            await self._start_command_listener()
            return
        await self._start_command_listener()
        for exchange in self.settings.MARKET_DATA_EXCHANGES:
            venue = ExecutionVenue(exchange.lower())
            connector = VenueWebSocketConnector(venue, self.settings.EXCHANGE_RECONNECT_MAX_DELAY_SECONDS, default_runtime_config().exchange.heartbeat_interval_seconds)
            subscriptions = self._subscriptions(venue)
            self.active_subscriptions += len(subscriptions)
            self.connectors.append(connector)
            self.tasks.append(asyncio.create_task(self._run_connector(connector, subscriptions), name=f"market-data-{venue.value}"))
            logger.info("market_data_subscription_registered", extra={"venue": venue.value, "subscriptions": [subscription.kind.value for subscription in subscriptions], "symbols": self.settings.MARKET_DATA_SYMBOLS})
        self.metrics.set_gauge("market_data.active_subscriptions", float(self.active_subscriptions))

    async def stop(self) -> None:
        if self.command_task is not None:
            self.command_task.cancel()
            await asyncio.gather(self.command_task, return_exceptions=True)
        if self.pubsub is not None:
            await self.pubsub.aclose()
        for connector in self.connectors:
            connector.stop()
        for task in self.tasks:
            task.cancel()
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)

    async def ingest_fixture(self, venue: ExecutionVenue, symbol: str, message: dict[str, Any]) -> None:
        await self._handle_message(venue, symbol, message)

    async def tick(self) -> None:
        await self.ensure_started()
        self.metrics.set_gauge("market_data.active_subscriptions", float(self.active_subscriptions))
        self.metrics.set_gauge("market_data.sequence_gaps", float(self.sequence_gaps))
        self.metrics.set_gauge("market_data.reconnect_count", float(self.reconnect_count))
        self.validate_health()

    def health(self) -> dict[str, object]:
        return {
            "active_subscriptions": self.active_subscriptions,
            "websocket_messages_received": self.websocket_messages_received,
            "connector_callback_invoked": sum(int(getattr(connector, "callback_invocations", 0) or 0) for connector in self.connectors),
            "runtime_handle_message_enter": self.runtime_handle_message_enter_count,
            "normalization_attempt": self.normalization_attempt_count,
            "normalization_success": self.normalization_success_count,
            "redis_publish_success": self.redis_publish_success_count,
            "last_websocket_message_timestamp": self.last_websocket_message_at.isoformat() if self.last_websocket_message_at else None,
            "last_websocket_message_by_venue": {key: value.isoformat() for key, value in self.last_websocket_message_by_venue.items()},
            "last_message_timestamp": {key: value.isoformat() for key, value in self.last_message_at.items()},
            "reconnect_count": self.reconnect_count,
            "sequence_gaps": self.sequence_gaps,
            "connector_tasks_total": len(self.tasks),
            "connector_tasks_running": sum(1 for task in self.tasks if not task.done()),
            "connector_tasks_failed": sum(1 for task in self.tasks if task.done() and not task.cancelled()),
            "raw_message_samples": self._raw_message_samples(),
            "redis_publish_count": self.redis_publish_count,
            "last_publish_timestamp": self.last_publish_at.isoformat() if self.last_publish_at else None,
            "db_insert_count": self.db_insert_count,
            "market_data_rows_written": self.market_data_rows_written,
            "liquidity_rows_written": self.liquidity_rows_written,
            "volatility_rows_written": self.volatility_rows_written,
            "last_db_insert_timestamp": self.last_db_insert_at.isoformat() if self.last_db_insert_at else None,
            "websocket_state": "active" if self.tasks and all(not task.done() for task in self.tasks) else "disabled",
            "configured_trading_pairs_validated": self.configured_trading_pairs_validated,
            "configured_trading_pairs_count": self.configured_trading_pairs_count,
            "metrics": self.metrics.snapshot(),
        }

    async def _start_command_listener(self) -> None:
        if self.command_task is not None:
            return
        self.pubsub = self.bus.pubsub.client.pubsub()
        await self.pubsub.psubscribe("engine.commands.market-data-engine", "runtime.config.liquidity.updated", "runtime.config.exchange.updated")
        self.command_task = asyncio.create_task(self._consume_runtime_commands(), name="market-data-runtime-command-consumer")

    async def _consume_runtime_commands(self) -> None:
        if self.pubsub is None:
            return
        while True:
            try:
                message = await self.pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if not message:
                    await asyncio.sleep(0.1)
                    continue
                channel = message.get("channel")
                data = message.get("data")
                if isinstance(channel, bytes):
                    channel = channel.decode()
                if isinstance(data, bytes):
                    data = data.decode()
                if not isinstance(data, str):
                    continue
                payload = json.loads(data)
                command_id = str(payload.get("command_id") or "")
                runtime_payload = payload.get("runtime_config") or payload.get("payload", {}).get("runtime_config")
                if isinstance(runtime_payload, dict):
                    config = RuntimeConfig.model_validate(runtime_payload)
                    self.engine.liquidity_settings = config.liquidity
                    self.metrics.increment("runtime.config_reloads")
                    await publish_runtime_ack(self.session, self.bus, component="market-data-engine", command_id=command_id or None, event_type="runtime_config_reload_ack", status="acknowledged", payload={"domains": list(runtime_payload.keys())})
                    logger.info("market_data_runtime_config_reloaded", extra={"channel": channel})
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.metrics.increment("market_data.runtime_command_errors")
                logger.warning("market_data_runtime_command_recovered", extra={"error": str(exc)})
                await asyncio.sleep(1.0)

    def validate_health(self) -> None:
        if not self.settings.MARKET_DATA_CONNECT_ON_START:
            return
        if self.active_subscriptions <= 0:
            raise RuntimeError("market data has no active subscriptions")
        if not self.tasks:
            raise RuntimeError("market data has no connector tasks")
        failed_tasks = [task.get_name() for task in self.tasks if task.done()]
        if failed_tasks:
            raise RuntimeError(f"market data connector tasks stopped: {failed_tasks}")
        connector_task_running = any(not task.done() for task in self.tasks)
        if not any(connector.connected for connector in self.connectors) and not (connector_task_running and self.websocket_messages_received > 0):
            raise RuntimeError("market data has no active websocket connections")
        if self.websocket_messages_received <= 0:
            raise RuntimeError("market data has not received any websocket messages")

    def _subscriptions(self, venue: ExecutionVenue) -> list[StreamSubscription]:
        subscriptions: list[StreamSubscription] = []
        for symbol in self.settings.MARKET_DATA_SYMBOLS:
            for stream in self.settings.MARKET_DATA_STREAMS:
                subscriptions.append(StreamSubscription(venue=venue, symbol=symbol, kind=StreamKind(stream)))
        return subscriptions

    def handle_session_rollback(self) -> None:
        self._pair_ids.clear()
        self.configured_trading_pairs_validated = False
        self.configured_trading_pairs_count = 0

    async def _run_connector(self, connector: VenueWebSocketConnector, subscriptions: list[StreamSubscription]) -> None:
        async def handler(message: dict[str, Any]) -> None:
            self._record_websocket_message(connector.venue, message)
            for subscription in subscriptions:
                await self._handle_message(subscription.venue, subscription.symbol or "", message)

        while True:
            try:
                await connector.connect(subscriptions, handler)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.reconnect_count += 1
                self.metrics.increment("market_data.reconnect_count")
                logger.warning("market_data_connector_restart", extra={"venue": connector.venue.value, "error": str(exc), "reconnect_count": self.reconnect_count})
                await asyncio.sleep(min(30, max(1, self.reconnect_count)))

    def _record_websocket_message(self, venue: ExecutionVenue, message: dict[str, Any] | None = None) -> None:
        now = datetime.now(timezone.utc)
        self.websocket_messages_received += 1
        self.last_websocket_message_at = now
        self.last_websocket_message_by_venue[venue.value] = now
        self.metrics.increment("market_data.websocket_messages_received")
        if venue is ExecutionVenue.coinstore and message is not None and self.raw_messages_logged < 20:
            self.raw_messages_logged += 1
            logger.info("raw_message_received", extra={"venue": venue.value, "raw_message_index": self.raw_messages_logged, "raw_message": message})

    def _raw_message_samples(self) -> dict[str, list[dict[str, Any]]]:
        samples: dict[str, list[dict[str, Any]]] = {}
        for connector in self.connectors:
            raw_samples = getattr(connector, "raw_message_samples", None)
            if raw_samples:
                samples[getattr(connector.venue, "value", str(connector.venue))] = raw_samples
        return samples

    async def _handle_message(self, venue: ExecutionVenue, symbol: str, message: dict[str, Any]) -> None:
        self.runtime_handle_message_enter_count += 1
        self.metrics.increment("market_data.runtime_handle_message_enter")
        logger.info("RUNTIME_HANDLE_MESSAGE_ENTER", extra={"venue": venue.value, "symbol": symbol, "runtime_handle_message_enter": self.runtime_handle_message_enter_count, "message_keys": list(message.keys())})
        self.normalization_attempt_count += 1
        self.metrics.increment("market_data.normalization_attempt")
        logger.info("NORMALIZATION_ATTEMPT", extra={"venue": venue.value, "symbol": symbol, "normalization_attempt": self.normalization_attempt_count, "message_keys": list(message.keys())})
        logger.info("normalization_attempt", extra={"venue": venue.value, "symbol": symbol, "message_keys": list(message.keys())})
        try:
            normalized = self.normalizer.normalize(venue, symbol, message)
        except Exception as exc:
            self.metrics.increment("market_data.normalization_errors")
            logger.warning("normalization_dropped", extra={"venue": venue.value, "symbol": symbol, "reason": f"{exc.__class__.__name__}: {exc}", "raw_message": message})
            return
        if normalized is None:
            self.metrics.increment("market_data.normalization_dropped")
            logger.info("normalization_dropped", extra={"venue": venue.value, "symbol": symbol, "reason": "unrecognized_or_control_message", "raw_message": message})
            return
        kind, payload = normalized
        self.normalization_success_count += 1
        self.metrics.increment("market_data.normalization_success")
        logger.info("NORMALIZATION_SUCCESS", extra={"venue": venue.value, "symbol": symbol, "kind": kind, "normalization_success": self.normalization_success_count})
        logger.info("normalization_success", extra={"venue": venue.value, "symbol": symbol, "kind": kind})
        key = f"{venue.value}:{symbol}"
        self.last_message_at[key] = datetime.now(timezone.utc)
        self.metrics.increment("market_data.messages")
        self.metrics.set_gauge("market_data.active_subscriptions", float(self.active_subscriptions))
        logger.info("message_normalized", extra={"venue": venue.value, "symbol": symbol, "kind": kind})
        if kind == "ticker":
            await self._publish(f"marketdata:ticker:{venue.value}:{symbol}", asdict(payload))
            await self._maybe_persist_ticker(payload)
        elif kind == "trade":
            await self._publish(f"marketdata:trades:{venue.value}:{symbol}", asdict(payload))
            stats = self.engine.market_statistics(symbol, [payload])
            await self._publish(f"marketdata:analytics:{venue.value}:{symbol}", asdict(stats))
        elif kind == "orderbook":
            analytics = self.engine.liquidity_analytics(payload)
            spread = self.engine.calculate_spread(payload)
            await self.engine.distribute_orderbook(payload)
            await self._publish(f"marketdata:orderbook:{venue.value}:{symbol}", asdict(payload))
            await self._publish(f"marketdata:analytics:{venue.value}:{symbol}", {"spread": asdict(spread), "liquidity": asdict(analytics)})
            await self._maybe_persist_orderbook(payload, spread)
            await self._maybe_persist_orderbook_metrics(payload, analytics, spread)
        elif kind == "kline":
            stats = self.engine.market_statistics(symbol, [], [payload])
            await self._publish(f"marketdata:analytics:{venue.value}:{symbol}", asdict(stats))
            await self._maybe_persist_volatility(payload, stats.realized_volatility)

    async def _publish(self, channel: str, payload: dict[str, Any]) -> None:
        await self.bus.cache.set_json(f"latest:{channel}", payload, ttl_seconds=300)
        await self.bus.pubsub.publish(channel, payload)
        self.redis_publish_count += 1
        self.redis_publish_success_count += 1
        self.last_publish_at = datetime.now(timezone.utc)
        self.metrics.increment("market_data.redis_publish_success")
        logger.info("REDIS_PUBLISH_SUCCESS", extra={"channel": channel, "redis_publish_success": self.redis_publish_success_count, "publish_count": self.redis_publish_count})
        logger.info("redis_publish_success", extra={"channel": channel, "publish_count": self.redis_publish_count})

    async def _validate_configured_trading_pairs(self) -> None:
        if self.session is None:
            return
        count = 0
        for exchange in self.settings.MARKET_DATA_EXCHANGES:
            for symbol in self.settings.MARKET_DATA_SYMBOLS:
                await self._ensure_trading_pair(exchange, symbol)
                count += 1
        self.configured_trading_pairs_validated = True
        self.configured_trading_pairs_count = count
        logger.info(
            "market_data_trading_pairs_validated",
            extra={
                "exchange_count": len(self.settings.MARKET_DATA_EXCHANGES),
                "symbol_count": len(self.settings.MARKET_DATA_SYMBOLS),
                "trading_pair_count": count,
            },
        )

    async def _ensure_trading_pair(self, exchange: str, symbol: str) -> uuid.UUID | None:
        if self.session is None:
            return None
        exchange_key = self._normalize_exchange(exchange)
        normalized_symbol = self._normalize_trading_symbol(symbol)
        base, quote = normalized_symbol.split("/", 1)
        venue_symbol = normalized_symbol.replace("/", "")
        key = (exchange_key, normalized_symbol)
        if not self.session.is_active:
            await self.session.rollback()
            self.handle_session_rollback()
        cached_id = self._pair_ids.get(key)
        if cached_id is not None:
            try:
                if await self.session.get(models.TradingPair, cached_id) is not None:
                    return cached_id
            except PendingRollbackError:
                await self.session.rollback()
                self.handle_session_rollback()
            else:
                self._pair_ids.pop(key, None)
        row = await self._find_trading_pair(exchange_key, normalized_symbol, venue_symbol)
        if row is None:
            row = models.TradingPair(
                exchange_name=exchange_key,
                base_asset=base,
                quote_asset=quote,
                normalized_symbol=normalized_symbol,
                venue_symbol=venue_symbol,
                price_precision=8,
                quantity_precision=8,
                is_enabled=True,
            )
            try:
                async with self.session.begin_nested():
                    self.session.add(row)
                    await self.session.flush()
            except IntegrityError:
                self._pair_ids.pop(key, None)
                row = await self._find_trading_pair(exchange_key, normalized_symbol, venue_symbol)
                if row is None:
                    raise
        self._pair_ids[key] = row.id
        return row.id

    async def _find_trading_pair(self, exchange: str, normalized_symbol: str, venue_symbol: str) -> models.TradingPair | None:
        result = await self.session.execute(
            select(models.TradingPair).where(
                models.TradingPair.exchange_name == exchange,
                or_(
                    models.TradingPair.venue_symbol == venue_symbol,
                    models.TradingPair.normalized_symbol == normalized_symbol,
                ),
            )
        )
        return result.scalars().first()

    def _normalize_exchange(self, exchange: str) -> str:
        normalized = str(exchange).strip().lower()
        if not normalized:
            raise ValueError("exchange name is required")
        return normalized

    def _normalize_trading_symbol(self, symbol: str) -> str:
        normalized = str(symbol).strip().upper()
        if "/" not in normalized:
            raise ValueError(f"configured trading symbol must use BASE/QUOTE format: {symbol}")
        base, quote = (part.strip() for part in normalized.split("/", 1))
        if not base or not quote:
            raise ValueError(f"configured trading symbol must include base and quote assets: {symbol}")
        return f"{base}/{quote}"

    async def _maybe_persist_ticker(self, ticker: Ticker) -> None:
        self._persist_counter += 1
        if self.session is None or self._persist_counter % self.settings.MARKET_DATA_PERSIST_EVERY_N_MESSAGES != 0:
            return
        await self._insert_market_data(
            exchange=ticker.exchange,
            symbol=ticker.symbol,
            data_type="ticker",
            source_timestamp=ticker.source_timestamp,
            payload=asdict(ticker),
            bid_price=ticker.bid_price,
            bid_size=ticker.bid_size,
            ask_price=ticker.ask_price,
            ask_size=ticker.ask_size,
            last_price=ticker.last_price,
            volume_24h=ticker.volume_24h,
        )

    async def _maybe_persist_orderbook(self, orderbook: OrderBookSnapshot, spread: Any) -> None:
        self._persist_counter += 1
        if self.session is None or self._persist_counter % self.settings.MARKET_DATA_PERSIST_EVERY_N_MESSAGES != 0:
            return
        best_bid = max(orderbook.bids, key=lambda level: level.price) if orderbook.bids else None
        best_ask = min(orderbook.asks, key=lambda level: level.price) if orderbook.asks else None
        await self._insert_market_data(
            exchange=orderbook.exchange,
            symbol=orderbook.symbol,
            data_type="order_book",
            source_timestamp=orderbook.source_timestamp,
            payload=asdict(orderbook),
            bid_price=best_bid.price if best_bid else None,
            bid_size=best_bid.size if best_bid else None,
            ask_price=best_ask.price if best_ask else None,
            ask_size=best_ask.size if best_ask else None,
            last_price=spread.mid,
            volume_24h=None,
        )

    async def _insert_market_data(
        self,
        exchange: str,
        symbol: str,
        data_type: str,
        source_timestamp: datetime,
        payload: dict[str, Any],
        bid_price: float | None,
        bid_size: float | None,
        ask_price: float | None,
        ask_size: float | None,
        last_price: float | None,
        volume_24h: float | None,
    ) -> None:
        logger.info("market_data_insert_attempt", extra={"table": "market_data", "exchange": exchange, "symbol": symbol, "data_type": data_type})
        try:
            pair_id = await self._ensure_trading_pair(exchange, symbol)
            if pair_id is None:
                raise RuntimeError("trading pair id unavailable")
            row = models.MarketData(exchange_name=exchange, trading_pair_id=pair_id, data_type=data_type, bid_price=bid_price, bid_size=bid_size, ask_price=ask_price, ask_size=ask_size, last_price=last_price, volume_24h=volume_24h, source_timestamp=source_timestamp, payload=self._json_safe(payload))
            self.session.add(row)
            await self.session.flush()
            self.db_insert_count += 1
            self.market_data_rows_written += 1
            self.last_db_insert_at = datetime.now(timezone.utc)
            self.metrics.increment("market_data.rows_written")
            logger.info("market_data_insert_success", extra={"table": "market_data", "exchange": exchange, "symbol": symbol, "data_type": data_type, "rows_written": self.market_data_rows_written})
            logger.info("db_insert_success", extra={"table": "market_data", "exchange": exchange, "symbol": symbol, "data_type": data_type})
        except Exception as exc:
            logger.exception("market_data_insert_failed", extra={"table": "market_data", "exchange": exchange, "symbol": symbol, "data_type": data_type, "error": str(exc)})
            raise

    async def _maybe_persist_orderbook_metrics(self, orderbook: OrderBookSnapshot, analytics: Any, spread: Any) -> None:
        self._persist_counter += 1
        if self.session is None or self._persist_counter % self.settings.MARKET_DATA_PERSIST_EVERY_N_MESSAGES != 0:
            return
        logger.info("liquidity_metrics_insert_attempt", extra={"table": "liquidity_metrics", "exchange": orderbook.exchange, "symbol": orderbook.symbol})
        try:
            pair_id = await self._ensure_trading_pair(orderbook.exchange, orderbook.symbol)
            if pair_id is None:
                raise RuntimeError("trading pair id unavailable")
            self.session.add(models.LiquidityMetric(exchange_name=orderbook.exchange, trading_pair_id=pair_id, spread_bps=spread.spread_bps, top_of_book_depth=analytics.top_of_book_depth, depth_1pct=analytics.depth_1pct, depth_5pct=analytics.depth_5pct, imbalance_ratio=analytics.imbalance_ratio, captured_at=orderbook.source_timestamp, metadata_json={"mode": "paper"}))
            await self.session.flush()
            self.db_insert_count += 1
            self.liquidity_rows_written += 1
            self.last_db_insert_at = datetime.now(timezone.utc)
            self.metrics.increment("liquidity.rows_written")
            logger.info("liquidity_metrics_insert_success", extra={"table": "liquidity_metrics", "exchange": orderbook.exchange, "symbol": orderbook.symbol, "rows_written": self.liquidity_rows_written})
            logger.info("db_insert_success", extra={"table": "liquidity_metrics", "exchange": orderbook.exchange, "symbol": orderbook.symbol})
        except Exception as exc:
            logger.exception("liquidity_metrics_insert_failed", extra={"table": "liquidity_metrics", "exchange": orderbook.exchange, "symbol": orderbook.symbol, "error": str(exc)})
            raise

    async def _maybe_persist_volatility(self, kline: Kline, volatility: float) -> None:
        self._persist_counter += 1
        if self.session is None or self._persist_counter % self.settings.MARKET_DATA_PERSIST_EVERY_N_MESSAGES != 0:
            return
        logger.info("volatility_metrics_insert_attempt", extra={"table": "volatility_metrics", "exchange": kline.exchange, "symbol": kline.symbol})
        try:
            pair_id = await self._ensure_trading_pair(kline.exchange, kline.symbol)
            if pair_id is None:
                raise RuntimeError("trading pair id unavailable")
            self.session.add(models.VolatilityMetric(exchange_name=kline.exchange, trading_pair_id=pair_id, window_seconds=60, realized_volatility=volatility, high_price=kline.high_price, low_price=kline.low_price, open_price=kline.open_price, close_price=kline.close_price, captured_at=kline.close_time, metadata_json={"interval": kline.interval}))
            await self.session.flush()
            self.db_insert_count += 1
            self.volatility_rows_written += 1
            self.last_db_insert_at = datetime.now(timezone.utc)
            self.metrics.increment("volatility.rows_written")
            logger.info("volatility_metrics_insert_success", extra={"table": "volatility_metrics", "exchange": kline.exchange, "symbol": kline.symbol, "rows_written": self.volatility_rows_written})
            logger.info("db_insert_success", extra={"table": "volatility_metrics", "exchange": kline.exchange, "symbol": kline.symbol})
        except Exception as exc:
            logger.exception("volatility_metrics_insert_failed", extra={"table": "volatility_metrics", "exchange": kline.exchange, "symbol": kline.symbol, "error": str(exc)})
            raise

    def _json_safe(self, value: Any) -> Any:
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, Decimal):
            return str(value)
        if isinstance(value, dict):
            return {str(key): self._json_safe(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._json_safe(item) for item in value]
        if isinstance(value, tuple):
            return [self._json_safe(item) for item in value]
        return value
