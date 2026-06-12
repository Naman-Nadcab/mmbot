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
