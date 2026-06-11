from __future__ import annotations

import json
import os
import sys
from functools import lru_cache
from typing import Annotated, Any, Dict, Literal

from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

AppEnv = Literal["development", "staging", "production", "test"]
TradingMode = Literal["shadow", "paper", "canary", "live"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, case_sensitive=True, extra="ignore")

    APP_ENV: AppEnv = "development"
    LOG_LEVEL: str = "INFO"
    SERVER_IP: str = "127.0.0.1"
    SERVER_PORT: int = 8000
    DATABASE_URL: str
    REDIS_URL: str
    REDIS_PASSWORD: str | None = None
    JWT_SECRET: str
    TELEGRAM_BOT_TOKEN: str
    TELEGRAM_CHAT_ID: str
    EXCHANGE_API_KEYS: Dict[str, str] = Field(default_factory=dict)
    EXCHANGE_API_SECRETS: Dict[str, str] = Field(default_factory=dict)
    EXCHANGE_API_PASSPHRASES: Dict[str, str] = Field(default_factory=dict)
    EXCHANGE_API_MEMOS: Dict[str, str] = Field(default_factory=dict)
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 20
    DB_POOL_TIMEOUT_SECONDS: int = 30
    REDIS_SOCKET_TIMEOUT_SECONDS: float = 5.0
    HTTP_TIMEOUT_SECONDS: float = 10.0
    EXCHANGE_RECONNECT_MAX_DELAY_SECONDS: float = 30.0
    JWT_ALGORITHM: str = "HS256"
    TRADING_MODE: TradingMode = "paper"
    MARKET_DATA_EXCHANGES: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["binance", "coinstore", "mexc", "gate", "bitmart", "kucoin"])
    MARKET_DATA_SYMBOLS: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["BTC/USDT"])
    MARKET_DATA_STREAMS: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["orderbook", "trades", "ticker", "kline"])
    MARKET_DATA_CONNECT_ON_START: bool = True
    MARKET_DATA_PERSIST_EVERY_N_MESSAGES: int = 25
    MARKET_MAKER_REFRESH_SECONDS: float = 5.0
    RECONCILIATION_INTERVAL_SECONDS: float = 60.0
    PAPER_STARTING_CASH: float = 100000.0
    PAPER_BASE_ASSET: str = "BTC"
    PAPER_QUOTE_ASSET: str = "USDT"

    @field_validator("LOG_LEVEL")
    @classmethod
    def validate_log_level(cls, value: str) -> str:
        normalized = value.upper()
        allowed = {"TRACE", "DEBUG", "INFO", "WARNING", "WARN", "ERROR", "CRITICAL"}
        if normalized not in allowed:
            raise ValueError(f"LOG_LEVEL must be one of {sorted(allowed)}")
        return normalized

    @field_validator("DATABASE_URL", mode="before")
    @classmethod
    def normalize_database_url(cls, value: Any) -> str:
        url = str(value)
        if url.startswith("postgresql://"):
            return url.replace("postgresql://", "postgresql+asyncpg://", 1)
        if url.startswith("postgres://"):
            return url.replace("postgres://", "postgresql+asyncpg://", 1)
        if url.startswith("postgresql+psycopg2://") or url.startswith("postgresql+psycopg://"):
            raise ValueError("DATABASE_URL must use an async driver: postgresql+asyncpg://")
        return url

    @field_validator("MARKET_DATA_EXCHANGES", "MARKET_DATA_SYMBOLS", "MARKET_DATA_STREAMS", mode="before")
    @classmethod
    def parse_string_lists(cls, value: Any) -> list[str]:
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        if isinstance(value, str):
            stripped = value.strip()
            try:
                parsed = json.loads(stripped)
                if isinstance(parsed, list):
                    return [str(item).strip() for item in parsed if str(item).strip()]
                if isinstance(parsed, str):
                    return cls.parse_string_lists(parsed)
            except json.JSONDecodeError:
                pass
            if len(stripped) >= 2 and stripped[0] == stripped[-1] and stripped[0] in {"'", '"'}:
                stripped = stripped[1:-1].strip()
            if stripped.startswith("["):
                parsed = json.loads(stripped)
                if not isinstance(parsed, list):
                    raise ValueError("expected a JSON array")
                return [str(item).strip() for item in parsed if str(item).strip()]
            return [item.strip() for item in stripped.split(",") if item.strip()]
        raise ValueError("expected list, JSON array string, or comma-separated string")

    @field_validator("EXCHANGE_API_KEYS", "EXCHANGE_API_SECRETS", "EXCHANGE_API_PASSPHRASES", "EXCHANGE_API_MEMOS", mode="before")
    @classmethod
    def parse_exchange_maps(cls, value: Any) -> Dict[str, str]:
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            parsed = json.loads(value)
            if not isinstance(parsed, dict):
                raise ValueError("exchange credential values must be JSON objects")
            return {str(k): str(v) for k, v in parsed.items()}
        raise ValueError("exchange credential values must be JSON objects")

    @field_validator("JWT_SECRET")
    @classmethod
    def validate_jwt_secret(cls, value: str) -> str:
        if len(value) < 32:
            raise ValueError("JWT_SECRET must contain at least 32 characters")
        return value

    def exchange_credentials(self, alias: str) -> tuple[str, str]:
        return self.EXCHANGE_API_KEYS[alias], self.EXCHANGE_API_SECRETS[alias]


class SpreadSettings(BaseModel):
    base_spread_bps: float = Field(gt=0)
    min_spread_bps: float = Field(gt=0)
    max_spread_bps: float = Field(gt=0)
    volatility_multiplier: float = Field(ge=0)


class OrderSizeSettings(BaseModel):
    base_order_size: float = Field(gt=0)
    min_order_size: float = Field(gt=0)
    max_order_size: float = Field(gt=0)
    ladder_levels: int = Field(ge=1, le=50)
    ladder_size_multiplier: float = Field(gt=0)


class InventorySettings(BaseModel):
    target_base_ratio: float = Field(ge=0, le=1)
    skew_intensity: float = Field(ge=0)
    max_asset_exposure: float = Field(gt=0)
    alert_threshold_ratio: float = Field(gt=0, le=1)


class RiskSettings(BaseModel):
    max_position_notional: float = Field(gt=0)
    max_total_exposure: float = Field(gt=0)
    max_order_notional: float = Field(gt=0)
    max_open_orders: int = Field(ge=1)
    max_daily_loss: float = Field(gt=0)
    circuit_breaker_error_threshold: int = Field(ge=1)
    circuit_breaker_cooldown_seconds: int = Field(ge=1)


class ExchangeSettings(BaseModel):
    enabled_exchanges: list[str]
    default_timeout_seconds: float = Field(gt=0)
    max_reconnect_delay_seconds: float = Field(gt=0)
    heartbeat_interval_seconds: float = Field(gt=0)


class LiquiditySettings(BaseModel):
    depth_levels: int = Field(ge=1, le=200)
    imbalance_threshold: float = Field(gt=0, le=1)
    min_top_of_book_depth: float = Field(ge=0)


class AlertSettings(BaseModel):
    enabled_channels: list[str]
    min_severity: Literal["info", "warning", "critical", "emergency"]
    telegram_enabled: bool = True


class RuntimeConfig(BaseModel):
    spread: SpreadSettings
    order_size: OrderSizeSettings
    inventory: InventorySettings
    risk: RiskSettings
    exchange: ExchangeSettings
    liquidity: LiquiditySettings
    alert: AlertSettings


def default_runtime_config() -> RuntimeConfig:
    return RuntimeConfig(
        spread=SpreadSettings(base_spread_bps=20, min_spread_bps=5, max_spread_bps=200, volatility_multiplier=1.5),
        order_size=OrderSizeSettings(base_order_size=0.01, min_order_size=0.001, max_order_size=1.0, ladder_levels=3, ladder_size_multiplier=1.25),
        inventory=InventorySettings(target_base_ratio=0.5, skew_intensity=0.75, max_asset_exposure=100000, alert_threshold_ratio=0.8),
        risk=RiskSettings(max_position_notional=100000, max_total_exposure=250000, max_order_notional=25000, max_open_orders=100, max_daily_loss=10000, circuit_breaker_error_threshold=5, circuit_breaker_cooldown_seconds=300),
        exchange=ExchangeSettings(enabled_exchanges=["binance", "coinstore", "mexc", "gate", "bitmart", "kucoin"], default_timeout_seconds=10, max_reconnect_delay_seconds=30, heartbeat_interval_seconds=20),
        liquidity=LiquiditySettings(depth_levels=20, imbalance_threshold=0.35, min_top_of_book_depth=0),
        alert=AlertSettings(enabled_channels=["telegram", "dashboard"], min_severity="warning", telegram_enabled=True),
    )


@lru_cache
def get_settings() -> Settings:
    _emit_market_data_settings_diagnostics()
    settings = Settings()
    _emit_market_data_settings_diagnostics(settings)
    return settings


def _emit_market_data_settings_diagnostics(settings: Settings | None = None) -> None:
    fields = ("MARKET_DATA_EXCHANGES", "MARKET_DATA_SYMBOLS", "MARKET_DATA_STREAMS")
    for field in fields:
        raw = os.environ.get(field)
        decoded = getattr(settings, field, None) if settings is not None else None
        provider = "EnvSettingsSource(os.environ)" if raw is not None else "DefaultSettingsSource"
        print(
            json.dumps(
                {
                    "event": "settings_startup_diagnostics",
                    "field": field,
                    "source_provider": provider,
                    "raw_env_value": raw,
                    "decoded_value": decoded,
                    "phase": "after" if settings is not None else "before",
                },
                default=str,
                separators=(",", ":"),
            ),
            file=sys.stderr,
        )
