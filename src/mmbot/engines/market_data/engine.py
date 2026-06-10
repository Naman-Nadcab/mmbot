from __future__ import annotations

import math
import statistics
import uuid
from dataclasses import asdict, dataclass

from mmbot.core.config import LiquiditySettings
from mmbot.db import models
from mmbot.db.repositories import MarketDataRepository
from mmbot.exchanges.types import Kline, OrderBookSnapshot, Ticker, TradeTick
from mmbot.redis.manager import EngineCommunicationLayer


@dataclass(frozen=True)
class SpreadMetrics:
    bid: float
    ask: float
    mid: float
    spread: float
    spread_bps: float


@dataclass(frozen=True)
class LiquidityAnalytics:
    top_of_book_depth: float
    depth_1pct: float
    depth_5pct: float
    imbalance_ratio: float
    imbalance_detected: bool


@dataclass(frozen=True)
class MarketStatistics:
    last_price: float | None
    trade_count: int
    volume: float
    realized_volatility: float
    high_price: float | None
    low_price: float | None


class MarketDataEngine:
    def __init__(self, liquidity_settings: LiquiditySettings, bus: EngineCommunicationLayer | None = None):
        self.liquidity_settings = liquidity_settings
        self.bus = bus
        self._trade_prices: dict[str, list[float]] = {}

    def calculate_spread(self, orderbook: OrderBookSnapshot) -> SpreadMetrics:
        if not orderbook.bids or not orderbook.asks:
            raise ValueError("orderbook requires at least one bid and one ask")
        bid = max(level.price for level in orderbook.bids)
        ask = min(level.price for level in orderbook.asks)
        if bid <= 0 or ask <= 0 or ask < bid:
            raise ValueError("orderbook top of book is invalid")
        mid = (bid + ask) / 2
        spread = ask - bid
        return SpreadMetrics(bid=bid, ask=ask, mid=mid, spread=spread, spread_bps=(spread / mid) * 10000)

    def liquidity_analytics(self, orderbook: OrderBookSnapshot) -> LiquidityAnalytics:
        spread = self.calculate_spread(orderbook)
        bid_depth = sum(level.size for level in orderbook.bids[: self.liquidity_settings.depth_levels])
        ask_depth = sum(level.size for level in orderbook.asks[: self.liquidity_settings.depth_levels])
        depth_1pct = self._depth_within(orderbook, spread.mid, 0.01)
        depth_5pct = self._depth_within(orderbook, spread.mid, 0.05)
        total = bid_depth + ask_depth
        imbalance = 0.0 if total == 0 else (bid_depth - ask_depth) / total
        return LiquidityAnalytics(bid_depth + ask_depth, depth_1pct, depth_5pct, imbalance, abs(imbalance) >= self.liquidity_settings.imbalance_threshold)

    def market_statistics(self, symbol: str, trades: list[TradeTick], klines: list[Kline] | None = None) -> MarketStatistics:
        prices = [trade.price for trade in trades if trade.price > 0]
        self._trade_prices.setdefault(symbol, []).extend(prices)
        self._trade_prices[symbol] = self._trade_prices[symbol][-1000:]
        returns = [math.log(b / a) for a, b in zip(self._trade_prices[symbol], self._trade_prices[symbol][1:]) if a > 0 and b > 0]
        vol = statistics.pstdev(returns) * math.sqrt(len(returns)) if len(returns) > 1 else 0.0
        volume = sum(trade.quantity for trade in trades)
        kline_prices = [k.close_price for k in klines or []]
        all_prices = prices + kline_prices
        return MarketStatistics(prices[-1] if prices else (kline_prices[-1] if kline_prices else None), len(trades), volume, vol, max(all_prices) if all_prices else None, min(all_prices) if all_prices else None)

    async def persist_ticker(self, repository: MarketDataRepository, trading_pair_id: uuid.UUID, ticker: Ticker) -> models.MarketData:
        row = models.MarketData(exchange_name=ticker.exchange, trading_pair_id=trading_pair_id, data_type="ticker", bid_price=ticker.bid_price, bid_size=ticker.bid_size, ask_price=ticker.ask_price, ask_size=ticker.ask_size, last_price=ticker.last_price, volume_24h=ticker.volume_24h, source_timestamp=ticker.source_timestamp, payload=asdict(ticker))
        saved = await repository.add(row)
        if self.bus:
            await self.bus.publish_event("market-data", "ticker", asdict(ticker))
        return saved

    async def distribute_orderbook(self, orderbook: OrderBookSnapshot) -> None:
        if self.bus:
            await self.bus.publish_event("market-data", "orderbook", asdict(orderbook))

    def _depth_within(self, orderbook: OrderBookSnapshot, mid: float, percent: float) -> float:
        min_bid = mid * (1 - percent)
        max_ask = mid * (1 + percent)
        bid_depth = sum(level.size for level in orderbook.bids if level.price >= min_bid)
        ask_depth = sum(level.size for level in orderbook.asks if level.price <= max_ask)
        return bid_depth + ask_depth
