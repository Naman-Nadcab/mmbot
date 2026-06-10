from datetime import datetime, timezone

from mmbot.core.config import default_runtime_config
from mmbot.engines.market_data.engine import MarketDataEngine
from mmbot.exchanges.types import OrderBookLevel, OrderBookSnapshot, TradeTick


def test_market_data_spread_liquidity_and_volatility():
    config = default_runtime_config()
    engine = MarketDataEngine(config.liquidity)
    orderbook = OrderBookSnapshot(
        exchange="binance",
        symbol="BTC/USDT",
        bids=[OrderBookLevel(99.0, 2.0), OrderBookLevel(98.0, 3.0)],
        asks=[OrderBookLevel(101.0, 1.0), OrderBookLevel(102.0, 4.0)],
        source_timestamp=datetime.now(timezone.utc),
    )
    spread = engine.calculate_spread(orderbook)
    liquidity = engine.liquidity_analytics(orderbook)
    trades = [TradeTick("binance", "BTC/USDT", str(i), 100 + i, 0.5, "buy", datetime.now(timezone.utc), {}) for i in range(5)]
    stats = engine.market_statistics("BTC/USDT", trades)
    assert spread.mid == 100.0
    assert spread.spread_bps == 200.0
    assert liquidity.top_of_book_depth == 10.0
    assert stats.trade_count == 5
    assert stats.realized_volatility >= 0
