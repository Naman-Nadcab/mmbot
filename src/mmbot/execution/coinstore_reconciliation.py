from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mmbot.db import models
from mmbot.execution.models import ExecutionOrder, Balance
from mmbot.reconciliation.engine import BalanceRecord, FillRecord, OrderRecord, ReconciliationEngine, ReconciliationMismatch, ReconciliationSeverity, ReconciliationSnapshot


@dataclass(frozen=True)
class CoinstoreReconciliationReport:
    exchange: ReconciliationSnapshot
    internal: ReconciliationSnapshot
    mismatches: list[ReconciliationMismatch]
    stale_order_ids: list[str]
    orphan_order_ids: list[str]


class CoinstoreLiveReconciler:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.engine = ReconciliationEngine()

    async def reconcile(self, account: models.ExchangeAccount, balances: list[Balance], orders: list[ExecutionOrder], fills: list[dict], stale_order_ids: list[str], orphan_order_ids: list[str]) -> CoinstoreReconciliationReport:
        exchange_snapshot = self._exchange_snapshot(balances, orders, fills)
        internal_snapshot = await self._internal_snapshot(account)
        mismatches = self.engine.reconcile(exchange_snapshot, internal_snapshot)
        for order_id in stale_order_ids:
            mismatches.append(ReconciliationMismatch("order", order_id, ReconciliationSeverity.warning, "local order is stale", "fresh", "stale"))
        for order_id in orphan_order_ids:
            mismatches.append(ReconciliationMismatch("order", order_id, ReconciliationSeverity.critical, "local order missing from exchange active order set", "missing", "present"))
        return CoinstoreReconciliationReport(exchange_snapshot, internal_snapshot, mismatches, stale_order_ids, orphan_order_ids)

    def _exchange_snapshot(self, balances: list[Balance], orders: list[ExecutionOrder], fills: list[dict]) -> ReconciliationSnapshot:
        return ReconciliationSnapshot(
            balances=[BalanceRecord(item.asset, item.total, item.available, item.reserved) for item in balances],
            orders=[
                OrderRecord(
                    item.client_order_id or item.exchange_order_id or "",
                    item.exchange_order_id,
                    item.symbol,
                    item.status.value,
                    item.filled_quantity,
                    max(Decimal("0"), (item.quantity or Decimal("0")) - item.filled_quantity),
                )
                for item in orders
            ],
            fills=[self._fill_record(item) for item in fills],
        )

    async def _internal_snapshot(self, account: models.ExchangeAccount) -> ReconciliationSnapshot:
        inventory_result = await self.session.execute(select(models.InventorySnapshot).where(models.InventorySnapshot.exchange_account_id == account.id))
        latest_balances: dict[str, models.InventorySnapshot] = {}
        for row in inventory_result.scalars().all():
            current = latest_balances.get(row.asset)
            if current is None or row.captured_at > current.captured_at:
                latest_balances[row.asset] = row
        order_result = await self.session.execute(select(models.Order).where(models.Order.exchange_account_id == account.id))
        trade_result = await self.session.execute(select(models.Trade).where(models.Trade.exchange_account_id == account.id))
        pair_result = await self.session.execute(select(models.TradingPair))
        pairs = {row.id: row.normalized_symbol for row in pair_result.scalars().all()}
        return ReconciliationSnapshot(
            balances=[BalanceRecord(row.asset, Decimal(str(row.total_balance)), Decimal(str(row.available_balance)), Decimal(str(row.reserved_balance))) for row in latest_balances.values()],
            orders=[
                OrderRecord(
                    row.client_order_id,
                    row.exchange_order_id,
                    pairs.get(row.trading_pair_id, str(row.trading_pair_id)),
                    row.status.value,
                    Decimal(str(row.filled_quantity)),
                    max(Decimal("0"), Decimal(str(row.quantity)) - Decimal(str(row.filled_quantity))),
                )
                for row in order_result.scalars().all()
            ],
            fills=[
                FillRecord(row.exchange_trade_id, str(row.order_id), pairs.get(row.trading_pair_id, str(row.trading_pair_id)), Decimal(str(row.quantity)), Decimal(str(row.price)), Decimal(str(row.fee_amount)))
                for row in trade_result.scalars().all()
            ],
        )

    def _fill_record(self, payload: dict) -> FillRecord:
        trade_id = str(payload.get("tradeId") or payload.get("trade_id") or payload.get("matchId") or payload.get("match_id") or "")
        order_id = str(payload.get("orderId") or payload.get("order_id") or payload.get("clientOrderId") or payload.get("clOrdId") or "")
        symbol = str(payload.get("symbol") or payload.get("currencyPair") or "")
        quantity = Decimal(str(payload.get("execQty") or payload.get("matchQty") or payload.get("quantity") or payload.get("filledQty") or 0))
        price = Decimal(str(payload.get("price") or payload.get("avgPrice") or payload.get("orderPrice") or 0))
        fee = Decimal(str(payload.get("fee") or 0))
        return FillRecord(trade_id, order_id, symbol, quantity, price, fee)
