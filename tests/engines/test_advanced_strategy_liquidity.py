from mmbot.engines.liquidity.shaping import LiquidityShapingEngine
from mmbot.engines.strategy.advanced import InstitutionalStrategyEngine, InventoryProfile, MarketRegime, MicrostructureSnapshot


def test_advanced_strategy_widens_under_stress_and_reduces_inventory():
    engine = InstitutionalStrategyEngine(base_spread_bps=20)
    decision = engine.decide(
        MicrostructureSnapshot(100.0, 10.0, 0.08, 0.7, 0.5, 1000.0, -0.2, 200.0),
        InventoryProfile(0.9, 0.5, 90000.0, 100000.0),
    )
    assert decision.regime is MarketRegime.stressed
    assert decision.neutralization_required is True
    assert decision.reduction_side == "sell"
    assert decision.bid_spread_bps > 20


def test_liquidity_shaping_builds_symmetric_depth_plan():
    engine = LiquidityShapingEngine(levels=3, base_distance_bps=5, target_depth=3000)
    shape, synthetic = engine.build_shape(100.0, 1.0, volatility=0.01, inventory_skew_bps=0.0, imbalance=0.0)
    assert len(shape.orders) == 6
    assert shape.target_depth == 3000
    assert synthetic["bid_depth"] > 0
    assert synthetic["ask_depth"] > 0
