from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any


class ExecutionVenue(str, Enum):
    coinstore = "coinstore"
    mexc = "mexc"
    gate = "gate"
    bitmart = "bitmart"
    kucoin = "kucoin"
    binance = "binance"


class ExecutionSide(str, Enum):
    buy = "buy"
    sell = "sell"


class ExecutionOrderType(str, Enum):
    limit = "limit"
    market = "market"
    post_only = "post_only"
    ioc = "ioc"
    fok = "fok"


class TimeInForce(str, Enum):
    gtc = "GTC"
    ioc = "IOC"
    fok = "FOK"
    post_only = "POST_ONLY"


class NormalizedOrderStatus(str, Enum):
    new = "new"
    open = "open"
    partially_filled = "partially_filled"
    filled = "filled"
    cancelled = "cancelled"
    rejected = "rejected"
    expired = "expired"
    unknown = "unknown"


@dataclass(frozen=True)
class SymbolPrecision:
    symbol: str
    venue_symbol: str
    price_tick: Decimal
    quantity_step: Decimal
    min_quantity: Decimal
    min_notional: Decimal
    price_precision: int
    quantity_precision: int


@dataclass(frozen=True)
class OrderIntent:
    venue: ExecutionVenue
    symbol: str
    side: ExecutionSide
    order_type: ExecutionOrderType
    quantity: Decimal
    price: Decimal | None
    client_order_id: str
    time_in_force: TimeInForce = TimeInForce.gtc
    reduce_only: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CancelIntent:
    venue: ExecutionVenue
    symbol: str
    client_order_id: str | None = None
    exchange_order_id: str | None = None


@dataclass(frozen=True)
class ReplaceIntent:
    cancel: CancelIntent
    replacement: OrderIntent


@dataclass(frozen=True)
class ExecutionOrder:
    venue: ExecutionVenue
    symbol: str
    client_order_id: str | None
    exchange_order_id: str | None
    status: NormalizedOrderStatus
    side: ExecutionSide | None
    order_type: ExecutionOrderType | None
    price: Decimal | None
    quantity: Decimal | None
    filled_quantity: Decimal
    average_price: Decimal | None
    fee: Decimal | None
    raw: dict[str, Any]
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class Balance:
    venue: ExecutionVenue
    asset: str
    total: Decimal
    available: Decimal
    reserved: Decimal
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Position:
    venue: ExecutionVenue
    symbol: str
    asset: str
    quantity: Decimal
    notional: Decimal
    mark_price: Decimal | None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BulkExecutionResult:
    accepted: list[ExecutionOrder]
    rejected: list[tuple[OrderIntent | CancelIntent, str]]


@dataclass(frozen=True)
class ExecutionErrorContext:
    venue: ExecutionVenue
    code: str
    message: str
    retryable: bool
    raw: dict[str, Any] | str | None = None
