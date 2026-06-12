from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from mmbot.core.config import RiskSettings
from mmbot.core.exceptions import KillSwitchActiveError, RiskViolationError


@dataclass(frozen=True)
class OrderIntent:
    symbol: str
    side: str
    price: float
    quantity: float

    @property
    def notional(self) -> float:
        return self.price * self.quantity


@dataclass(frozen=True)
class RiskEvaluation:
    accepted: bool
    score: float
    violations: list[str]


@dataclass
class CircuitBreaker:
    name: str
    threshold: int
    cooldown_seconds: int
    failures: int = 0
    opened_at: datetime | None = None

    def record_success(self) -> None:
        self.failures = 0
        self.opened_at = None

    def record_failure(self) -> None:
        self.failures += 1
        if self.failures >= self.threshold:
            self.opened_at = datetime.now(timezone.utc)

    def is_open(self) -> bool:
        if self.opened_at is None:
            return False
        elapsed = datetime.now(timezone.utc) - self.opened_at
        if elapsed >= timedelta(seconds=self.cooldown_seconds):
            self.record_success()
            return False
        return True


@dataclass
class KillSwitch:
    active: bool = False
    reason: str | None = None
    activated_at: datetime | None = None

    def activate(self, reason: str) -> None:
        self.active = True
        self.reason = reason
        self.activated_at = datetime.now(timezone.utc)

    def deactivate(self) -> None:
        self.active = False
        self.reason = None
        self.activated_at = None


@dataclass
class EmergencyShutdownState:
    active: bool = False
    reason: str | None = None
    started_at: datetime | None = None
    completed_steps: list[str] = field(default_factory=list)


class RiskEngine:
    def __init__(self, settings: RiskSettings):
        self.settings = settings
        self.kill_switch = KillSwitch()
        self.breakers: dict[str, CircuitBreaker] = {
            "exchange_errors": CircuitBreaker("exchange_errors", settings.circuit_breaker_error_threshold, settings.circuit_breaker_cooldown_seconds),
            "market_data_stale": CircuitBreaker("market_data_stale", settings.circuit_breaker_error_threshold, settings.circuit_breaker_cooldown_seconds),
            "dependency_unavailable": CircuitBreaker("dependency_unavailable", settings.circuit_breaker_error_threshold, settings.circuit_breaker_cooldown_seconds),
        }
        self.shutdown_state = EmergencyShutdownState()

    def evaluate_order(self, intent: OrderIntent, position_notional: float, total_exposure: float, open_orders: int, daily_pnl: float) -> RiskEvaluation:
        if self.kill_switch.active:
            raise KillSwitchActiveError(self.kill_switch.reason or "kill switch active")
        violations: list[str] = []
        if intent.notional > self.settings.max_order_notional:
            violations.append("max_order_notional")
        if intent.quantity > self.settings.max_position_quantity:
            violations.append("max_position_quantity")
        if abs(position_notional) + intent.notional > self.settings.max_position_notional:
            violations.append("max_position_notional")
        if abs(total_exposure) + intent.notional > self.settings.max_total_exposure:
            violations.append("max_total_exposure")
        if open_orders + 1 > self.settings.max_open_orders:
            violations.append("max_open_orders")
        if daily_pnl <= -abs(self.settings.max_daily_loss):
            violations.append("max_daily_loss")
        if self.settings.circuit_breaker_enabled:
            for breaker in self.breakers.values():
                if breaker.is_open():
                    violations.append(f"circuit_breaker:{breaker.name}")
        score = self.risk_score(intent, total_exposure, daily_pnl, violations)
        return RiskEvaluation(not violations, score, violations)

    def assert_order_allowed(self, *args, **kwargs) -> RiskEvaluation:
        evaluation = self.evaluate_order(*args, **kwargs)
        if not evaluation.accepted:
            raise RiskViolationError(",".join(evaluation.violations))
        return evaluation

    def risk_score(self, intent: OrderIntent, total_exposure: float, daily_pnl: float, violations: list[str]) -> float:
        order_component = min(1.0, intent.notional / self.settings.max_order_notional)
        exposure_component = min(1.0, abs(total_exposure) / self.settings.max_total_exposure)
        loss_component = min(1.0, abs(min(0.0, daily_pnl)) / self.settings.max_daily_loss)
        violation_component = min(1.0, len(violations) / 5)
        return round((order_component * 0.25 + exposure_component * 0.3 + loss_component * 0.25 + violation_component * 0.2) * 100, 4)

    def record_failure(self, breaker_name: str) -> None:
        self.breakers[breaker_name].record_failure()

    def record_success(self, breaker_name: str) -> None:
        self.breakers[breaker_name].record_success()

    def activate_kill_switch(self, reason: str) -> None:
        self.kill_switch.activate(reason)

    def deactivate_kill_switch(self) -> None:
        self.kill_switch.deactivate()

    def emergency_shutdown(self, reason: str) -> EmergencyShutdownState:
        self.kill_switch.activate(reason)
        self.shutdown_state = EmergencyShutdownState(True, reason, datetime.now(timezone.utc), ["stop_order_creation", "freeze_strategy_state", "emit_alerts"])
        return self.shutdown_state

    def recovery_ready(self) -> bool:
        return self.shutdown_state.active and self.kill_switch.active and all(not breaker.is_open() for breaker in self.breakers.values())

    def recover(self) -> EmergencyShutdownState:
        if not self.recovery_ready():
            raise RiskViolationError("recovery prerequisites are not satisfied")
        self.shutdown_state.completed_steps.append("recovery_approved")
        self.shutdown_state.active = False
        self.kill_switch.deactivate()
        return self.shutdown_state
