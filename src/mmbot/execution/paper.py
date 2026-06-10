from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mmbot.db import models
from mmbot.execution.models import ExecutionOrderType, ExecutionSide, OrderIntent
from mmbot.exchanges.types import OrderBookSnapshot
from mmbot.reconciliation.engine import BalanceRecord, FillRecord, OrderRecord, PnlRecord, PositionRecord, ReconciliationSnapshot


@dataclass
class PaperOrder:
    intent: OrderIntent
    status: str = "NEW"
    exchange_order_id: str = field(default_factory=lambda: f"paper-{uuid.uuid4().hex}")
    filled_quantity: Decimal = Decimal("0")
    average_price: Decimal | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def remaining_quantity(self) -> Decimal:
        return max(Decimal("0"), self.intent.quantity - self.filled_quantity)


@dataclass(frozen=True)
class PaperFill:
    trade_id: str
    client_order_id: str
    symbol: str
    side: ExecutionSide
    price: Decimal
    quantity: Decimal
    fee: Decimal
    executed_at: datetime


@dataclass
class PaperAccountState:
    cash: Decimal
    base_asset: str
    quote_asset: str
    balances: dict[str, Decimal] = field(default_factory=dict)
    realized_pnl: Decimal = Decimal("0")
    fees: Decimal = Decimal("0")


class PaperExecutionEngine:
    def __init__(self, session: AsyncSession, starting_cash: Decimal, base_asset: str, quote_asset: str, fee_bps: Decimal = Decimal("1")):
        self.session = session
        self.account = PaperAccountState(starting_cash, base_asset, quote_asset, {quote_asset: starting_cash, base_asset: Decimal("0")})
        self.fee_bps = fee_bps
        self.open_orders: dict[str, PaperOrder] = {}
        self.fills: list[PaperFill] = []
        self.last_mark_price: Decimal = Decimal("0")
        self._exchange_account_id: uuid.UUID | None = None
        self._trading_pair_ids: dict[str, uuid.UUID] = {}

    async def place_order(self, intent: OrderIntent, orderbook: OrderBookSnapshot | None = None) -> PaperOrder:
        order = PaperOrder(intent=intent, status="ACKNOWLEDGED")
        self.open_orders[intent.client_order_id] = order
        await self._persist_order(order)
        if orderbook is not None:
            await self.simulate_fills(intent.symbol, orderbook)
        return order

    async def cancel_order(self, client_order_id: str) -> PaperOrder | None:
        order = self.open_orders.get(client_order_id)
        if order is None:
            return None
        order.status = "CANCELLED"
        order.updated_at = datetime.now(timezone.utc)
        self.open_orders.pop(client_order_id, None)
        await self._persist_order(order)
        return order

    async def replace_order(self, client_order_id: str, replacement: OrderIntent, orderbook: OrderBookSnapshot | None = None) -> PaperOrder:
        await self.cancel_order(client_order_id)
        return await self.place_order(replacement, orderbook)

    async def simulate_fills(self, symbol: str, orderbook: OrderBookSnapshot) -> list[PaperFill]:
        fills: list[PaperFill] = []
        if orderbook.bids and orderbook.asks:
            self.last_mark_price = Decimal(str((max(level.price for level in orderbook.bids) + min(level.price for level in orderbook.asks)) / 2))
        for order in list(self.open_orders.values()):
            if order.intent.symbol != symbol or order.remaining_quantity <= 0:
                continue
            fill_price, fill_qty = self._match_order(order, orderbook)
            if fill_qty <= 0 or fill_price is None:
                continue
            fill = await self._apply_fill(order, fill_price, fill_qty)
            fills.append(fill)
            if order.remaining_quantity <= 0:
                order.status = "FILLED"
                self.open_orders.pop(order.intent.client_order_id, None)
            else:
                order.status = "PARTIAL_FILL"
            order.updated_at = datetime.now(timezone.utc)
            await self._persist_order(order)
        return fills

    def balances(self) -> list[BalanceRecord]:
        records = []
        for asset, total in self.account.balances.items():
            reserved = Decimal("0")
            records.append(BalanceRecord(asset, total, total - reserved, reserved))
        return records

    def positions(self) -> list[PositionRecord]:
        quantity = self.account.balances.get(self.account.base_asset, Decimal("0"))
        notional = quantity * self.last_mark_price
        return [PositionRecord(f"{self.account.base_asset}/{self.account.quote_asset}", self.account.base_asset, quantity, notional)]

    def orders(self) -> list[OrderRecord]:
        return [
            OrderRecord(
                order.intent.client_order_id,
                order.exchange_order_id,
                order.intent.symbol,
                order.status,
                order.filled_quantity,
                order.remaining_quantity,
            )
            for order in self.open_orders.values()
        ]

    def reconciliation_snapshot(self) -> ReconciliationSnapshot:
        return ReconciliationSnapshot(
            balances=self.balances(),
            positions=self.positions(),
            orders=self.orders(),
            fills=[FillRecord(fill.trade_id, fill.client_order_id, fill.symbol, fill.quantity, fill.price, fill.fee) for fill in self.fills],
            pnl=PnlRecord(self.account.realized_pnl, self.unrealized_pnl(), self.account.fees),
        )

    def unrealized_pnl(self) -> Decimal:
        return self.account.balances.get(self.account.base_asset, Decimal("0")) * self.last_mark_price

    def exposure_notional(self) -> Decimal:
        return abs(self.account.balances.get(self.account.base_asset, Decimal("0")) * self.last_mark_price)

    def _match_order(self, order: PaperOrder, orderbook: OrderBookSnapshot) -> tuple[Decimal | None, Decimal]:
        if order.intent.price is None or order.intent.order_type not in {ExecutionOrderType.limit, ExecutionOrderType.post_only}:
            return None, Decimal("0")
        if order.intent.side is ExecutionSide.buy:
            asks = sorted(orderbook.asks, key=lambda level: level.price)
            fillable = [level for level in asks if Decimal(str(level.price)) <= order.intent.price]
        else:
            bids = sorted(orderbook.bids, key=lambda level: level.price, reverse=True)
            fillable = [level for level in bids if Decimal(str(level.price)) >= order.intent.price]
        if not fillable:
            return None, Decimal("0")
        level = fillable[0]
        quantity = min(order.remaining_quantity, Decimal(str(level.size)))
        return Decimal(str(level.price)), quantity

    async def _apply_fill(self, order: PaperOrder, price: Decimal, quantity: Decimal) -> PaperFill:
        notional = price * quantity
        fee = notional * self.fee_bps / Decimal("10000")
        base = self.account.base_asset
        quote = self.account.quote_asset
        if order.intent.side is ExecutionSide.buy:
            self.account.balances[base] = self.account.balances.get(base, Decimal("0")) + quantity
            self.account.balances[quote] = self.account.balances.get(quote, Decimal("0")) - notional - fee
        else:
            self.account.balances[base] = self.account.balances.get(base, Decimal("0")) - quantity
            self.account.balances[quote] = self.account.balances.get(quote, Decimal("0")) + notional - fee
            self.account.realized_pnl += notional - fee
        self.account.fees += fee
        previous_filled = order.filled_quantity
        order.filled_quantity += quantity
        order.average_price = price if previous_filled == 0 else ((order.average_price or price) * previous_filled + price * quantity) / order.filled_quantity
        fill = PaperFill(f"paper-trade-{uuid.uuid4().hex}", order.intent.client_order_id, order.intent.symbol, order.intent.side, price, quantity, fee, datetime.now(timezone.utc))
        self.fills.append(fill)
        await self._persist_trade(order, fill)
        await self._persist_position(order.intent.symbol)
        return fill

    async def _ensure_exchange_account(self) -> uuid.UUID:
        if self._exchange_account_id is not None:
            return self._exchange_account_id
        result = await self.session.execute(select(models.ExchangeAccount).where(models.ExchangeAccount.exchange_name == "paper", models.ExchangeAccount.account_alias == "paper", models.ExchangeAccount.environment == "sandbox"))
        row = result.scalar_one_or_none()
        if row is None:
            row = models.ExchangeAccount(exchange_name="paper", account_alias="paper", environment="sandbox", api_key_ciphertext=b"paper", api_secret_ciphertext=b"paper", encryption_key_id="paper", permissions=[], is_enabled=True)
            self.session.add(row)
            await self.session.flush()
        self._exchange_account_id = row.id
        return row.id

    async def _ensure_trading_pair(self, symbol: str) -> uuid.UUID:
        if symbol in self._trading_pair_ids:
            return self._trading_pair_ids[symbol]
        venue_symbol = symbol.replace("/", "")
        result = await self.session.execute(select(models.TradingPair).where(models.TradingPair.exchange_name == "paper", models.TradingPair.normalized_symbol == symbol))
        row = result.scalar_one_or_none()
        if row is None:
            base, quote = symbol.split("/", 1)
            row = models.TradingPair(exchange_name="paper", base_asset=base, quote_asset=quote, normalized_symbol=symbol, venue_symbol=venue_symbol, price_precision=8, quantity_precision=8, min_order_size=Decimal("0.00000001"), min_notional=Decimal("0"), tick_size=Decimal("0.00000001"), lot_size=Decimal("0.00000001"), is_enabled=True)
            self.session.add(row)
            await self.session.flush()
        self._trading_pair_ids[symbol] = row.id
        return row.id

    async def _persist_order(self, order: PaperOrder) -> None:
        account_id = await self._ensure_exchange_account()
        pair_id = await self._ensure_trading_pair(order.intent.symbol)
        result = await self.session.execute(select(models.Order).where(models.Order.client_order_id == order.intent.client_order_id))
        row = result.scalar_one_or_none()
        status = {
            "NEW": models.OrderStatus.created,
            "ACKNOWLEDGED": models.OrderStatus.open,
            "PARTIAL_FILL": models.OrderStatus.partially_filled,
            "FILLED": models.OrderStatus.filled,
            "CANCELLED": models.OrderStatus.cancelled,
            "REPLACED": models.OrderStatus.cancelled,
        }[order.status]
        if row is None:
            row = models.Order(client_order_id=order.intent.client_order_id, exchange_order_id=order.exchange_order_id, exchange_account_id=account_id, trading_pair_id=pair_id, side=models.OrderSide(order.intent.side.value), order_type=models.OrderType.limit, status=status, price=order.intent.price, quantity=order.intent.quantity, filled_quantity=order.filled_quantity, average_fill_price=order.average_price, metadata_json={"mode": "paper"})
            self.session.add(row)
        else:
            row.status = status
            row.filled_quantity = order.filled_quantity
            row.average_fill_price = order.average_price
        await self.session.flush()

    async def _persist_trade(self, order: PaperOrder, fill: PaperFill) -> None:
        account_id = await self._ensure_exchange_account()
        pair_id = await self._ensure_trading_pair(fill.symbol)
        order_result = await self.session.execute(select(models.Order).where(models.Order.client_order_id == order.intent.client_order_id))
        order_row = order_result.scalar_one()
        self.session.add(models.Trade(order_id=order_row.id, exchange_trade_id=fill.trade_id, exchange_account_id=account_id, trading_pair_id=pair_id, side=models.OrderSide(fill.side.value), price=fill.price, quantity=fill.quantity, fee_amount=fill.fee, fee_asset=self.account.quote_asset, traded_at=fill.executed_at, metadata_json={"mode": "paper"}))
        await self.session.flush()

    async def _persist_position(self, symbol: str) -> None:
        account_id = await self._ensure_exchange_account()
        pair_id = await self._ensure_trading_pair(symbol)
        base_quantity = self.account.balances.get(self.account.base_asset, Decimal("0"))
        result = await self.session.execute(select(models.Position).where(models.Position.exchange_account_id == account_id, models.Position.trading_pair_id == pair_id, models.Position.asset == self.account.base_asset))
        row = result.scalar_one_or_none()
        side = models.PositionSide.flat if base_quantity == 0 else models.PositionSide.long if base_quantity > 0 else models.PositionSide.short
        if row is None:
            row = models.Position(exchange_account_id=account_id, trading_pair_id=pair_id, asset=self.account.base_asset, side=side, quantity=abs(base_quantity), average_entry_price=self.last_mark_price if self.last_mark_price else None, realized_pnl=self.account.realized_pnl, unrealized_pnl=self.unrealized_pnl(), mark_price=self.last_mark_price if self.last_mark_price else None)
            self.session.add(row)
        else:
            row.side = side
            row.quantity = abs(base_quantity)
            row.realized_pnl = self.account.realized_pnl
            row.unrealized_pnl = self.unrealized_pnl()
            row.mark_price = self.last_mark_price if self.last_mark_price else None
        await self.session.flush()
