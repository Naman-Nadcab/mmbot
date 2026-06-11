import asyncio
import json
import secrets
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from mmbot.core.config import Settings, default_runtime_config
from mmbot.db import models
from mmbot.db.models import Base
from mmbot.db.session import Database
from mmbot.engines.market_data.engine import MarketDataEngine
from mmbot.engines.market_data.runtime import MarketDataRuntime
from mmbot.engines.market_making.engine import InventoryState, MarketState, QuoteEngine
from mmbot.engines.market_making.runtime import MarketMakerRuntime
from mmbot.execution.models import ExecutionOrderType, ExecutionSide, ExecutionVenue, OrderIntent, TimeInForce
from mmbot.exchanges.types import OrderBookLevel, OrderBookSnapshot
from mmbot.observability.metrics import RuntimeMetrics
from mmbot.redis.manager import CacheManager, EngineCommunicationLayer, PubSubFramework, RedisManager


async def _redis_server(reader, writer):
    store = _redis_server.store
    try:
        while True:
            line = await reader.readline()
            if not line:
                break
            if not line.startswith(b"*"):
                writer.write(b"+OK\r\n")
                await writer.drain()
                continue
            count = int(line[1:].strip() or b"0")
            parts = []
            for _ in range(count):
                header = await reader.readline()
                size = int(header[1:].strip() or b"0")
                data = await reader.readexactly(size)
                await reader.readexactly(2)
                parts.append(data)
            command = parts[0].upper() if parts else b""
            if command == b"PING":
                writer.write(b"+PONG\r\n")
            elif command == b"SET":
                store[parts[1].decode()] = parts[2].decode()
                writer.write(b"+OK\r\n")
            elif command == b"GET":
                value = store.get(parts[1].decode())
                if value is None:
                    writer.write(b"$-1\r\n")
                else:
                    encoded = value.encode()
                    writer.write(b"$" + str(len(encoded)).encode() + b"\r\n" + encoded + b"\r\n")
            elif command == b"PUBLISH":
                writer.write(b":1\r\n")
            elif command in {b"PSUBSCRIBE", b"SUBSCRIBE"}:
                for index, pattern in enumerate(parts[1:], start=1):
                    writer.write(b"*3\r\n$10\r\npsubscribe\r\n$" + str(len(pattern)).encode() + b"\r\n" + pattern + b"\r\n:" + str(index).encode() + b"\r\n")
            else:
                writer.write(b"+OK\r\n")
            await writer.drain()
    finally:
        writer.close()
        await writer.wait_closed()


_redis_server.store = {}


def _settings(redis_url: str) -> Settings:
    return Settings(
        APP_ENV="test",
        DATABASE_URL="sqlite+aiosqlite:///:memory:",
        REDIS_URL=redis_url,
        JWT_SECRET=secrets.token_urlsafe(48),
        TELEGRAM_BOT_TOKEN="token",
        TELEGRAM_CHAT_ID="chat",
        EXCHANGE_API_KEYS={"binance": "key"},
        EXCHANGE_API_SECRETS={"binance": "secret"},
        MARKET_DATA_CONNECT_ON_START=False,
        MARKET_DATA_EXCHANGES=["binance"],
        MARKET_DATA_SYMBOLS=["BTC/USDT"],
        MARKET_DATA_STREAMS=["orderbook", "trades", "ticker", "kline"],
        MARKET_DATA_PERSIST_EVERY_N_MESSAGES=1,
        MARKET_MAKER_REFRESH_SECONDS=0.01,
        RECONCILIATION_INTERVAL_SECONDS=0.01,
    )


@pytest.mark.asyncio
async def test_market_data_flow_normalizes_analyzes_publishes_and_persists():
    _redis_server.store = {}
    server = await asyncio.start_server(_redis_server, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    settings = _settings(f"redis://127.0.0.1:{port}/0")
    database = Database(settings)
    redis = RedisManager(settings)
    try:
        async with database.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        session = database.session_factory()
        bus = EngineCommunicationLayer(PubSubFramework(redis.client), CacheManager(redis.client))
        runtime = MarketDataRuntime(settings, session, bus, MarketDataEngine(default_runtime_config().liquidity, bus), RuntimeMetrics())
        fixture = {
            "e": "depthUpdate",
            "E": int(datetime.now(timezone.utc).timestamp() * 1000),
            "b": [["99999", "1.2"], ["99998", "2.0"]],
            "a": [["100001", "1.0"], ["100002", "2.5"]],
            "u": 101,
        }
        await runtime.ingest_fixture(ExecutionVenue.binance, "BTC/USDT", fixture)
        await session.commit()
        latest = await redis.client.get("latest:marketdata:orderbook:binance:BTC/USDT")
        analytics = await redis.client.get("latest:marketdata:analytics:binance:BTC/USDT")
        assert latest is not None
        assert analytics is not None
        assert json.loads(latest)["symbol"] == "BTC/USDT"
        assert runtime.health()["last_message_timestamp"]
        assert runtime.health()["market_data_rows_written"] == 1
        assert runtime.health()["liquidity_rows_written"] == 1
        market_data_count = await session.scalar(select(func.count()).select_from(models.MarketData))
        liquidity_count = await session.scalar(select(func.count()).select_from(models.LiquidityMetric))
        assert market_data_count == 1
        assert liquidity_count == 1
        await session.close()
    finally:
        await redis.close()
        await database.close()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_market_data_payload_datetime_is_serialized_before_json_bind():
    settings = _settings("redis://localhost:6379/0")
    database = Database(settings)
    try:
        async with database.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with database.session(actor_service="test") as session:
            pair = models.TradingPair(
                exchange_name="binance",
                base_asset="BTC",
                quote_asset="USDT",
                normalized_symbol="BTC/USDT",
                venue_symbol="BTCUSDT",
                price_precision=8,
                quantity_precision=8,
                is_enabled=True,
            )
            session.add(pair)
            await session.flush()
            source_timestamp = datetime.now(timezone.utc)
            session.add(
                models.MarketData(
                    exchange_name="binance",
                    trading_pair_id=pair.id,
                    data_type="order_book",
                    bid_price=99999.0,
                    bid_size=1.0,
                    ask_price=100001.0,
                    ask_size=1.0,
                    last_price=100000.0,
                    source_timestamp=source_timestamp,
                    payload={"source_timestamp": source_timestamp, "nested": {"seen_at": source_timestamp}},
                )
            )
        async with database.session() as session:
            market_data_count = await session.scalar(select(func.count()).select_from(models.MarketData))
            assert market_data_count == 1
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_market_maker_flow_generates_risk_checks_paper_fills_and_reconciles():
    _redis_server.store = {}
    server = await asyncio.start_server(_redis_server, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    settings = _settings(f"redis://127.0.0.1:{port}/0")
    database = Database(settings)
    redis = RedisManager(settings)
    try:
        async with database.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        session = database.session_factory()
        bus = EngineCommunicationLayer(PubSubFramework(redis.client), CacheManager(redis.client))
        quote_engine = QuoteEngine(default_runtime_config().spread, default_runtime_config().order_size, default_runtime_config().inventory)
        runtime = MarketMakerRuntime(settings, session, bus, quote_engine, RuntimeMetrics())
        orderbook = OrderBookSnapshot(
            "binance",
            "BTC/USDT",
            bids=[OrderBookLevel(99999, 5.0)],
            asks=[OrderBookLevel(100001, 5.0)],
            source_timestamp=datetime.now(timezone.utc),
            sequence="1",
        )
        await runtime.ingest_market_event("marketdata:orderbook:binance:BTC/USDT", {
            "exchange": "binance",
            "symbol": "BTC/USDT",
            "bids": [dict(price=99999, size=5.0)],
            "asks": [dict(price=100001, size=5.0)],
            "source_timestamp": orderbook.source_timestamp.isoformat(),
            "sequence": "1",
        })
        await runtime.ingest_market_event("marketdata:analytics:binance:BTC/USDT", {
            "spread": {"spread_bps": 0.2},
            "liquidity": {"imbalance_ratio": 0.0},
            "realized_volatility": 0.0,
        })
        await runtime.tick()
        generated = runtime.metrics.counters.get("market_maker.quotes_generated", 0)
        assert generated > 0
        assert runtime.metrics.counters.get("risk.approvals", 0) > 0
        quote = quote_engine.generate_quotes(MarketState("BTC/USDT", 100000, 0.2, 0.0, 0.0), InventoryState(0.5, 0.5, 0.0))[0]
        crossing_book = OrderBookSnapshot("binance", "BTC/USDT", bids=[OrderBookLevel(99990, 5)], asks=[OrderBookLevel(quote.price - 1, 5)], source_timestamp=datetime.now(timezone.utc))
        intent = OrderIntent(ExecutionVenue.binance, "BTC/USDT", ExecutionSide.buy, ExecutionOrderType.limit, Decimal(str(quote.quantity)), Decimal(str(quote.price)), "paper-crossing-order", TimeInForce.gtc)
        await runtime.paper.place_order(intent, crossing_book)
        assert runtime.paper.fills
        snapshot = runtime.paper.reconciliation_snapshot()
        assert snapshot.balances
        assert snapshot.positions
        await session.commit()
        await session.close()
    finally:
        await redis.close()
        await database.close()
        server.close()
        await server.wait_closed()
