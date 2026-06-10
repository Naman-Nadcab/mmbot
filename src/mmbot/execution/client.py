from __future__ import annotations

import asyncio
import logging
from decimal import Decimal
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from mmbot.core.config import Settings
from mmbot.core.exceptions import ExchangeError, RateLimitExceededError
from mmbot.execution.errors import normalize_exchange_error, raise_normalized_error
from mmbot.execution.models import Balance, BulkExecutionResult, CancelIntent, ExecutionOrder, ExecutionOrderType, ExecutionSide, ExecutionVenue, OrderIntent, Position, ReplaceIntent, SymbolPrecision, TimeInForce
from mmbot.execution.precision import apply_symbol_precision, decimal_to_exchange
from mmbot.execution.signing import ExecutionCredentials, sign_request
from mmbot.execution.specs import EXECUTION_SPECS, VenueExecutionSpec
from mmbot.execution.status import normalize_status
from mmbot.exchanges.rate_limit import AsyncRateLimiter
from mmbot.exchanges.registry import get_exchange_definition

logger = logging.getLogger(__name__)


class PrivateRestExecutionClient:
    def __init__(self, venue: ExecutionVenue, settings: Settings):
        self.venue = venue
        self.spec: VenueExecutionSpec = EXECUTION_SPECS[venue]
        self.definition = get_exchange_definition(venue.value)
        self.credentials = ExecutionCredentials(
            api_key=settings.EXCHANGE_API_KEYS[venue.value],
            api_secret=settings.EXCHANGE_API_SECRETS[venue.value],
            passphrase=settings.EXCHANGE_API_PASSPHRASES.get(venue.value),
            memo=settings.EXCHANGE_API_MEMOS.get(venue.value),
        )
        self.rate_limiter = AsyncRateLimiter(self.definition.rate_limit)
        self.http = httpx.AsyncClient(base_url=self.definition.rest_base_url, timeout=settings.HTTP_TIMEOUT_SECONDS)

    async def close(self) -> None:
        await self.http.aclose()

    async def place_order(self, intent: OrderIntent, precision: SymbolPrecision) -> ExecutionOrder:
        precise = apply_symbol_precision(intent, precision)
        payload = self._order_payload(precise)
        data = await self._signed_request("POST", self.spec.place_order_path, params={} if self._body_signed() else payload, body=payload if self._body_signed() else None)
        return self._parse_order(data, precise)

    async def cancel_order(self, intent: CancelIntent) -> ExecutionOrder:
        path = self._format_path(self.spec.cancel_order_path, intent.exchange_order_id)
        params = self._cancel_payload(intent)
        data = await self._signed_request("DELETE", path, params=params, body=params if self._body_signed() else None)
        return self._parse_order(data, None)

    async def replace_order(self, intent: ReplaceIntent, precision: SymbolPrecision) -> ExecutionOrder:
        if self.spec.order_replace_path:
            replacement = apply_symbol_precision(intent.replacement, precision)
            payload = self._replace_payload(intent, replacement)
            data = await self._signed_request("POST", self.spec.order_replace_path, params=payload if not self._body_signed() else {}, body=payload if self._body_signed() else None)
            return self._parse_order(data, replacement)
        await self.cancel_order(intent.cancel)
        return await self.place_order(intent.replacement, precision)

    async def bulk_place_orders(self, intents: list[OrderIntent], precision_by_symbol: dict[str, SymbolPrecision]) -> BulkExecutionResult:
        accepted: list[ExecutionOrder] = []
        rejected: list[tuple[OrderIntent | CancelIntent, str]] = []
        for intent in intents:
            try:
                accepted.append(await self.place_order(intent, precision_by_symbol[intent.symbol]))
            except Exception as exc:
                rejected.append((intent, str(exc)))
        return BulkExecutionResult(accepted, rejected)

    async def bulk_cancel_orders(self, intents: list[CancelIntent]) -> BulkExecutionResult:
        if self.spec.bulk_cancel_path:
            payload = {"orders": [self._cancel_payload(intent) for intent in intents]}
            data = await self._signed_request("POST", self.spec.bulk_cancel_path, params={}, body=payload)
            orders = [self._parse_order(item, None) for item in self._extract_list(data)]
            return BulkExecutionResult(orders, [])
        accepted: list[ExecutionOrder] = []
        rejected: list[tuple[OrderIntent | CancelIntent, str]] = []
        for intent in intents:
            try:
                accepted.append(await self.cancel_order(intent))
            except Exception as exc:
                rejected.append((intent, str(exc)))
        return BulkExecutionResult(accepted, rejected)

    async def cancel_all_orders(self, symbol: str | None = None) -> list[ExecutionOrder]:
        params = {"symbol": self._venue_symbol(symbol)} if symbol else {}
        data = await self._signed_request("DELETE", self.spec.cancel_all_path, params=params, body=params if self._body_signed() else None)
        return [self._parse_order(item, None) for item in self._extract_list(data)]

    async def get_order_status(self, intent: CancelIntent) -> ExecutionOrder:
        path = self._format_path(self.spec.order_status_path, intent.exchange_order_id)
        data = await self._signed_request("GET", path, params=self._cancel_payload(intent), body=None)
        return self._parse_order(data, None)

    async def sync_balances(self) -> list[Balance]:
        data = await self._signed_request("GET", self.spec.account_balances_path, params={}, body=None)
        return self._parse_balances(data)

    async def sync_positions(self, mark_prices: dict[str, Decimal] | None = None) -> list[Position]:
        balances = await self.sync_balances()
        mark_prices = mark_prices or {}
        positions: list[Position] = []
        for balance in balances:
            price = mark_prices.get(balance.asset, Decimal("0"))
            positions.append(Position(self.venue, f"{balance.asset}/ACCOUNT", balance.asset, balance.total, balance.total * price, price if price else None, balance.raw))
        return positions

    async def discover_symbol_precision(self) -> dict[str, SymbolPrecision]:
        data = await self._signed_request("GET", self.spec.exchange_info_path, params={}, body=None, signed=False)
        return self._parse_symbol_precision(data)

    @retry(retry=retry_if_exception_type((ExchangeError, RateLimitExceededError, httpx.TransportError)), stop=stop_after_attempt(3), wait=wait_exponential(multiplier=0.2, max=3), reraise=True)
    async def _signed_request(self, method: str, path: str, params: dict[str, Any], body: dict[str, Any] | None, signed: bool = True) -> Any:
        await self.rate_limiter.acquire()
        headers: dict[str, str] = {}
        request_params = {k: v for k, v in params.items() if v is not None}
        request_body = body
        if signed:
            signed_request = sign_request(self.spec.signing_style, method, path, request_params, request_body, self.credentials)
            path, request_params, request_body, headers = signed_request.path, signed_request.params, signed_request.body, signed_request.headers
        response = await self.http.request(method, path, params=request_params if method.upper() in {"GET", "DELETE"} or not request_body else None, json=request_body, headers=headers)
        payload: Any
        try:
            payload = response.json() if response.content else {}
        except ValueError:
            payload = response.text
        if response.status_code >= 400:
            context = normalize_exchange_error(self.venue, response.status_code, payload)
            logger.warning("exchange_execution_error", extra={"venue": self.venue.value, "code": context.code, "retryable": context.retryable})
            raise_normalized_error(context)
        return payload

    def _order_payload(self, intent: OrderIntent) -> dict[str, Any]:
        symbol = self._venue_symbol(intent.symbol)
        if self.venue in {ExecutionVenue.binance, ExecutionVenue.mexc}:
            payload = {"symbol": symbol, "side": intent.side.value.upper(), "type": self._venue_order_type(intent), "quantity": decimal_to_exchange(intent.quantity), "newClientOrderId": intent.client_order_id}
            if intent.price is not None:
                payload["price"] = decimal_to_exchange(intent.price)
            if intent.time_in_force is not TimeInForce.post_only and intent.order_type is ExecutionOrderType.limit:
                payload["timeInForce"] = intent.time_in_force.value
            return payload
        if self.venue is ExecutionVenue.gate:
            return {"currency_pair": symbol, "side": intent.side.value, "type": intent.order_type.value, "amount": decimal_to_exchange(intent.quantity), "price": decimal_to_exchange(intent.price), "text": intent.client_order_id, "time_in_force": intent.time_in_force.value.lower()}
        if self.venue is ExecutionVenue.kucoin:
            return {"clientOid": intent.client_order_id, "symbol": symbol, "side": intent.side.value, "type": intent.order_type.value, "size": decimal_to_exchange(intent.quantity), "price": decimal_to_exchange(intent.price), "timeInForce": intent.time_in_force.value}
        if self.venue is ExecutionVenue.bitmart:
            return {"client_order_id": intent.client_order_id, "symbol": symbol, "side": intent.side.value, "type": intent.order_type.value, "size": decimal_to_exchange(intent.quantity), "price": decimal_to_exchange(intent.price)}
        return {"clientOrderId": intent.client_order_id, "symbol": symbol, "side": intent.side.value.upper(), "ordType": intent.order_type.value, "ordQty": decimal_to_exchange(intent.quantity), "ordPrice": decimal_to_exchange(intent.price)}

    def _replace_payload(self, intent: ReplaceIntent, replacement: OrderIntent) -> dict[str, Any]:
        payload = self._order_payload(replacement)
        if self.venue is ExecutionVenue.binance:
            payload.update({"cancelReplaceMode": "STOP_ON_FAILURE", "cancelOrigClientOrderId": intent.cancel.client_order_id, "cancelOrderId": intent.cancel.exchange_order_id})
        return payload

    def _cancel_payload(self, intent: CancelIntent) -> dict[str, Any]:
        symbol = self._venue_symbol(intent.symbol)
        if self.venue in {ExecutionVenue.binance, ExecutionVenue.mexc}:
            return {"symbol": symbol, "orderId": intent.exchange_order_id, "origClientOrderId": intent.client_order_id}
        if self.venue is ExecutionVenue.gate:
            return {"currency_pair": symbol}
        if self.venue is ExecutionVenue.kucoin:
            return {"symbol": symbol, "clientOid": intent.client_order_id}
        if self.venue is ExecutionVenue.bitmart:
            return {"symbol": symbol, "order_id": intent.exchange_order_id, "client_order_id": intent.client_order_id}
        return {"symbol": symbol, "ordId": intent.exchange_order_id, "clientOrderId": intent.client_order_id}

    def _parse_order(self, data: Any, intent: OrderIntent | None) -> ExecutionOrder:
        raw = self._unwrap(data)
        side_value = raw.get("side") or (intent.side.value if intent else None)
        type_value = raw.get("type") or raw.get("ordType") or (intent.order_type.value if intent else None)
        return ExecutionOrder(
            venue=self.venue,
            symbol=str(raw.get("symbol") or raw.get("currency_pair") or (intent.symbol if intent else "")),
            client_order_id=str(raw.get("clientOrderId") or raw.get("client_order_id") or raw.get("clientOid") or raw.get("text") or (intent.client_order_id if intent else "")) or None,
            exchange_order_id=str(raw.get("orderId") or raw.get("order_id") or raw.get("id") or raw.get("ordId") or "") or None,
            status=normalize_status(raw.get("status") or raw.get("state")),
            side=ExecutionSide(str(side_value).lower()) if side_value and str(side_value).lower() in {"buy", "sell"} else None,
            order_type=ExecutionOrderType(str(type_value).lower()) if type_value and str(type_value).lower() in {item.value for item in ExecutionOrderType} else None,
            price=self._decimal(raw.get("price") or (intent.price if intent else None)),
            quantity=self._decimal(raw.get("origQty") or raw.get("size") or raw.get("amount") or raw.get("ordQty") or (intent.quantity if intent else None)),
            filled_quantity=self._decimal(raw.get("executedQty") or raw.get("filled_size") or raw.get("filled_amount") or raw.get("cumExecQty") or 0) or Decimal("0"),
            average_price=self._decimal(raw.get("avgPrice") or raw.get("deal_avg_price") or raw.get("average_price")),
            fee=self._decimal(raw.get("fee")),
            raw=raw,
        )

    def _parse_balances(self, data: Any) -> list[Balance]:
        payload = self._unwrap(data)
        rows = payload.get("balances") or payload.get("data") or payload.get("accounts") or payload if isinstance(payload, list) else []
        balances: list[Balance] = []
        for row in rows:
            asset = row.get("asset") or row.get("currency") or row.get("coinName") or row.get("coin")
            if not asset:
                continue
            free = self._decimal(row.get("free") or row.get("available") or row.get("available_balance") or row.get("availableBalance") or 0) or Decimal("0")
            locked = self._decimal(row.get("locked") or row.get("hold") or row.get("frozen") or row.get("reserved") or 0) or Decimal("0")
            total = self._decimal(row.get("total") or row.get("balance") or row.get("totalBalance")) or free + locked
            balances.append(Balance(self.venue, str(asset), total, free, locked, row))
        return balances

    def _parse_symbol_precision(self, data: Any) -> dict[str, SymbolPrecision]:
        payload = self._unwrap(data)
        rows = payload.get("symbols") or payload.get("data") or payload.get("currency_pairs") or payload if isinstance(payload, list) else []
        result: dict[str, SymbolPrecision] = {}
        for row in rows:
            symbol = str(row.get("symbol") or row.get("id") or row.get("currency_pair") or row.get("symbolName") or "")
            if not symbol:
                continue
            price_tick = self._decimal(row.get("tickSize") or row.get("priceIncrement") or row.get("price_tick") or row.get("price_min_precision") or "0.00000001") or Decimal("0.00000001")
            qty_step = self._decimal(row.get("stepSize") or row.get("baseIncrement") or row.get("amount_precision") or row.get("quantity_min_precision") or "0.00000001") or Decimal("0.00000001")
            min_qty = self._decimal(row.get("minQty") or row.get("baseMinSize") or row.get("min_base_amount") or row.get("min_amount") or "0") or Decimal("0")
            min_notional = self._decimal(row.get("minNotional") or row.get("quoteMinSize") or row.get("min_quote_amount") or row.get("min_buy_amount") or "0") or Decimal("0")
            normalized = symbol.replace("_", "/").replace("-", "/")
            result[normalized] = SymbolPrecision(normalized, symbol, price_tick, qty_step, min_qty, min_notional, abs(price_tick.as_tuple().exponent), abs(qty_step.as_tuple().exponent))
        return result

    def _venue_order_type(self, intent: OrderIntent) -> str:
        if intent.order_type is ExecutionOrderType.post_only:
            return "LIMIT_MAKER"
        return intent.order_type.value.upper()

    def _venue_symbol(self, symbol: str | None) -> str:
        if symbol is None:
            return ""
        return symbol.replace("/", self.spec.symbol_separator).upper()

    def _format_path(self, path: str, order_id: str | None) -> str:
        return path.replace("{order_id}", order_id or "")

    def _body_signed(self) -> bool:
        return self.venue in {ExecutionVenue.gate, ExecutionVenue.kucoin, ExecutionVenue.bitmart, ExecutionVenue.coinstore}

    def _unwrap(self, data: Any) -> dict[str, Any] | list[Any]:
        if isinstance(data, dict):
            nested = data.get("data")
            if isinstance(nested, dict):
                return nested
            return data
        return data if isinstance(data, list) else {"value": data}

    def _extract_list(self, data: Any) -> list[Any]:
        payload = self._unwrap(data)
        if isinstance(payload, list):
            return payload
        for key in ("orders", "data", "result"):
            if isinstance(payload.get(key), list):
                return payload[key]
        return [payload]

    def _decimal(self, value: Any) -> Decimal | None:
        if value is None or value == "":
            return None
        return Decimal(str(value))


class ExecutionClientFactory:
    def __init__(self, settings: Settings):
        self.settings = settings

    def create(self, venue: ExecutionVenue | str) -> PrivateRestExecutionClient:
        normalized = venue if isinstance(venue, ExecutionVenue) else ExecutionVenue(str(venue).lower())
        return PrivateRestExecutionClient(normalized, self.settings)

    async def close_many(self, clients: list[PrivateRestExecutionClient]) -> None:
        await asyncio.gather(*(client.close() for client in clients), return_exceptions=True)
