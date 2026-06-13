import asyncio
import json
import secrets

import pytest

from mmbot.core.config import Settings, default_runtime_config
from mmbot.engines.market_data.engine import MarketDataEngine
from mmbot.engines.market_data.runtime import MarketDataRuntime
from mmbot.engines.market_making.engine import QuoteEngine
from mmbot.engines.market_making.runtime import MarketMakerRuntime
from mmbot.execution.models import ExecutionVenue
from mmbot.observability.metrics import RuntimeMetrics
from mmbot.websocket.connectors import StreamKind, StreamSubscription, VenueWebSocketConnector


class MemoryCache:
    def __init__(self):
        self.data = {}

    async def set_json(self, key, value, ttl_seconds=None):
        self.data[key] = json.loads(json.dumps(value, default=str))

    async def get_json(self, key):
        return self.data.get(key)


class MemoryPubSub:
    def __init__(self):
        self.published = []

    async def publish(self, channel, payload):
        self.published.append((channel, payload))
        return 1


class MemoryBus:
    def __init__(self):
        self.cache = MemoryCache()
        self.pubsub = MemoryPubSub()

    async def publish_event(self, engine, event_type, payload):
        await self.cache.set_json(f"engine:last_event:{engine}", {"engine": engine, "event_type": event_type, "payload": payload})
        return await self.pubsub.publish(f"engine.events.{engine}", {"engine": engine, "event_type": event_type, "payload": payload})


class FakeWebSocket:
    def __init__(self):
        self.sent = []
        self.messages = [
            json.dumps({"S": 1, "T": "req", "sid": "sid-1", "C": 200, "M": "established"}),
            json.dumps({"T": "trade", "channel": "BTCUSDT@trade", "price": "100", "volume": "1", "tradeId": 1, "symbol": "BTCUSDT"}),
        ]

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def send(self, message):
        self.sent.append(json.loads(message))

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self.messages:
            raise StopAsyncIteration
        return self.messages.pop(0)


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
        MARKET_DATA_CONNECT_ON_START=True,
        MARKET_DATA_EXCHANGES=["coinstore"],
        MARKET_DATA_SYMBOLS=["BTC/USDT"],
        MARKET_DATA_STREAMS=["orderbook", "trades", "ticker", "kline"],
    )


async def _market_data_runtime(bus):
    config = default_runtime_config()
    return MarketDataRuntime(_settings(), None, bus, MarketDataEngine(config.liquidity, bus), RuntimeMetrics())


@pytest.mark.asyncio
async def test_coinstore_trade_message_publishes_to_redis():
    bus = MemoryBus()
    runtime = await _market_data_runtime(bus)
    message = {"T": "trade", "channel": "BTCUSDT@trade", "price": "9811.7494086", "takerSide": "SELL", "tradeId": 26461, "volume": "7.505", "symbol": "BTCUSDT", "instrumentId": 80004, "time": 1700000000000}

    await runtime.ingest_fixture(ExecutionVenue.coinstore, "BTC/USDT", message)

    assert await bus.cache.get_json("latest:marketdata:trades:coinstore:BTC/USDT") is not None
    assert any(channel == "marketdata:trades:coinstore:BTC/USDT" for channel, _ in bus.pubsub.published)
    assert runtime.redis_publish_count >= 2
    assert runtime.metrics.counters["market_data.normalization_success"] == 1
    assert runtime.health()["runtime_handle_message_enter"] == 1
    assert runtime.health()["normalization_attempt"] == 1
    assert runtime.health()["normalization_success"] == 1
    assert runtime.health()["redis_publish_success"] >= 2


@pytest.mark.asyncio
async def test_coinstore_orderbook_message_publishes_to_redis():
    bus = MemoryBus()
    runtime = await _market_data_runtime(bus)
    message = {"T": "depth", "channel": "BTCUSDT@depth", "symbol": "BTCUSDT", "bids": [["99999", "1.2"], ["99998", "2.0"]], "asks": [["100001", "1.0"], ["100002", "2.5"]], "sequence": 101, "time": 1700000000000}

    await runtime.ingest_fixture(ExecutionVenue.coinstore, "BTC/USDT", message)

    assert await bus.cache.get_json("latest:marketdata:orderbook:coinstore:BTC/USDT") is not None
    assert await bus.cache.get_json("latest:marketdata:analytics:coinstore:BTC/USDT") is not None
    assert any(channel == "marketdata:orderbook:coinstore:BTC/USDT" for channel, _ in bus.pubsub.published)
    assert runtime.redis_publish_count >= 2


@pytest.mark.asyncio
async def test_coinstore_ticker_message_publishes_to_redis():
    bus = MemoryBus()
    runtime = await _market_data_runtime(bus)
    message = {"T": "ticker", "channel": "BTCUSDT@ticker", "symbol": "BTCUSDT", "bid": "99999", "ask": "100001", "last": "100000", "volume": "123.45", "time": 1700000000000}

    await runtime.ingest_fixture(ExecutionVenue.coinstore, "BTC/USDT", message)

    ticker = await bus.cache.get_json("latest:marketdata:ticker:coinstore:BTC/USDT")
    assert ticker["last_price"] == 100000.0
    assert any(channel == "marketdata:ticker:coinstore:BTC/USDT" for channel, _ in bus.pubsub.published)


@pytest.mark.asyncio
async def test_coinstore_control_message_is_dropped_without_publish():
    bus = MemoryBus()
    runtime = await _market_data_runtime(bus)
    message = {"T": "resp", "M": "sub.channel.success", "channel": "BTCUSDT@trade"}

    await runtime.ingest_fixture(ExecutionVenue.coinstore, "BTC/USDT", message)

    assert runtime.redis_publish_count == 0
    assert runtime.metrics.counters["market_data.normalization_dropped"] == 1


@pytest.mark.asyncio
async def test_market_maker_receives_coinstore_orderbook_and_ticker_from_cache():
    bus = MemoryBus()
    market_data = await _market_data_runtime(bus)
    await market_data.ingest_fixture(ExecutionVenue.coinstore, "BTC/USDT", {"T": "depth", "channel": "BTCUSDT@depth", "symbol": "BTCUSDT", "bids": [["99999", "1.2"]], "asks": [["100001", "1.0"]], "sequence": 101, "time": 1700000000000})
    await market_data.ingest_fixture(ExecutionVenue.coinstore, "BTC/USDT", {"T": "ticker", "channel": "BTCUSDT@ticker", "symbol": "BTCUSDT", "bid": "99999", "ask": "100001", "last": "100000", "volume": "123.45", "time": 1700000000000})
    config = default_runtime_config()
    maker = MarketMakerRuntime(_settings(), None, bus, QuoteEngine(config.spread, config.order_size, config.inventory, config.order_layers), RuntimeMetrics(), config)
    maker.started = True

    await maker._load_latest_market_state()

    assert maker.health()["known_orderbooks"] > 0
    assert maker.health()["known_tickers"] > 0


@pytest.mark.asyncio
async def test_coinstore_connector_subscribes_after_established_and_captures_raw(monkeypatch):
    fake_ws = FakeWebSocket()

    def fake_connect(*args, **kwargs):
        return fake_ws

    monkeypatch.setattr("mmbot.websocket.connectors.websockets.connect", fake_connect)
    connector = VenueWebSocketConnector(ExecutionVenue.coinstore, max_reconnect_delay_seconds=0.01)
    handled = []

    async def handler(message):
        handled.append(message)
        if len(handled) >= 2:
            connector.stop()

    subscriptions = [StreamSubscription(ExecutionVenue.coinstore, "BTC/USDT", StreamKind.trades)]
    await connector.connect(subscriptions, handler)

    assert connector.raw_message_samples[0] == {"S": 1, "T": "req", "sid": "sid-1", "C": 200, "M": "established"}
    assert connector.messages_received == 2
    assert connector.callback_invocations == 2
    assert fake_ws.sent
    assert fake_ws.sent[0]["op"] == "SUB"
    assert fake_ws.sent[0]["channel"] == ["BTCUSDT@trade"]
    assert handled[0]["M"] == "established"
    assert handled[1]["T"] == "trade"
