from decimal import Decimal

from mmbot.execution.models import ExecutionOrderType, ExecutionSide, ExecutionVenue, OrderIntent, SymbolPrecision
from mmbot.execution.precision import apply_symbol_precision
from mmbot.execution.signing import ExecutionCredentials, sign_request
from mmbot.execution.specs import SigningStyle


def test_precision_enforces_tick_lot_and_notional():
    intent = OrderIntent(ExecutionVenue.binance, "BTC/USDT", ExecutionSide.buy, ExecutionOrderType.limit, Decimal("1.234567"), Decimal("100.123456"), "cid")
    precision = SymbolPrecision("BTC/USDT", "BTCUSDT", Decimal("0.01"), Decimal("0.001"), Decimal("0.001"), Decimal("10"), 2, 3)
    adjusted = apply_symbol_precision(intent, precision)
    assert adjusted.quantity == Decimal("1.234")
    assert adjusted.price == Decimal("100.12")


def test_binance_query_signing_adds_signature_and_key_header():
    signed = sign_request(SigningStyle.binance_query, "POST", "/api/v3/order", {"symbol": "BTCUSDT"}, None, ExecutionCredentials("key", "secret"))
    assert signed.headers["X-MBX-APIKEY"] == "key"
    assert "signature" in signed.params
    assert "timestamp" in signed.params
