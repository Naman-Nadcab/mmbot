from datetime import datetime, timezone
from decimal import Decimal

from mmbot.execution.models import ExecutionSide
from mmbot.surveillance.anti_manipulation import AntiManipulationEngine, SurveillanceOrder


def test_self_trade_detection_alerts_on_crossing_same_account_order():
    engine = AntiManipulationEngine()
    now = datetime.now(timezone.utc)
    first = SurveillanceOrder("acct-a", "BTC/USDT", ExecutionSide.buy, Decimal("100"), Decimal("1"), "buy-1", now)
    second = SurveillanceOrder("acct-a", "BTC/USDT", ExecutionSide.sell, Decimal("100"), Decimal("1"), "sell-1", now)
    assert engine.record_order(first) == []
    alerts = engine.record_order(second)
    assert alerts[0].alert_type == "self_trade_prevention"
