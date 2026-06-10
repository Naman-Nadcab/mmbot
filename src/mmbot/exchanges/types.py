from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any


class ExchangeName(str, Enum):
    binance = "binance"
    coinstore = "coinstore"
    mexc = "mexc"
    gate = "gate"
    bitmart = "bitmart"
    kucoin = "kucoin"


@dataclass(frozen=True)
class RateLimitRule:
    requests: int
    window_seconds: int


@dataclass(frozen=True)
class ExchangeCapabilities:
    rest: bool
    websocket: bool
    orderbook_stream: bool
    trades_stream: bool
    ticker_stream: bool
    kline_stream: bool
    private_trading: bool
    signed_requests: bool


@dataclass(frozen=True)
class ExchangeDefinition:
    name: ExchangeName
    rest_base_url: str
    websocket_url: str
    rate_limit: RateLimitRule
    capabilities: ExchangeCapabilities
    health_path: str


@dataclass(frozen=True)
class OrderBookLevel:
    price: float
    size: float


@dataclass(frozen=True)
class OrderBookSnapshot:
    exchange: str
    symbol: str
    bids: list[OrderBookLevel]
    asks: list[OrderBookLevel]
    source_timestamp: datetime
    sequence: str | None = None


@dataclass(frozen=True)
class TradeTick:
    exchange: str
    symbol: str
    trade_id: str
    price: float
    quantity: float
    side: str
    traded_at: datetime
    metadata: dict[str, Any]


@dataclass(frozen=True)
class Ticker:
    exchange: str
    symbol: str
    bid_price: float | None
    bid_size: float | None
    ask_price: float | None
    ask_size: float | None
    last_price: float | None
    volume_24h: float | None
    source_timestamp: datetime


@dataclass(frozen=True)
class Kline:
    exchange: str
    symbol: str
    interval: str
    open_time: datetime
    close_time: datetime
    open_price: float
    high_price: float
    low_price: float
    close_price: float
    volume: float
