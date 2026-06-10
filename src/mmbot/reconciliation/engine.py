from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Iterable


class ReconciliationSeverity(str, Enum):
    info = "info"
    warning = "warning"
    critical = "critical"


@dataclass(frozen=True)
class BalanceRecord:
    asset: str
    total: Decimal
    available: Decimal
    reserved: Decimal


@dataclass(frozen=True)
class PositionRecord:
    symbol: str
    asset: str
    quantity: Decimal
    notional: Decimal


@dataclass(frozen=True)
class OrderRecord:
    client_order_id: str
    exchange_order_id: str | None
    symbol: str
    status: str
    filled_quantity: Decimal
    remaining_quantity: Decimal


@dataclass(frozen=True)
class FillRecord:
    trade_id: str
    order_id: str
    symbol: str
    quantity: Decimal
    price: Decimal
    fee: Decimal


@dataclass(frozen=True)
class PnlRecord:
    realized: Decimal
    unrealized: Decimal
    fees: Decimal


@dataclass(frozen=True)
class ReconciliationSnapshot:
    balances: list[BalanceRecord] = field(default_factory=list)
    positions: list[PositionRecord] = field(default_factory=list)
    orders: list[OrderRecord] = field(default_factory=list)
    fills: list[FillRecord] = field(default_factory=list)
    pnl: PnlRecord | None = None


@dataclass(frozen=True)
class ReconciliationMismatch:
    category: str
    key: str
    severity: ReconciliationSeverity
    message: str
    exchange_value: str | None
    internal_value: str | None


class ReconciliationEngine:
    def __init__(self, balance_tolerance: Decimal = Decimal("0.00000001"), notional_tolerance: Decimal = Decimal("0.01")):
        self.balance_tolerance = balance_tolerance
        self.notional_tolerance = notional_tolerance

    def reconcile(self, exchange: ReconciliationSnapshot, internal: ReconciliationSnapshot) -> list[ReconciliationMismatch]:
        mismatches: list[ReconciliationMismatch] = []
        mismatches.extend(self._compare_balances(exchange.balances, internal.balances))
        mismatches.extend(self._compare_positions(exchange.positions, internal.positions))
        mismatches.extend(self._compare_orders(exchange.orders, internal.orders))
        mismatches.extend(self._compare_fills(exchange.fills, internal.fills))
        mismatches.extend(self._compare_pnl(exchange.pnl, internal.pnl))
        return mismatches

    def generate_alerts(self, mismatches: list[ReconciliationMismatch]) -> list[dict[str, str]]:
        return [
            {
                "severity": mismatch.severity.value,
                "title": f"Reconciliation mismatch: {mismatch.category}",
                "body": f"{mismatch.key}: {mismatch.message} exchange={mismatch.exchange_value} internal={mismatch.internal_value}",
            }
            for mismatch in mismatches
        ]

    def _compare_balances(self, exchange: Iterable[BalanceRecord], internal: Iterable[BalanceRecord]) -> list[ReconciliationMismatch]:
        exchange_map = {item.asset: item for item in exchange}
        internal_map = {item.asset: item for item in internal}
        mismatches: list[ReconciliationMismatch] = []
        for asset in sorted(set(exchange_map) | set(internal_map)):
            left = exchange_map.get(asset)
            right = internal_map.get(asset)
            if left is None or right is None:
                mismatches.append(ReconciliationMismatch("balance", asset, ReconciliationSeverity.critical, "missing balance record", str(left), str(right)))
                continue
            if abs(left.total - right.total) > self.balance_tolerance:
                mismatches.append(ReconciliationMismatch("balance", asset, ReconciliationSeverity.critical, "total balance mismatch", str(left.total), str(right.total)))
            if abs(left.available - right.available) > self.balance_tolerance:
                mismatches.append(ReconciliationMismatch("balance", asset, ReconciliationSeverity.warning, "available balance mismatch", str(left.available), str(right.available)))
        return mismatches

    def _compare_positions(self, exchange: Iterable[PositionRecord], internal: Iterable[PositionRecord]) -> list[ReconciliationMismatch]:
        exchange_map = {(item.symbol, item.asset): item for item in exchange}
        internal_map = {(item.symbol, item.asset): item for item in internal}
        mismatches: list[ReconciliationMismatch] = []
        for key in sorted(set(exchange_map) | set(internal_map)):
            left = exchange_map.get(key)
            right = internal_map.get(key)
            if left is None or right is None:
                mismatches.append(ReconciliationMismatch("position", "/".join(key), ReconciliationSeverity.critical, "missing position record", str(left), str(right)))
                continue
            if abs(left.quantity - right.quantity) > self.balance_tolerance:
                mismatches.append(ReconciliationMismatch("position", "/".join(key), ReconciliationSeverity.critical, "position quantity mismatch", str(left.quantity), str(right.quantity)))
            if abs(left.notional - right.notional) > self.notional_tolerance:
                mismatches.append(ReconciliationMismatch("position", "/".join(key), ReconciliationSeverity.warning, "position notional mismatch", str(left.notional), str(right.notional)))
        return mismatches

    def _compare_orders(self, exchange: Iterable[OrderRecord], internal: Iterable[OrderRecord]) -> list[ReconciliationMismatch]:
        exchange_map = {item.client_order_id: item for item in exchange}
        internal_map = {item.client_order_id: item for item in internal}
        mismatches: list[ReconciliationMismatch] = []
        for key in sorted(set(exchange_map) | set(internal_map)):
            left = exchange_map.get(key)
            right = internal_map.get(key)
            if left is None or right is None:
                mismatches.append(ReconciliationMismatch("order", key, ReconciliationSeverity.critical, "missing order record", str(left), str(right)))
                continue
            if left.status != right.status:
                mismatches.append(ReconciliationMismatch("order", key, ReconciliationSeverity.critical, "order status mismatch", left.status, right.status))
            if abs(left.filled_quantity - right.filled_quantity) > self.balance_tolerance:
                mismatches.append(ReconciliationMismatch("order", key, ReconciliationSeverity.warning, "filled quantity mismatch", str(left.filled_quantity), str(right.filled_quantity)))
        return mismatches

    def _compare_fills(self, exchange: Iterable[FillRecord], internal: Iterable[FillRecord]) -> list[ReconciliationMismatch]:
        exchange_ids = {item.trade_id for item in exchange}
        internal_ids = {item.trade_id for item in internal}
        return [ReconciliationMismatch("fill", trade_id, ReconciliationSeverity.critical, "fill missing from one side", str(trade_id in exchange_ids), str(trade_id in internal_ids)) for trade_id in sorted(exchange_ids ^ internal_ids)]

    def _compare_pnl(self, exchange: PnlRecord | None, internal: PnlRecord | None) -> list[ReconciliationMismatch]:
        if exchange is None and internal is None:
            return []
        if exchange is None or internal is None:
            return [ReconciliationMismatch("pnl", "summary", ReconciliationSeverity.critical, "missing pnl record", str(exchange), str(internal))]
        mismatches: list[ReconciliationMismatch] = []
        for field_name in ("realized", "unrealized", "fees"):
            left = getattr(exchange, field_name)
            right = getattr(internal, field_name)
            if abs(left - right) > self.notional_tolerance:
                mismatches.append(ReconciliationMismatch("pnl", field_name, ReconciliationSeverity.warning, "pnl component mismatch", str(left), str(right)))
        return mismatches
