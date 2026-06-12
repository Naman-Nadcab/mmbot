from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from mmbot.core.config import Settings
from mmbot.db import models
from mmbot.execution.client import PrivateRestExecutionClient
from mmbot.execution.coinstore_reconciliation import CoinstoreLiveReconciler, CoinstoreReconciliationReport
from mmbot.execution.coinstore_safety import CoinstoreExecutionSafety
from mmbot.execution.coinstore_validation import CoinstoreValidationLayer
from mmbot.execution.models import Balance, CancelIntent, ExecutionOrder, ExecutionOrderType, ExecutionSide, ExecutionVenue, NormalizedOrderStatus, OrderIntent, SymbolPrecision
from mmbot.execution.precision import validate_symbol_precision
from mmbot.execution.signing import ExecutionCredentials
from mmbot.execution.status import normalize_status
from mmbot.security.secrets import SecretCipher


class CoinstoreExecutionService:
    def __init__(self, settings: Settings, session: AsyncSession):
        self.settings = settings
        self.session = session
        self._client: PrivateRestExecutionClient | None = None
        self._account: models.ExchangeAccount | None = None
        self._precision: dict[str, SymbolPrecision] | None = None
        self.validation = CoinstoreValidationLayer()
        self.safety = CoinstoreExecutionSafety(session)

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()

    async def place_order(self, intent: OrderIntent) -> ExecutionOrder:
        if intent.venue is not ExecutionVenue.coinstore:
            raise ValueError("CoinstoreExecutionService only accepts coinstore intents")
        account = await self.account()
        precision = await self.precision_for_symbol(intent.symbol)
        client = await self.client()
        self.validation.verify_runtime_ready(client.credentials, precision, intent)
        await self.safety.assert_order_safe(account.id, intent)
        order = await client.place_order(intent, precision)
        self.validation.verify_order_response(order.raw)
        await self.persist_order(order, intent)
        return order

    async def cancel_order(self, intent: CancelIntent) -> ExecutionOrder:
        client = await self.client()
        order = await client.cancel_order(intent)
        self.validation.verify_order_response(order.raw)
        await self.persist_order(order, None)
        return order

    async def cancel_all_orders(self, symbol: str | None = None) -> list[ExecutionOrder]:
        client = await self.client()
        orders = await client.cancel_all_orders(symbol)
        for order in orders:
            self.validation.verify_order_response(order.raw)
            await self.persist_order(order, None)
        return orders

    async def get_order_status(self, intent: CancelIntent) -> ExecutionOrder:
        client = await self.client()
        order = await client.get_order_status(intent)
        self.validation.verify_order_response(order.raw)
        await self.persist_order(order, None)
        return order

    async def reconcile_live(self, symbols: list[str]) -> CoinstoreReconciliationReport:
        client = await self.client()
        account = await self.account()
        balances = await client.sync_balances()
        self.validation.verify_balance_response([balance.raw for balance in balances])
        orders: list[ExecutionOrder] = []
        fills: list[dict[str, Any]] = []
        for symbol in symbols:
            orders.extend(await client.sync_open_orders(symbol))
            symbol_fills = await client.sync_trade_fills(symbol)
            self.validation.verify_trade_response(symbol_fills)
            fills.extend(symbol_fills)
        for balance in balances:
            await self.persist_balance_update(balance)
        for order in orders:
            await self.persist_order(order, None)
        for fill in fills:
            await self.persist_fill_update(fill)
        safety_report = await self.safety.detect_stale_and_orphan_orders(account.id, [order.exchange_order_id or "" for order in orders])
        return await CoinstoreLiveReconciler(self.session).reconcile(account, balances, orders, fills, safety_report.stale_order_ids, safety_report.orphan_order_ids)

    async def handle_private_message(self, message: dict[str, Any]) -> dict[str, int]:
        counts = {"orders": 0, "trades": 0, "fills": 0, "balances": 0}
        for payload in self._private_payloads(message):
            if self._is_balance_payload(payload):
                await self.persist_balance_update(self._balance_from_payload(payload))
                counts["balances"] += 1
            if self._is_order_payload(payload):
                await self.persist_order(self._execution_order_from_payload(payload), None)
                counts["orders"] += 1
            if self._is_fill_payload(payload):
                await self.persist_fill_update(payload)
                counts["trades"] += 1
                counts["fills"] += 1
        return counts

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

    async def persist_balance_update(self, balance: Balance) -> models.InventorySnapshot:
        account = await self.account()
        row = models.InventorySnapshot(
            exchange_account_id=account.id,
            asset=balance.asset,
            total_balance=balance.total,
            available_balance=balance.available,
            reserved_balance=balance.reserved,
            valuation_asset="USDT",
            valuation_price=None,
            valuation_amount=None,
            captured_at=datetime.now(timezone.utc),
            metadata_json={"source": "coinstore_private_websocket", "raw": balance.raw},
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def persist_fill_update(self, payload: dict[str, Any]) -> models.Trade:
        account = await self.account()
        await self.safety.assert_fill_safe(str(account.id), payload)
        order = await self.persist_order(self._execution_order_from_payload(payload), None)
        trade_id = str(payload.get("tradeId") or payload.get("trade_id") or payload.get("matchId") or payload.get("match_id") or "")
        if not trade_id:
            raise RuntimeError("Coinstore fill update is missing tradeId/matchId")
        result = await self.session.execute(select(models.Trade).where(models.Trade.exchange_account_id == account.id, models.Trade.exchange_trade_id == trade_id))
        row = result.scalar_one_or_none()
        if row is not None:
            return row
        price = self._decimal(payload.get("price") or payload.get("avgPrice") or payload.get("orderPrice") or 0)
        quantity = self._decimal(payload.get("execQty") or payload.get("matchQty") or payload.get("quantity") or payload.get("filledQty") or 0)
        fee = self._decimal(payload.get("fee") or 0)
        side_raw = _side(payload.get("side")) or order.side
        side = models.OrderSide(side_raw.value if hasattr(side_raw, "value") else str(side_raw))
        traded_at = _timestamp(payload.get("matchTime") or payload.get("time") or payload.get("ts"))
        row = models.Trade(
            order_id=order.id,
            exchange_trade_id=trade_id,
            exchange_account_id=account.id,
            trading_pair_id=order.trading_pair_id,
            side=side,
            price=price,
            quantity=quantity,
            fee_amount=fee,
            fee_asset=str(payload.get("feeAsset") or payload.get("feeCurrency") or ""),
            traded_at=traded_at,
            metadata_json={"source": "coinstore_private_websocket", "raw": payload},
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def _trading_pair(self, symbol: str) -> models.TradingPair:
        normalized = symbol.upper().replace("-", "/").replace("_", "/")
        compact = normalized.replace("/", "")
        result = await self.session.execute(
            select(models.TradingPair).where(
                models.TradingPair.exchange_name == "coinstore",
                or_(models.TradingPair.normalized_symbol == normalized, models.TradingPair.venue_symbol == compact),
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
                or_(models.TradingPair.normalized_symbol == normalized, models.TradingPair.venue_symbol == compact),
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

    def _private_payloads(self, message: dict[str, Any]) -> list[dict[str, Any]]:
        candidates: list[Any] = [message]
        for key in ("body", "data", "result", "payload"):
            value = message.get(key)
            if value is not None:
                candidates.append(value)
        payloads: list[dict[str, Any]] = []
        while candidates:
            item = candidates.pop(0)
            if isinstance(item, list):
                candidates.extend(item)
            elif isinstance(item, dict):
                nested = item.get("data") or item.get("items") or item.get("orders") or item.get("matches") or item.get("balances")
                if isinstance(nested, list):
                    candidates.extend(nested)
                else:
                    payloads.append(item)
        return payloads

    def _is_balance_payload(self, payload: dict[str, Any]) -> bool:
        return _int(payload.get("messageType")) == 3002 or any(key in payload for key in ("totalBalance", "available", "availableBalance", "frozenInitMargin"))

    def _is_order_payload(self, payload: dict[str, Any]) -> bool:
        return _int(payload.get("messageType")) == 3004 or any(key in payload for key in ("orderId", "clientOrderId", "clOrdId", "orderStatus"))

    def _is_fill_payload(self, payload: dict[str, Any]) -> bool:
        return any(key in payload for key in ("tradeId", "trade_id", "matchId", "match_id")) and any(key in payload for key in ("execQty", "matchQty", "filledQty"))

    def _balance_from_payload(self, payload: dict[str, Any]) -> Balance:
        asset = str(payload.get("asset") or payload.get("currency") or payload.get("coin") or payload.get("currencyId") or "").upper()
        if not asset:
            raise RuntimeError("Coinstore balance update is missing asset/currency")
        available = self._decimal(payload.get("available") or payload.get("availableBalance") or 0)
        total = self._decimal(payload.get("totalBalance") or payload.get("total") or payload.get("balance") or available)
        reserved = self._decimal(payload.get("reserved") or payload.get("locked") or payload.get("frozenInitMargin") or max(Decimal("0"), total - available))
        return Balance(ExecutionVenue.coinstore, asset, total, available, reserved, payload)

    def _execution_order_from_payload(self, payload: dict[str, Any]) -> ExecutionOrder:
        symbol = self._symbol_from_payload(payload)
        side = _side(payload.get("side"))
        order_type = _order_type(payload.get("orderType") or payload.get("ordType") or payload.get("type"))
        quantity = self._decimal(payload.get("quantity") or payload.get("orderQty") or payload.get("ordQty") or payload.get("execQty") or payload.get("matchQty") or 0)
        filled = self._decimal(payload.get("matchQty") or payload.get("execQty") or payload.get("filledQty") or 0)
        price = self._decimal(payload.get("price") or payload.get("orderPrice") or payload.get("avgPrice") or 0)
        status = _coinstore_order_status(payload.get("orderStatus") or payload.get("status") or payload.get("state"))
        return ExecutionOrder(
            venue=ExecutionVenue.coinstore,
            symbol=symbol,
            client_order_id=str(payload.get("clientOrderId") or payload.get("clOrdId") or payload.get("client_order_id") or "") or None,
            exchange_order_id=str(payload.get("orderId") or payload.get("order_id") or payload.get("ordId") or "") or None,
            status=status,
            side=side,
            order_type=order_type,
            price=price if price > 0 else None,
            quantity=quantity,
            filled_quantity=filled,
            average_price=self._decimal(payload.get("avgPrice") or payload.get("averagePrice")) or None,
            fee=self._decimal(payload.get("fee") or 0),
            raw=payload,
        )

    def _symbol_from_payload(self, payload: dict[str, Any]) -> str:
        raw = payload.get("symbol") or payload.get("currencyPair") or payload.get("instrumentId") or payload.get("contractId")
        if raw is None:
            raise RuntimeError("Coinstore private update is missing symbol/instrument")
        value = str(raw).upper().replace("-", "/").replace("_", "/")
        if "/" in value:
            return value
        return value

    def _decimal(self, value: Any) -> Decimal:
        if value is None or value == "":
            return Decimal("0")
        return Decimal(str(value))


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


def _coinstore_order_status(value: Any) -> NormalizedOrderStatus:
    numeric = _int(value)
    if numeric is not None:
        return {
            0: NormalizedOrderStatus.new,
            1: NormalizedOrderStatus.open,
            2: NormalizedOrderStatus.open,
            3: NormalizedOrderStatus.partially_filled,
            4: NormalizedOrderStatus.filled,
            5: NormalizedOrderStatus.cancelled,
            6: NormalizedOrderStatus.cancelled,
            7: NormalizedOrderStatus.open,
            8: NormalizedOrderStatus.rejected,
            11: NormalizedOrderStatus.rejected,
            12: NormalizedOrderStatus.rejected,
        }.get(numeric, NormalizedOrderStatus.unknown)
    return normalize_status(value)


def _side(value: Any) -> models.OrderSide | ExecutionSide | None:
    normalized = str(value).lower()
    if normalized in {"1", "buy", "bid", "b"}:
        return ExecutionSide.buy
    if normalized in {"-1", "sell", "ask", "s"}:
        return ExecutionSide.sell
    return None


def _order_type(value: Any) -> ExecutionOrderType | None:
    normalized = str(value).lower()
    if normalized in {"1", "limit"}:
        return ExecutionOrderType.limit
    if normalized in {"3", "market"}:
        return ExecutionOrderType.market
    if normalized in {"post_only", "post-only"}:
        return ExecutionOrderType.post_only
    return None


def _timestamp(value: Any) -> datetime:
    if value is None or value == "":
        return datetime.now(timezone.utc)
    number = float(value)
    if number > 10_000_000_000:
        number /= 1000
    return datetime.fromtimestamp(number, tz=timezone.utc)


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
