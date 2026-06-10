from decimal import Decimal

from mmbot.execution.models import ExecutionOrderType, ExecutionSide, ExecutionVenue, OrderIntent
from mmbot.production.canary import CanaryController, CanaryPolicy, CanaryState, LaunchMode


def _intent(price='100', qty='1'):
    return OrderIntent(ExecutionVenue.binance, 'BTC/USDT', ExecutionSide.buy, ExecutionOrderType.limit, Decimal(qty), Decimal(price), 'cid')


def test_read_only_mode_blocks_external_execution():
    controller = CanaryController(CanaryPolicy(Decimal('1000'), Decimal('100'), 10, Decimal('1000'), Decimal('500')), CanaryState(LaunchMode.read_only))
    decision = controller.evaluate(_intent())
    assert decision.accepted is False
    assert decision.execution_allowed is False
    assert decision.reason == 'read_only_mode'


def test_dry_run_accepts_but_blocks_external_execution():
    controller = CanaryController(CanaryPolicy(Decimal('1000'), Decimal('100'), 10, Decimal('1000'), Decimal('500')), CanaryState(LaunchMode.dry_run))
    decision = controller.evaluate(_intent())
    assert decision.accepted is True
    assert decision.execution_allowed is False


def test_canary_auto_shutdown_on_order_limit():
    controller = CanaryController(CanaryPolicy(Decimal('1000'), Decimal('100'), 1, Decimal('1000'), Decimal('500')), CanaryState(LaunchMode.canary))
    assert controller.record_order(_intent()).execution_allowed is True
    decision = controller.evaluate(_intent())
    assert decision.accepted is False
    assert decision.reason == 'max_order_count_exceeded'
    assert controller.state.kill_switch_active is True
