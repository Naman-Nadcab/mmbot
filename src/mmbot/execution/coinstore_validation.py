from __future__ import annotations

from typing import Any

from mmbot.execution.models import ExecutionVenue, OrderIntent, SymbolPrecision
from mmbot.execution.precision import validate_symbol_precision
from mmbot.execution.signing import ExecutionCredentials, sign_request
from mmbot.execution.specs import EXECUTION_SPECS, SigningStyle


class CoinstoreValidationError(RuntimeError):
    pass


class CoinstoreValidationLayer:
    REQUIRED_PATHS = ("place_order_path", "cancel_order_path", "cancel_all_path", "order_status_path", "account_balances_path", "exchange_info_path", "open_orders_path", "account_trades_path")

    def verify_endpoints(self) -> None:
        spec = EXECUTION_SPECS[ExecutionVenue.coinstore]
        for field in self.REQUIRED_PATHS:
            value = getattr(spec, field)
            if not isinstance(value, str) or not value.startswith("/api/"):
                raise CoinstoreValidationError(f"Coinstore endpoint {field} is not configured")

    def verify_signing(self, credentials: ExecutionCredentials) -> None:
        signed = sign_request(SigningStyle.coinstore_hmac, "POST", EXECUTION_SPECS[ExecutionVenue.coinstore].place_order_path, {}, {"symbol": "BTCUSDT"}, credentials)
        missing = {"X-CS-APIKEY", "X-CS-EXPIRES", "X-CS-SIGN"} - set(signed.headers)
        if missing:
            raise CoinstoreValidationError(f"Coinstore signing headers missing: {sorted(missing)}")
        if signed.headers["X-CS-APIKEY"] != credentials.api_key or len(signed.headers["X-CS-SIGN"]) != 64:
            raise CoinstoreValidationError("Coinstore signing output is invalid")

    def verify_order_response(self, payload: dict[str, Any]) -> None:
        candidate = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        if not any(key in candidate for key in ("orderId", "order_id", "id", "ordId")):
            raise CoinstoreValidationError("Coinstore order response is missing order identifier")

    def verify_balance_response(self, payload: Any) -> None:
        rows = self._rows(payload)
        if not rows:
            raise CoinstoreValidationError("Coinstore balance response is empty")
        for row in rows:
            if not any(key in row for key in ("asset", "currency", "coin", "coinName")):
                raise CoinstoreValidationError("Coinstore balance row is missing asset")
            if not any(key in row for key in ("available", "available_balance", "availableBalance", "free", "normal")):
                raise CoinstoreValidationError("Coinstore balance row is missing available balance")

    def verify_trade_response(self, payload: Any) -> None:
        rows = self._rows(payload)
        for row in rows:
            if not any(key in row for key in ("tradeId", "trade_id", "matchId", "match_id")):
                raise CoinstoreValidationError("Coinstore trade row is missing trade identifier")
            if not any(key in row for key in ("execQty", "matchQty", "quantity", "filledQty")):
                raise CoinstoreValidationError("Coinstore trade row is missing execution quantity")

    def verify_runtime_ready(self, credentials: ExecutionCredentials, precision: SymbolPrecision, intent: OrderIntent) -> None:
        self.verify_endpoints()
        self.verify_signing(credentials)
        validate_symbol_precision(intent, precision)

    def _rows(self, payload: Any) -> list[dict[str, Any]]:
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if isinstance(payload, dict):
            for key in ("data", "balances", "accounts", "items", "list", "result"):
                value = payload.get(key)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)]
            return [payload]
        return []
