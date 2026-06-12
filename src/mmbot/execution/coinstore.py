from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mmbot.core.config import Settings
from mmbot.db import models
from mmbot.execution.client import PrivateRestExecutionClient
from mmbot.execution.models import CancelIntent, ExecutionOrder, ExecutionVenue, NormalizedOrderStatus, OrderIntent, SymbolPrecision
from mmbot.execution.precision import validate_symbol_precision
from mmbot.execution.signing import ExecutionCredentials
from mmbot.security.secrets import SecretCipher


class CoinstoreExecutionService:
    def __init__(self, settings: Settings, session: AsyncSession):
        self.settings = settings
        self.session = session
        self._client: PrivateRestExecutionClient | None = None
        self._account: models.ExchangeAccount | None = None
        self._precision: dict[str, SymbolPrecision] | None = None

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()

    async def place_order(self, intent: OrderIntent) -> ExecutionOrder:
        if intent.venue is not ExecutionVenue.coinstore:
            raise ValueError("CoinstoreExecutionService only accepts coinstore intents")
        precision = await self.precision_for_symbol(intent.symbol)
        validate_symbol_precision(intent, precision)
        client = await self.client()
        order = await client.place_order(intent, precision)
        await self.persist_order(order, intent)
        return order

    async def cancel_order(self, intent: CancelIntent) -> ExecutionOrder:
        client = await self.client()
        order = await client.cancel_order(intent)
        await self.persist_order(order, None)
        return order

    async def cancel_all_orders(self, symbol: str | None = None) -> list[ExecutionOrder]:
        client = await self.client()
        orders = await client.cancel_all_orders(symbol)
        for order in orders:
            await self.persist_order(order, None)
        return orders

    async def get_order_status(self, intent: CancelIntent) -> ExecutionOrder:
        client = await self.client()
        order = await client.get_order_status(intent)
        await self.persist_order(order, None)
        return order

    async def client(self) -> PrivateRestExecutionClient:
        if self._client is not None:
            return self._client
        account = await self.account()
        cipher = SecretCipher(self.settings)
        api_key = cipher.decrypt(account.api_key_ciphertext)
        api_secret = cipher.decrypt(account.api_secret_ciphertext)
        if not api_key or not api_secret:
            raise RuntimeError("enabled Coinstore account is missing API credentials")
        credentials = ExecutionCredentials(api_key=api_key, api_secret=api_secret, passphrase=cipher.decrypt(account.passphrase_ciphertext))
        self._client = PrivateRestExecutionClient(ExecutionVenue.coinstore, self.settings, credentials)
        return self._client

    async def account(self) -> models.ExchangeAccount:
        if self._account is not None:
            return self._account
        result = await self.session.execute(
            select(models.ExchangeAccount)
            .where(models.ExchangeAccount.exchange_name == "coinstore", models.ExchangeAccount.is_enabled.is_(True))
            .order_by(models.ExchangeAccount.updated_at.desc())
            .limit(1)
        )
        account = result.scalar_one_or_none()
        if account is None:
            raise RuntimeError("no enabled Coinstore exchange account")
        self._account = account
        return account

    async def precision_for_symbol(self, symbol: str) -> SymbolPrecision:
        precision = await self.precision()
        normalized = symbol.upper().replace("-", "/").replace("_", "/")
        if normalized in precision:
            return precision[normalized]
        compact = normalized.replace("/", "")
        for item in precision.values():
            if item.venue_symbol.upper() == compact or item.symbol.upper().replace("/", "") == compact:
                return item
        row = await self._trading_pair(symbol)
        if row.tick_size is None or row.lot_size is None:
            raise RuntimeError(f"Coinstore precision unavailable for {symbol}")
        return SymbolPrecision(
            symbol=row.normalized_symbol,
            venue_symbol=row.venue_symbol,
            price_tick=Decimal(str(row.tick_size)),
            quantity_step=Decimal(str(row.lot_size)),
            min_quantity=Decimal(str(row.min_order_size or 0)),
            min_notional=Decimal(str(row.min_notional or 0)),
            price_precision=row.price_precision,
            quantity_precision=row.quantity_precision,
        )

    async def precision(self) -> dict[str, SymbolPrecision]:
        if self._precision is not None:
            return self._precision
        client = await self.client()
        self._precision = await client.discover_symbol_precision()
        return self._precision

    async def persist_order(self, order: ExecutionOrder, intent: OrderIntent | None) -> models.Order:
        account = await self.account()
        symbol = order.symbol or (intent.symbol if intent else "")
        pair = await self._ensure_trading_pair(symbol)
        client_order_id = order.client_order_id or (intent.client_order_id if intent else None)
        if not client_order_id:
            client_order_id = f"coinstore-{order.exchange_order_id or uuid.uuid4().hex}"
        result = await self.session.execute(select(models.Order).where(models.Order.client_order_id == client_order_id))
        row = result.scalar_one_or_none()
        status = _order_status(order.status)
        side = order.side or (intent.side if intent else None)
        order_type = order.order_type or (intent.order_type if intent else None)
        price = order.price if order.price is not None else (intent.price if intent else None)
        quantity = order.quantity if order.quantity is not None else (intent.quantity if intent else Decimal("0"))
        if row is None:
            row = models.Order(
                client_order_id=client_order_id,
                exchange_order_id=order.exchange_order_id,
                exchange_account_id=account.id,
                trading_pair_id=pair.id,
                side=models.OrderSide(side.value if side else "buy"),
                order_type=models.OrderType(order_type.value if order_type else "limit"),
                status=status,
                price=price,
                quantity=quantity,
                filled_quantity=order.filled_quantity,
                average_fill_price=order.average_price,
                fee_amount=order.fee or Decimal("0"),
                metadata_json={"mode": "live", "venue": "coinstore", "raw": order.raw},
            )
            self.session.add(row)
        else:
            row.exchange_order_id = order.exchange_order_id or row.exchange_order_id
            row.status = status
            row.filled_quantity = order.filled_quantity
            row.average_fill_price = order.average_price
            row.fee_amount = order.fee or row.fee_amount
            row.metadata_json = {**(row.metadata_json or {}), "raw": order.raw, "venue": "coinstore"}
        await self.session.flush()
        return row

    async def _trading_pair(self, symbol: str) -> models.TradingPair:
        normalized = symbol.upper().replace("-", "/").replace("_", "/")
        compact = normalized.replace("/", "")
        result = await self.session.execute(
            select(models.TradingPair).where(
                models.TradingPair.exchange_name == "coinstore",
                (models.TradingPair.normalized_symbol == normalized) | (models.TradingPair.venue_symbol == compact),
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            raise RuntimeError(f"Coinstore trading pair {symbol} is not configured")
        return row

    async def _ensure_trading_pair(self, symbol: str) -> models.TradingPair:
        normalized = symbol.upper().replace("-", "/").replace("_", "/")
        compact = normalized.replace("/", "")
        result = await self.session.execute(
            select(models.TradingPair).where(
                models.TradingPair.exchange_name == "coinstore",
                (models.TradingPair.normalized_symbol == normalized) | (models.TradingPair.venue_symbol == compact),
            )
        )
        row = result.scalar_one_or_none()
        if row is not None:
            return row
        if "/" not in normalized:
            raise RuntimeError(f"Coinstore order symbol {symbol} is not normalized")
        base, quote = normalized.split("/", 1)
        precision = await self.precision_for_symbol(normalized)
        row = models.TradingPair(
            exchange_name="coinstore",
            base_asset=base,
            quote_asset=quote,
            normalized_symbol=normalized,
            venue_symbol=precision.venue_symbol,
            price_precision=precision.price_precision,
            quantity_precision=precision.quantity_precision,
            min_order_size=precision.min_quantity,
            min_notional=precision.min_notional,
            tick_size=precision.price_tick,
            lot_size=precision.quantity_step,
            is_enabled=True,
        )
        self.session.add(row)
        await self.session.flush()
        return row


def _order_status(status: NormalizedOrderStatus) -> models.OrderStatus:
    return {
        NormalizedOrderStatus.new: models.OrderStatus.created,
        NormalizedOrderStatus.open: models.OrderStatus.open,
        NormalizedOrderStatus.partially_filled: models.OrderStatus.partially_filled,
        NormalizedOrderStatus.filled: models.OrderStatus.filled,
        NormalizedOrderStatus.cancelled: models.OrderStatus.cancelled,
        NormalizedOrderStatus.rejected: models.OrderStatus.rejected,
        NormalizedOrderStatus.expired: models.OrderStatus.expired,
        NormalizedOrderStatus.unknown: models.OrderStatus.submitted,
    }[status]
