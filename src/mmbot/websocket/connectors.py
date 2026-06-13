from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Any

import websockets

from mmbot.execution.models import ExecutionVenue
from mmbot.exchanges.types import OrderBookLevel, OrderBookSnapshot
from mmbot.exchanges.registry import get_exchange_definition

logger = logging.getLogger(__name__)


class StreamKind(str, Enum):
    orderbook = "orderbook"
    trades = "trades"
    ticker = "ticker"
    kline = "kline"
    user_data = "user_data"
    balance_updates = "balance_updates"
    order_updates = "order_updates"
    execution_reports = "execution_reports"


@dataclass(frozen=True)
class StreamSubscription:
    venue: ExecutionVenue
    symbol: str | None
    kind: StreamKind
    interval: str | None = None
    auth_stream_token: str | None = None


@dataclass
class SequenceState:
    last_sequence: int | None = None
    gaps_detected: int = 0

    def validate(self, sequence: int | None, previous_sequence: int | None = None) -> bool:
        if sequence is None:
            return True
        if self.last_sequence is None:
            self.last_sequence = sequence
            return True
        expected_previous = self.last_sequence if previous_sequence is None else previous_sequence
        if expected_previous != self.last_sequence or sequence <= self.last_sequence:
            self.gaps_detected += 1
            return False
        self.last_sequence = sequence
        return True


@dataclass
class ReconstructedOrderBook:
    bids: dict[Decimal, Decimal] = field(default_factory=dict)
    asks: dict[Decimal, Decimal] = field(default_factory=dict)
    sequence: int | None = None

    def snapshot(self, venue: str, symbol: str) -> OrderBookSnapshot:
        bids = [OrderBookLevel(float(price), float(size)) for price, size in sorted(self.bids.items(), reverse=True) if size > 0]
        asks = [OrderBookLevel(float(price), float(size)) for price, size in sorted(self.asks.items()) if size > 0]
        return OrderBookSnapshot(venue, symbol, bids, asks, source_timestamp=__import__('datetime').datetime.now(__import__('datetime').timezone.utc), sequence=str(self.sequence) if self.sequence is not None else None)


class OrderBookReconstructor:
    def __init__(self):
        self.books: dict[tuple[ExecutionVenue, str], ReconstructedOrderBook] = {}
        self.sequences: dict[tuple[ExecutionVenue, str], SequenceState] = {}

    def apply_snapshot(self, venue: ExecutionVenue, symbol: str, bids: list[list[Any]], asks: list[list[Any]], sequence: int | None) -> OrderBookSnapshot:
        key = (venue, symbol)
        book = ReconstructedOrderBook(
            bids={Decimal(str(price)): Decimal(str(size)) for price, size, *_ in bids},
            asks={Decimal(str(price)): Decimal(str(size)) for price, size, *_ in asks},
            sequence=sequence,
        )
        self.books[key] = book
        self.sequences[key] = SequenceState(sequence)
        return book.snapshot(venue.value, symbol)

    def apply_delta(self, venue: ExecutionVenue, symbol: str, bids: list[list[Any]], asks: list[list[Any]], sequence: int | None, previous_sequence: int | None = None) -> tuple[bool, OrderBookSnapshot | None]:
        key = (venue, symbol)
        if key not in self.books:
            return False, None
        valid = self.sequences.setdefault(key, SequenceState()).validate(sequence, previous_sequence)
        if not valid:
            return False, None
        book = self.books[key]
        for price_raw, size_raw, *_ in bids:
            price = Decimal(str(price_raw)); size = Decimal(str(size_raw))
            if size == 0:
                book.bids.pop(price, None)
            else:
                book.bids[price] = size
        for price_raw, size_raw, *_ in asks:
            price = Decimal(str(price_raw)); size = Decimal(str(size_raw))
            if size == 0:
                book.asks.pop(price, None)
            else:
                book.asks[price] = size
        book.sequence = sequence
        return True, book.snapshot(venue.value, symbol)


class VenueWebSocketCodec:
    def subscribe_payload(self, subscription: StreamSubscription) -> dict[str, Any] | list[dict[str, Any]] | str:
        venue = subscription.venue
        symbol = self._symbol(subscription)
        if venue is ExecutionVenue.binance:
            stream = self._binance_stream(subscription, symbol)
            return {"method": "SUBSCRIBE", "params": [stream], "id": int(time.time())}
        if venue is ExecutionVenue.mexc:
            return {"method": "SUBSCRIPTION", "params": [self._mexc_stream(subscription, symbol)]}
        if venue is ExecutionVenue.gate:
            return {"time": int(time.time()), "channel": self._gate_channel(subscription), "event": "subscribe", "payload": self._gate_payload(subscription, symbol)}
        if venue is ExecutionVenue.bitmart:
            return {"op": "subscribe", "args": [self._bitmart_stream(subscription, symbol)]}
        if venue is ExecutionVenue.kucoin:
            return {"id": str(int(time.time() * 1000)), "type": "subscribe", "topic": self._kucoin_topic(subscription, symbol), "privateChannel": subscription.kind in {StreamKind.user_data, StreamKind.balance_updates, StreamKind.order_updates, StreamKind.execution_reports}, "response": True}
        if venue is ExecutionVenue.coinstore:
            return {"op": "SUB", "channel": [self._coinstore_channel(subscription, symbol)], "id": int(time.time())}
        return {"op": "subscribe", "channel": subscription.kind.value, "symbol": symbol}

    def parse_sequence(self, venue: ExecutionVenue, message: dict[str, Any]) -> tuple[int | None, int | None]:
        if venue in {ExecutionVenue.binance, ExecutionVenue.mexc}:
            return _int(message.get("u") or message.get("lastUpdateId")), _int(message.get("pu") or message.get("U"))
        if venue is ExecutionVenue.gate:
            result = message.get("result", {})
            return _int(result.get("u") or result.get("last_update_id")), _int(result.get("U"))
        if venue is ExecutionVenue.kucoin:
            data = message.get("data", {})
            return _int(data.get("sequenceEnd") or data.get("sequence")), _int(data.get("sequenceStart"))
        if venue is ExecutionVenue.bitmart:
            data = message.get("data", [{}])
            row = data[0] if isinstance(data, list) and data else data
            return _int(row.get("ms_t") or row.get("sequence")), None
        return _int(message.get("sequence") or message.get("seq")), _int(message.get("prevSeq"))

    def _symbol(self, subscription: StreamSubscription) -> str:
        symbol = subscription.symbol or ""
        if subscription.venue in {ExecutionVenue.gate, ExecutionVenue.bitmart}:
            return symbol.replace("/", "_").upper()
        if subscription.venue is ExecutionVenue.kucoin:
            return symbol.replace("/", "-").upper()
        return symbol.replace("/", "").lower() if subscription.venue is ExecutionVenue.binance else symbol.replace("/", "").upper()

    def _binance_stream(self, subscription: StreamSubscription, symbol: str) -> str:
        mapping = {StreamKind.orderbook: f"{symbol}@depth@100ms", StreamKind.trades: f"{symbol}@trade", StreamKind.ticker: f"{symbol}@ticker", StreamKind.kline: f"{symbol}@kline_{subscription.interval or '1m'}"}
        return subscription.auth_stream_token or mapping[subscription.kind]

    def _mexc_stream(self, subscription: StreamSubscription, symbol: str) -> str:
        mapping = {StreamKind.orderbook: f"spot@public.limit.depth.v3.api@{symbol}@20", StreamKind.trades: f"spot@public.deals.v3.api@{symbol}", StreamKind.ticker: f"spot@public.ticker.v3.api@{symbol}", StreamKind.kline: f"spot@public.kline.v3.api@{symbol}@{subscription.interval or 'Min1'}"}
        return mapping.get(subscription.kind, "spot@private.orders.v3.api")

    def _gate_channel(self, subscription: StreamSubscription) -> str:
        return {StreamKind.orderbook: "spot.order_book_update", StreamKind.trades: "spot.trades", StreamKind.ticker: "spot.tickers", StreamKind.kline: "spot.candlesticks", StreamKind.user_data: "spot.usertrades", StreamKind.order_updates: "spot.orders", StreamKind.balance_updates: "spot.balances", StreamKind.execution_reports: "spot.usertrades"}[subscription.kind]

    def _gate_payload(self, subscription: StreamSubscription, symbol: str) -> list[Any]:
        if subscription.kind is StreamKind.kline:
            return [subscription.interval or "1m", symbol]
        return [symbol]

    def _bitmart_stream(self, subscription: StreamSubscription, symbol: str) -> str:
        return {StreamKind.orderbook: f"spot/depth50:{symbol}", StreamKind.trades: f"spot/trade:{symbol}", StreamKind.ticker: f"spot/ticker:{symbol}", StreamKind.kline: f"spot/kline{subscription.interval or '1m'}:{symbol}", StreamKind.order_updates: "spot/user/order", StreamKind.balance_updates: "spot/user/balance", StreamKind.execution_reports: "spot/user/trade"}[subscription.kind]

    def _kucoin_topic(self, subscription: StreamSubscription, symbol: str) -> str:
        return {StreamKind.orderbook: f"/market/level2:{symbol}", StreamKind.trades: f"/market/match:{symbol}", StreamKind.ticker: f"/market/ticker:{symbol}", StreamKind.kline: f"/market/candles:{symbol}_{subscription.interval or '1min'}", StreamKind.order_updates: "/spotMarket/tradeOrders", StreamKind.balance_updates: "/account/balance", StreamKind.execution_reports: "/spotMarket/tradeOrders"}[subscription.kind]

    def _coinstore_channel(self, subscription: StreamSubscription, symbol: str) -> str:
        return {
            StreamKind.orderbook: f"{symbol}@depth",
            StreamKind.trades: f"{symbol}@trade",
            StreamKind.ticker: f"{symbol}@ticker",
            StreamKind.kline: f"{symbol}@kline_{subscription.interval or '1m'}",
        }.get(subscription.kind, f"{symbol}@{subscription.kind.value}")


class VenueWebSocketConnector:
    def __init__(self, venue: ExecutionVenue, max_reconnect_delay_seconds: float = 30.0, heartbeat_interval_seconds: float = 20.0):
        self.venue = venue
        self.definition = get_exchange_definition(venue.value)
        self.codec = VenueWebSocketCodec()
        self.max_reconnect_delay_seconds = max_reconnect_delay_seconds
        self.heartbeat_interval_seconds = heartbeat_interval_seconds
        self.reconstructor = OrderBookReconstructor()
        self._stopped = asyncio.Event()
        self.connected = False
        self.connection_attempts = 0
        self.messages_received = 0
        self.callback_invocations = 0
        self.subscriptions_sent = 0
        self.raw_message_samples: list[dict[str, Any]] = []

    def stop(self) -> None:
        self._stopped.set()

    async def connect(self, subscriptions: list[StreamSubscription], handler: Callable[[dict[str, Any]], Awaitable[None]], recover_snapshot: Callable[[StreamSubscription], Awaitable[OrderBookSnapshot | None]] | None = None) -> None:
        delay = 1.0
        while not self._stopped.is_set():
            try:
                self.connection_attempts += 1
                logger.info("websocket_connecting", extra={"venue": self.venue.value, "url": self.definition.websocket_url, "attempt": self.connection_attempts})
                async with websockets.connect(self.definition.websocket_url, ping_interval=self.heartbeat_interval_seconds, ping_timeout=self.heartbeat_interval_seconds) as ws:
                    self.connected = True
                    logger.info("websocket_connected", extra={"venue": self.venue.value, "url": self.definition.websocket_url})
                    if self.venue is not ExecutionVenue.coinstore:
                        await self._subscribe(ws, subscriptions)
                    delay = 1.0
                    async for raw in ws:
                        message = json.loads(raw) if isinstance(raw, str) else {"binary": raw.hex()}
                        self.messages_received += 1
                        logger.info("WEBSOCKET_MESSAGE_RECEIVED", extra={"venue": self.venue.value, "messages_received": self.messages_received, "message_keys": list(message.keys())})
                        self._capture_raw_sample(message)
                        logger.info("message_received", extra={"venue": self.venue.value, "messages_received": self.messages_received})
                        if self.venue is ExecutionVenue.coinstore and self._coinstore_established(message):
                            await self._subscribe(ws, subscriptions)
                            self.callback_invocations += 1
                            logger.info("CONNECTOR_CALLBACK_INVOKED", extra={"venue": self.venue.value, "callback_invocations": self.callback_invocations, "message_keys": list(message.keys())})
                            await handler(message)
                            continue
                        if await self._gap_detected(message, subscriptions, recover_snapshot):
                            await self._subscribe(ws, subscriptions)
                            continue
                        self.callback_invocations += 1
                        logger.info("CONNECTOR_CALLBACK_INVOKED", extra={"venue": self.venue.value, "callback_invocations": self.callback_invocations, "message_keys": list(message.keys())})
                        await handler(message)
            except Exception as exc:
                self.connected = False
                logger.warning("venue_websocket_reconnect", extra={"venue": self.venue.value, "error": str(exc), "delay": delay})
                await asyncio.sleep(delay)
                delay = min(self.max_reconnect_delay_seconds, delay * 2)

    def _capture_raw_sample(self, message: dict[str, Any]) -> None:
        if len(self.raw_message_samples) >= 20:
            return
        self.raw_message_samples.append(message)
        logger.info("raw_message_received", extra={"venue": self.venue.value, "raw_message_index": len(self.raw_message_samples), "raw_message": message})

    def _coinstore_established(self, message: dict[str, Any]) -> bool:
        message_type = str(message.get("T") or "").lower()
        return message_type in {"req", "resp"} and str(message.get("M") or "").lower() == "established"

    async def _subscribe(self, ws: Any, subscriptions: list[StreamSubscription]) -> None:
        for subscription in subscriptions:
            payload = self.codec.subscribe_payload(subscription)
            messages = payload if isinstance(payload, list) else [payload]
            for message in messages:
                await ws.send(message if isinstance(message, str) else json.dumps(message, separators=(",", ":")))
                self.subscriptions_sent += 1
                logger.info("subscription_sent", extra={"venue": self.venue.value, "kind": subscription.kind.value, "symbol": subscription.symbol, "subscriptions_sent": self.subscriptions_sent})

    async def _gap_detected(self, message: dict[str, Any], subscriptions: list[StreamSubscription], recover_snapshot: Callable[[StreamSubscription], Awaitable[OrderBookSnapshot | None]] | None) -> bool:
        sequence, previous = self.codec.parse_sequence(self.venue, message)
        if sequence is None:
            return False
        for subscription in subscriptions:
            if subscription.kind is StreamKind.orderbook and subscription.symbol:
                state = self.reconstructor.sequences.setdefault((self.venue, subscription.symbol), SequenceState())
                if not state.validate(sequence, previous):
                    logger.critical("orderbook_sequence_gap", extra={"venue": self.venue.value, "symbol": subscription.symbol, "sequence": sequence, "previous": previous})
                    if recover_snapshot:
                        await recover_snapshot(subscription)
                    return True
                break
        return False


def _int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
