from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from mmbot.execution.models import ExecutionVenue


class SigningStyle(str, Enum):
    binance_query = "binance_query"
    mexc_query = "mexc_query"
    gate_v4 = "gate_v4"
    kucoin_v2 = "kucoin_v2"
    bitmart_v2 = "bitmart_v2"
    coinstore_hmac = "coinstore_hmac"


@dataclass(frozen=True)
class VenueExecutionSpec:
    venue: ExecutionVenue
    signing_style: SigningStyle
    place_order_path: str
    cancel_order_path: str
    cancel_all_path: str
    order_status_path: str
    account_balances_path: str
    exchange_info_path: str
    order_replace_path: str | None = None
    bulk_order_path: str | None = None
    bulk_cancel_path: str | None = None
    open_orders_path: str | None = None
    account_trades_path: str | None = None
    symbol_separator: str = ""


EXECUTION_SPECS: dict[ExecutionVenue, VenueExecutionSpec] = {
    ExecutionVenue.binance: VenueExecutionSpec(ExecutionVenue.binance, SigningStyle.binance_query, "/api/v3/order", "/api/v3/order", "/api/v3/openOrders", "/api/v3/order", "/api/v3/account", "/api/v3/exchangeInfo", "/api/v3/order/cancelReplace"),
    ExecutionVenue.mexc: VenueExecutionSpec(ExecutionVenue.mexc, SigningStyle.mexc_query, "/api/v3/order", "/api/v3/order", "/api/v3/openOrders", "/api/v3/order", "/api/v3/account", "/api/v3/exchangeInfo"),
    ExecutionVenue.gate: VenueExecutionSpec(ExecutionVenue.gate, SigningStyle.gate_v4, "/spot/orders", "/spot/orders/{order_id}", "/spot/orders", "/spot/orders/{order_id}", "/spot/accounts", "/spot/currency_pairs", symbol_separator="_"),
    ExecutionVenue.bitmart: VenueExecutionSpec(ExecutionVenue.bitmart, SigningStyle.bitmart_v2, "/spot/v2/submit_order", "/spot/v3/cancel_order", "/spot/v1/cancel_orders", "/spot/v1/order_detail", "/spot/v1/wallet", "/spot/v1/symbols/details", "/spot/v1/cancel_order"),
    ExecutionVenue.kucoin: VenueExecutionSpec(ExecutionVenue.kucoin, SigningStyle.kucoin_v2, "/api/v1/orders", "/api/v1/orders/{order_id}", "/api/v1/orders", "/api/v1/orders/{order_id}", "/api/v1/accounts", "/api/v2/symbols", symbol_separator="-"),
    ExecutionVenue.coinstore: VenueExecutionSpec(ExecutionVenue.coinstore, SigningStyle.coinstore_hmac, "/api/trade/order/place", "/api/trade/order/cancel", "/api/trade/order/cancelAll", "/api/trade/order/orderInfo", "/api/spot/accountList", "/api/v1/market/tickers", open_orders_path="/api/trade/order/active", account_trades_path="/api/trade/match/accountMatches", symbol_separator=""),
}
