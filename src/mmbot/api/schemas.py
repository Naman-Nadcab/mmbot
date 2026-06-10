from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from mmbot.core.config import AlertSettings, ExchangeSettings, InventorySettings, LiquiditySettings, OrderSizeSettings, RiskSettings, SpreadSettings

ConfigDomain = Literal["spread", "order_size", "inventory", "risk", "exchange", "liquidity", "alert"]

DOMAIN_MODELS = {
    "spread": SpreadSettings,
    "order_size": OrderSizeSettings,
    "inventory": InventorySettings,
    "risk": RiskSettings,
    "exchange": ExchangeSettings,
    "liquidity": LiquiditySettings,
    "alert": AlertSettings,
}


class ConfigUpdateRequest(BaseModel):
    config: dict[str, Any] = Field(default_factory=dict)


class ConfigResponse(BaseModel):
    domain: str
    version: int
    config: dict[str, Any]


class HealthResponse(BaseModel):
    status: str
    dependencies: dict[str, str]
