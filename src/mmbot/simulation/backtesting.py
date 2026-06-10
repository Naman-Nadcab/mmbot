from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Iterable

from mmbot.engines.risk.engine import OrderIntent as RiskOrderIntent, RiskEngine
from mmbot.engines.strategy.advanced import InstitutionalStrategyEngine, InventoryProfile, MicrostructureSnapshot, StrategyDecision


class ReplayEventType(str, Enum):
    ticker = "ticker"
    trade = "trade"
    orderbook = "orderbook"
    fill = "fill"


@dataclass(frozen=True)
class ReplayEvent:
    timestamp: datetime
    event_type: ReplayEventType
    symbol: str
    price: Decimal
    quantity: Decimal = Decimal("0")
    spread_bps: float = 10.0
    volatility: float = 0.0
    imbalance: float = 0.0
    depth: float = 0.0


@dataclass
class SimulationState:
    cash: Decimal
    inventory: Decimal
    realized_pnl: Decimal = Decimal("0")
    fees: Decimal = Decimal("0")
    decisions: list[StrategyDecision] = field(default_factory=list)
    rejected_orders: int = 0

    def mark_to_market(self, price: Decimal) -> Decimal:
        return self.cash + self.inventory * price


@dataclass(frozen=True)
class SimulationResult:
    final_equity: Decimal
    realized_pnl: Decimal
    inventory: Decimal
    fees: Decimal
    max_drawdown: Decimal
    decision_count: int
    rejected_orders: int


class HistoricalReplayEngine:
    def replay(self, events: Iterable[ReplayEvent]) -> list[ReplayEvent]:
        return sorted(events, key=lambda event: event.timestamp)


class StrategySimulator:
    def __init__(self, strategy: InstitutionalStrategyEngine, risk: RiskEngine, fee_bps: Decimal = Decimal("1")):
        self.strategy = strategy
        self.risk = risk
        self.fee_bps = fee_bps

    def run(self, events: Iterable[ReplayEvent], initial_cash: Decimal, initial_inventory: Decimal, max_inventory_notional: Decimal) -> SimulationResult:
        state = SimulationState(initial_cash, initial_inventory)
        equity_curve: list[Decimal] = []
        replay = HistoricalReplayEngine().replay(events)
        for event in replay:
            equity = state.mark_to_market(event.price)
            equity_curve.append(equity)
            total_notional = abs(state.inventory * event.price)
            base_ratio = float(total_notional / max(equity, Decimal("1"))) if equity > 0 else 0.0
            micro = MicrostructureSnapshot(float(event.price), event.spread_bps, event.volatility, event.imbalance, 0.0, event.depth, 0.0, 0.0)
            inventory = InventoryProfile(base_ratio, 0.5, float(total_notional), float(max_inventory_notional))
            decision = self.strategy.decide(micro, inventory)
            state.decisions.append(decision)
            if event.event_type is ReplayEventType.trade:
                side = "buy" if decision.reduction_side == "buy" else "sell" if decision.reduction_side == "sell" else "buy"
                quantity = min(event.quantity, Decimal("0.01"))
                try:
                    self.risk.assert_order_allowed(RiskOrderIntent(event.symbol, side, float(event.price), float(quantity)), float(total_notional), float(total_notional), 0, float(state.realized_pnl))
                except Exception:
                    state.rejected_orders += 1
                    continue
                notional = event.price * quantity
                fee = notional * self.fee_bps / Decimal("10000")
                if side == "buy":
                    state.cash -= notional + fee
                    state.inventory += quantity
                else:
                    state.cash += notional - fee
                    state.inventory -= quantity
                    state.realized_pnl += notional - fee
                state.fees += fee
        max_drawdown = self._max_drawdown(equity_curve)
        final_price = replay[-1].price if replay else Decimal("0")
        return SimulationResult(state.mark_to_market(final_price), state.realized_pnl, state.inventory, state.fees, max_drawdown, len(state.decisions), state.rejected_orders)

    def _max_drawdown(self, equity_curve: list[Decimal]) -> Decimal:
        peak = Decimal("0")
        max_drawdown = Decimal("0")
        for equity in equity_curve:
            peak = max(peak, equity)
            if peak > 0:
                max_drawdown = max(max_drawdown, (peak - equity) / peak)
        return max_drawdown


class PerformanceAnalytics:
    def summarize(self, result: SimulationResult) -> dict[str, str | int]:
        return {
            "final_equity": str(result.final_equity),
            "realized_pnl": str(result.realized_pnl),
            "inventory": str(result.inventory),
            "fees": str(result.fees),
            "max_drawdown": str(result.max_drawdown),
            "decision_count": result.decision_count,
            "rejected_orders": result.rejected_orders,
        }
