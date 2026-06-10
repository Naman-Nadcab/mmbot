from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum

from mmbot.execution.models import ExecutionSide


class ManipulationSeverity(str, Enum):
    warning = "warning"
    critical = "critical"
    emergency = "emergency"


@dataclass(frozen=True)
class SurveillanceOrder:
    account_id: str
    symbol: str
    side: ExecutionSide
    price: Decimal
    quantity: Decimal
    client_order_id: str
    created_at: datetime


@dataclass(frozen=True)
class SurveillanceFill:
    account_id: str
    counterparty_account_id: str | None
    symbol: str
    side: ExecutionSide
    price: Decimal
    quantity: Decimal
    client_order_id: str
    trade_id: str
    executed_at: datetime


@dataclass(frozen=True)
class CancelEvent:
    account_id: str
    symbol: str
    client_order_id: str
    cancelled_at: datetime


@dataclass(frozen=True)
class ManipulationAlert:
    alert_type: str
    severity: ManipulationSeverity
    symbol: str
    message: str
    evidence: dict[str, object]


class AntiManipulationEngine:
    def __init__(self, cancel_window_seconds: int = 60, max_cancels_per_window: int = 100, max_orders_per_second: int = 50, abnormal_fill_notional: Decimal = Decimal("1000000")):
        self.cancel_window = timedelta(seconds=cancel_window_seconds)
        self.max_cancels = max_cancels_per_window
        self.max_orders_per_second = max_orders_per_second
        self.abnormal_fill_notional = abnormal_fill_notional
        self.open_orders: dict[str, SurveillanceOrder] = {}
        self.cancels: dict[tuple[str, str], deque[datetime]] = defaultdict(deque)
        self.order_times: dict[tuple[str, str], deque[datetime]] = defaultdict(deque)
        self.recent_fills: dict[tuple[str, str], deque[SurveillanceFill]] = defaultdict(deque)

    def record_order(self, order: SurveillanceOrder) -> list[ManipulationAlert]:
        alerts: list[ManipulationAlert] = []
        now = order.created_at
        key = (order.account_id, order.symbol)
        times = self.order_times[key]
        times.append(now)
        self._trim_times(times, now - timedelta(seconds=1))
        if len(times) > self.max_orders_per_second:
            alerts.append(ManipulationAlert("order_spam", ManipulationSeverity.critical, order.symbol, "order submission rate exceeded", {"account_id": order.account_id, "orders_last_second": len(times)}))
        alerts.extend(self._detect_self_trade(order))
        alerts.extend(self._detect_cross_account_collision(order))
        self.open_orders[order.client_order_id] = order
        return alerts

    def record_cancel(self, event: CancelEvent) -> list[ManipulationAlert]:
        self.open_orders.pop(event.client_order_id, None)
        key = (event.account_id, event.symbol)
        cancels = self.cancels[key]
        cancels.append(event.cancelled_at)
        self._trim_times(cancels, event.cancelled_at - self.cancel_window)
        if len(cancels) > self.max_cancels:
            return [ManipulationAlert("excessive_cancel", ManipulationSeverity.critical, event.symbol, "cancel rate exceeded", {"account_id": event.account_id, "cancels": len(cancels), "window_seconds": self.cancel_window.total_seconds()})]
        return []

    def record_fill(self, fill: SurveillanceFill) -> list[ManipulationAlert]:
        alerts: list[ManipulationAlert] = []
        notional = fill.price * fill.quantity
        if notional >= self.abnormal_fill_notional:
            alerts.append(ManipulationAlert("abnormal_fill", ManipulationSeverity.warning, fill.symbol, "large fill detected", {"notional": str(notional), "trade_id": fill.trade_id}))
        if fill.counterparty_account_id and fill.counterparty_account_id == fill.account_id:
            alerts.append(ManipulationAlert("wash_trade", ManipulationSeverity.emergency, fill.symbol, "same-account execution detected", {"trade_id": fill.trade_id, "account_id": fill.account_id}))
        key = (fill.account_id, fill.symbol)
        fills = self.recent_fills[key]
        fills.append(fill)
        while fills and fills[0].executed_at < fill.executed_at - timedelta(minutes=5):
            fills.popleft()
        alerts.extend(self._detect_abnormal_execution_cluster(fill, list(fills)))
        return alerts

    def _detect_self_trade(self, order: SurveillanceOrder) -> list[ManipulationAlert]:
        opposite = ExecutionSide.sell if order.side is ExecutionSide.buy else ExecutionSide.buy
        for existing in self.open_orders.values():
            if existing.account_id == order.account_id and existing.symbol == order.symbol and existing.side is opposite and existing.price == order.price:
                return [ManipulationAlert("self_trade_prevention", ManipulationSeverity.emergency, order.symbol, "opposing same-account orders would cross", {"new_order": order.client_order_id, "existing_order": existing.client_order_id})]
        return []

    def _detect_cross_account_collision(self, order: SurveillanceOrder) -> list[ManipulationAlert]:
        opposite = ExecutionSide.sell if order.side is ExecutionSide.buy else ExecutionSide.buy
        alerts: list[ManipulationAlert] = []
        for existing in self.open_orders.values():
            if existing.account_id != order.account_id and existing.symbol == order.symbol and existing.side is opposite and existing.price == order.price:
                alerts.append(ManipulationAlert("cross_account_collision", ManipulationSeverity.critical, order.symbol, "managed accounts have crossing orders", {"new_account": order.account_id, "existing_account": existing.account_id, "price": str(order.price)}))
        return alerts

    def _detect_abnormal_execution_cluster(self, fill: SurveillanceFill, fills: list[SurveillanceFill]) -> list[ManipulationAlert]:
        same_price = [item for item in fills if item.price == fill.price and item.side is fill.side]
        if len(same_price) >= 20:
            return [ManipulationAlert("abnormal_execution", ManipulationSeverity.warning, fill.symbol, "clustered executions at identical price and side", {"count": len(same_price), "price": str(fill.price)})]
        return []

    def _trim_times(self, values: deque[datetime], cutoff: datetime) -> None:
        while values and values[0] < cutoff:
            values.popleft()
