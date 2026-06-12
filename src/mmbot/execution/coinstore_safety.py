from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mmbot.db import models
from mmbot.execution.models import ExecutionSide, OrderIntent


class CoinstoreSafetyError(RuntimeError):
    pass


@dataclass(frozen=True)
class CoinstoreSafetyReport:
    stale_order_ids: list[str]
    orphan_order_ids: list[str]


class CoinstoreExecutionSafety:
    def __init__(self, session: AsyncSession, stale_after_seconds: int = 300):
        self.session = session
        self.stale_after = timedelta(seconds=stale_after_seconds)

    async def assert_order_safe(self, account_id, intent: OrderIntent) -> None:
        await self._assert_duplicate_order(account_id, intent)
        await self._assert_no_self_trade(account_id, intent)

    async def assert_fill_safe(self, account_id: str, payload: dict) -> None:
        counterparty = payload.get("counterpartyAccountId") or payload.get("counterparty_account_id")
        if counterparty is not None and str(counterparty) == str(account_id):
            raise CoinstoreSafetyError("wash_trade_detected")
        if str(payload.get("selfTrade") or payload.get("self_trade") or "").lower() in {"true", "1", "yes"}:
            raise CoinstoreSafetyError("self_trade_fill_detected")

    async def detect_stale_and_orphan_orders(self, account_id, exchange_orders: list[str]) -> CoinstoreSafetyReport:
        cutoff = datetime.now(timezone.utc) - self.stale_after
        result = await self.session.execute(
            select(models.Order).where(
                models.Order.exchange_account_id == account_id,
                models.Order.status.in_([models.OrderStatus.open, models.OrderStatus.submitted, models.OrderStatus.partially_filled]),
            )
        )
        stale: list[str] = []
        orphan: list[str] = []
        exchange_ids = {str(item) for item in exchange_orders if item}
        for order in result.scalars().all():
            if _as_utc(order.updated_at or order.created_at) < cutoff:
                stale.append(order.client_order_id)
            if order.exchange_order_id and order.exchange_order_id not in exchange_ids:
                orphan.append(order.client_order_id)
        return CoinstoreSafetyReport(stale, orphan)

    async def _assert_duplicate_order(self, account_id, intent: OrderIntent) -> None:
        result = await self.session.execute(
            select(models.Order).where(
                models.Order.exchange_account_id == account_id,
                models.Order.client_order_id == intent.client_order_id,
                models.Order.status.in_([models.OrderStatus.open, models.OrderStatus.submitted, models.OrderStatus.partially_filled, models.OrderStatus.created]),
            )
        )
        if result.scalar_one_or_none() is not None:
            raise CoinstoreSafetyError(f"duplicate_client_order_id:{intent.client_order_id}")

    async def _assert_no_self_trade(self, account_id, intent: OrderIntent) -> None:
        if intent.price is None:
            return
        opposite = models.OrderSide.sell if intent.side is ExecutionSide.buy else models.OrderSide.buy
        result = await self.session.execute(
            select(models.Order).where(
                models.Order.exchange_account_id == account_id,
                models.Order.status.in_([models.OrderStatus.open, models.OrderStatus.submitted, models.OrderStatus.partially_filled]),
                models.Order.side == opposite,
            )
        )
        for order in result.scalars().all():
            if order.price is None:
                continue
            existing_price = Decimal(str(order.price))
            if intent.side is ExecutionSide.buy and intent.price >= existing_price:
                raise CoinstoreSafetyError(f"self_trade_prevention:{intent.client_order_id}:{order.client_order_id}")
            if intent.side is ExecutionSide.sell and intent.price <= existing_price:
                raise CoinstoreSafetyError(f"self_trade_prevention:{intent.client_order_id}:{order.client_order_id}")


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
