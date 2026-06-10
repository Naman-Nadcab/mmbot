from mmbot.core.config import default_runtime_config
from mmbot.engines.market_making.engine import InventoryState, MarketState, OrderReplacementEngine, QuoteEngine


def test_quote_engine_generates_configured_ladder():
    config = default_runtime_config()
    engine = QuoteEngine(config.spread, config.order_size, config.inventory)
    quotes = engine.generate_quotes(MarketState("BTC/USDT", 100000.0, 20.0, 0.01, 0.1), InventoryState(0.5, 0.5, 0.0))
    assert len(quotes) == config.order_size.ladder_levels * 2
    assert all(quote.quantity >= config.order_size.min_order_size for quote in quotes)
    assert min(q.price for q in quotes if q.side == "buy") < 100000.0
    assert max(q.price for q in quotes if q.side == "sell") > 100000.0


def test_order_replacement_detects_drift():
    config = default_runtime_config()
    engine = QuoteEngine(config.spread, config.order_size, config.inventory)
    desired = engine.generate_quotes(MarketState("ETH/USDT", 3000.0, 15.0, 0.0, 0.0), InventoryState(0.5, 0.5, 0.0))
    existing = {(desired[0].side, desired[0].level): {"price": desired[0].price * 0.99, "quantity": desired[0].quantity, "order_id": "abc"}}
    decisions = OrderReplacementEngine().decisions(desired[:1], existing, price_threshold_bps=5)
    assert decisions[0].replace is True
    assert decisions[0].reason == "price_or_size_drift"
