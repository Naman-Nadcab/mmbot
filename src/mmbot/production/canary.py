from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum

from mmbot.execution.models import ExecutionOrderType, OrderIntent


class LaunchMode(str, Enum):
    read_only = "read_only"
    shadow = "shadow"
    paper = "paper"
    dry_run = "dry_run"
    canary = "canary"
    live = "live"


@dataclass(frozen=True)
class CanaryPolicy:
    max_position_notional: Decimal
    max_daily_loss: Decimal
    max_order_count: int
    max_inventory_notional: Decimal
    max_order_notional: Decimal
    allowed_order_types: set[ExecutionOrderType] = field(default_factory=lambda: {ExecutionOrderType.limit, ExecutionOrderType.post_only})


@dataclass
class CanaryState:
    mode: LaunchMode
    position_notional: Decimal = Decimal("0")
    inventory_notional: Decimal = Decimal("0")
    daily_pnl: Decimal = Decimal("0")
    order_count: int = 0
    trading_day: date = field(default_factory=lambda: datetime.now(timezone.utc).date())
    kill_switch_active: bool = False
    shutdown_reason: str | None = None


@dataclass(frozen=True)
class CanaryDecision:
    accepted: bool
    execution_allowed: bool
    reason: str
    mode: LaunchMode


class CanaryController:
    def __init__(self, policy: CanaryPolicy, state: CanaryState):
        self.policy = policy
        self.state = state

    def evaluate(self, intent: OrderIntent | None = None) -> CanaryDecision:
        self._roll_day_if_needed()
        if self.state.kill_switch_active:
            return CanaryDecision(False, False, self.state.shutdown_reason or "kill_switch_active", self.state.mode)
        shutdown_reason = self._automatic_shutdown_reason(intent)
        if shutdown_reason:
            self.activate_shutdown(shutdown_reason)
            return CanaryDecision(False, False, shutdown_reason, self.state.mode)
        if self.state.mode is LaunchMode.read_only:
            return CanaryDecision(False, False, "read_only_mode", self.state.mode)
        if self.state.mode in {LaunchMode.shadow, LaunchMode.paper, LaunchMode.dry_run}:
            return CanaryDecision(True, False, f"{self.state.mode.value}_no_external_execution", self.state.mode)
        return CanaryDecision(True, True, "execution_allowed", self.state.mode)

    def record_order(self, intent: OrderIntent) -> CanaryDecision:
        decision = self.evaluate(intent)
        if decision.accepted:
            self.state.order_count += 1
            notional = (intent.price or Decimal("0")) * intent.quantity
            self.state.position_notional += notional if intent.side.value == "buy" else -notional
            self.state.inventory_notional = abs(self.state.position_notional)
        return decision

    def activate_shutdown(self, reason: str) -> None:
        self.state.kill_switch_active = True
        self.state.shutdown_reason = reason

    def reset_after_approval(self, mode: LaunchMode) -> None:
        self.state.mode = mode
        self.state.kill_switch_active = False
        self.state.shutdown_reason = None
        self.state.order_count = 0

    def _automatic_shutdown_reason(self, intent: OrderIntent | None) -> str | None:
        if abs(self.state.position_notional) > self.policy.max_position_notional:
            return "max_position_limit_exceeded"
        if self.state.inventory_notional > self.policy.max_inventory_notional:
            return "max_inventory_limit_exceeded"
        if self.state.daily_pnl <= -abs(self.policy.max_daily_loss):
            return "max_daily_loss_exceeded"
        if self.state.order_count >= self.policy.max_order_count:
            return "max_order_count_exceeded"
        if intent is not None:
            notional = (intent.price or Decimal("0")) * intent.quantity
            if notional > self.policy.max_order_notional:
                return "max_order_notional_exceeded"
            if intent.order_type not in self.policy.allowed_order_types:
                return "order_type_not_allowed_in_canary"
        return None

    def _roll_day_if_needed(self) -> None:
        today = datetime.now(timezone.utc).date()
        if self.state.trading_day != today:
            self.state.trading_day = today
            self.state.order_count = 0
            self.state.daily_pnl = Decimal("0")
