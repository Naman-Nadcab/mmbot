import asyncio
from datetime import datetime, timezone
from decimal import Decimal

from mmbot.core.config import default_runtime_config
from mmbot.engines.risk.engine import OrderIntent, RiskEngine
from mmbot.engines.strategy.advanced import InstitutionalStrategyEngine
from mmbot.simulation.backtesting import ReplayEvent, ReplayEventType, StrategySimulator
from mmbot.stress.testing import StressHarness


def test_strategy_simulator_produces_performance_result():
    config = default_runtime_config()
    simulator = StrategySimulator(InstitutionalStrategyEngine(config.spread.base_spread_bps), RiskEngine(config.risk))
    events = [ReplayEvent(datetime.now(timezone.utc), ReplayEventType.trade, "BTC/USDT", Decimal("100"), Decimal("0.01"), volatility=0.001, depth=100000)]
    result = simulator.run(events, Decimal("10000"), Decimal("0"), Decimal("100000"))
    assert result.decision_count == 1
    assert result.final_equity > 0


def test_stress_harness_risk_trigger_result():
    risk = RiskEngine(default_runtime_config().risk)
    result = StressHarness().risk_trigger_test(risk, OrderIntent("BTC/USDT", "buy", 100.0, 1.0), iterations=10)
    assert result.operations == 10
    assert result.throughput_per_second > 0
