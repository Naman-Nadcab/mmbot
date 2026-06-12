from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from mmbot.core.config import AlertSettings, ExchangeSettings, InventorySettings, LiquiditySettings, OrderLayerSettings, OrderSizeSettings, RiskSettings, SpreadSettings, StrategySettings, VolumeSettings

ConfigDomain = Literal["strategy", "spread", "order_layers", "order_size", "volume", "inventory", "risk", "exchange", "liquidity", "alert"]

DOMAIN_MODELS = {
    "strategy": StrategySettings,
    "spread": SpreadSettings,
    "order_layers": OrderLayerSettings,
    "order_size": OrderSizeSettings,
    "volume": VolumeSettings,
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


class ConfirmedActionRequest(BaseModel):
    confirmation: str = Field(min_length=3)
    reason: str = Field(min_length=3)


class StrategyCommandRequest(ConfirmedActionRequest):
    command: Literal["start", "pause", "resume", "stop"]


class ExchangeAccountCreateRequest(BaseModel):
    account_alias: str = Field(min_length=1)
    environment: Literal["sandbox", "staging", "production"] = "production"
    api_key: str = Field(min_length=1)
    api_secret: str = Field(min_length=1)
    passphrase: str | None = None
    permissions: list[str] = Field(default_factory=list)
    is_enabled: bool = False


class ExchangeAccountStatusRequest(BaseModel):
    is_enabled: bool
    confirmation: str = Field(min_length=3)
    reason: str = Field(min_length=3)
