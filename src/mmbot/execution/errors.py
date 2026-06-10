from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from mmbot.core.exceptions import ExchangeError, RateLimitExceededError
from mmbot.execution.models import ExecutionErrorContext, ExecutionVenue


@dataclass(frozen=True)
class VenueErrorRule:
    code: str
    normalized: str
    retryable: bool


class NonRetryableExchangeExecutionError(ExchangeError):
    """Raised for deterministic venue rejects that should not be retried."""


ERROR_RULES: dict[ExecutionVenue, dict[str, VenueErrorRule]] = {
    venue: {
        "429": VenueErrorRule("429", "rate_limited", True),
        "418": VenueErrorRule("418", "ip_banned", False),
        "500": VenueErrorRule("500", "venue_internal", True),
        "502": VenueErrorRule("502", "bad_gateway", True),
        "503": VenueErrorRule("503", "service_unavailable", True),
        "504": VenueErrorRule("504", "gateway_timeout", True),
        "INSUFFICIENT_BALANCE": VenueErrorRule("INSUFFICIENT_BALANCE", "insufficient_balance", False),
        "MIN_NOTIONAL": VenueErrorRule("MIN_NOTIONAL", "min_notional", False),
        "PRECISION": VenueErrorRule("PRECISION", "precision", False),
    }
    for venue in ExecutionVenue
}


def normalize_exchange_error(venue: ExecutionVenue, status_code: int | None, payload: Any) -> ExecutionErrorContext:
    code = str(status_code or _payload_code(payload) or "UNKNOWN")
    message = _payload_message(payload)
    rules = ERROR_RULES[venue]
    rule = rules.get(code) or rules.get(message.upper())
    if rule is None:
        retryable = status_code is not None and status_code >= 500
        normalized = code
    else:
        retryable = rule.retryable
        normalized = rule.normalized
    return ExecutionErrorContext(venue=venue, code=normalized, message=message, retryable=retryable, raw=payload)


def raise_normalized_error(context: ExecutionErrorContext) -> None:
    if context.code == "rate_limited":
        raise RateLimitExceededError(f"{context.venue.value}: {context.message}")
    if not context.retryable:
        raise NonRetryableExchangeExecutionError(f"{context.venue.value}:{context.code}: {context.message}")
    raise ExchangeError(f"{context.venue.value}:{context.code}: {context.message}")


def _payload_code(payload: Any) -> str | None:
    if isinstance(payload, dict):
        value = payload.get("code") or payload.get("err-code") or payload.get("errorCode") or payload.get("label")
        return str(value) if value is not None else None
    return None


def _payload_message(payload: Any) -> str:
    if isinstance(payload, dict):
        return str(payload.get("msg") or payload.get("message") or payload.get("errorMessage") or payload.get("detail") or payload)
    return str(payload)
