import pytest

from mmbot.core.config import default_runtime_config
from mmbot.core.exceptions import KillSwitchActiveError, RiskViolationError
from mmbot.engines.risk.engine import OrderIntent, RiskEngine


def test_risk_engine_accepts_order_inside_limits():
    engine = RiskEngine(default_runtime_config().risk)
    evaluation = engine.evaluate_order(OrderIntent("BTC/USDT", "buy", 100.0, 1.0), position_notional=0.0, total_exposure=0.0, open_orders=0, daily_pnl=0.0)
    assert evaluation.accepted is True
    assert evaluation.violations == []


def test_risk_engine_rejects_order_outside_limits():
    engine = RiskEngine(default_runtime_config().risk)
    with pytest.raises(RiskViolationError):
        engine.assert_order_allowed(OrderIntent("BTC/USDT", "buy", 100000.0, 10.0), position_notional=0.0, total_exposure=0.0, open_orders=0, daily_pnl=0.0)


def test_kill_switch_blocks_orders():
    engine = RiskEngine(default_runtime_config().risk)
    engine.activate_kill_switch("operator_request")
    with pytest.raises(KillSwitchActiveError):
        engine.evaluate_order(OrderIntent("BTC/USDT", "buy", 100.0, 1.0), position_notional=0.0, total_exposure=0.0, open_orders=0, daily_pnl=0.0)
