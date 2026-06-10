from __future__ import annotations

import argparse

from mmbot.core.config import default_runtime_config
from mmbot.engines.market_making.engine import InventoryState, MarketState, QuoteEngine


def main() -> None:
    parser = argparse.ArgumentParser(description="Engine command utilities")
    parser.add_argument("command", choices=["sample-quotes"])
    args = parser.parse_args()
    if args.command == "sample-quotes":
        config = default_runtime_config()
        engine = QuoteEngine(config.spread, config.order_size, config.inventory)
        quotes = engine.generate_quotes(MarketState("BTC/USDT", 100000, 20, 0.01, 0.0), InventoryState(0.5, 0.5, 0))
        print([quote.__dict__ for quote in quotes])
