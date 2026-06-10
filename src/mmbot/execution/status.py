from __future__ import annotations

from mmbot.execution.models import NormalizedOrderStatus

STATUS_MAP = {
    "NEW": NormalizedOrderStatus.open,
    "PARTIALLY_FILLED": NormalizedOrderStatus.partially_filled,
    "FILLED": NormalizedOrderStatus.filled,
    "CANCELED": NormalizedOrderStatus.cancelled,
    "CANCELLED": NormalizedOrderStatus.cancelled,
    "REJECTED": NormalizedOrderStatus.rejected,
    "EXPIRED": NormalizedOrderStatus.expired,
    "open": NormalizedOrderStatus.open,
    "closed": NormalizedOrderStatus.filled,
    "cancelled": NormalizedOrderStatus.cancelled,
    "canceled": NormalizedOrderStatus.cancelled,
    "done": NormalizedOrderStatus.filled,
    "active": NormalizedOrderStatus.open,
    "success": NormalizedOrderStatus.open,
}


def normalize_status(value: object) -> NormalizedOrderStatus:
    if value is None:
        return NormalizedOrderStatus.unknown
    return STATUS_MAP.get(str(value), STATUS_MAP.get(str(value).upper(), NormalizedOrderStatus.unknown))
