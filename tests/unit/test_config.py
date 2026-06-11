import secrets

import pytest
from pydantic import ValidationError

from mmbot.core.config import RuntimeConfig, default_runtime_config
from mmbot.core.config import Settings


def test_default_runtime_config_is_valid():
    config = default_runtime_config()
    assert isinstance(config, RuntimeConfig)
    assert config.spread.min_spread_bps <= config.spread.base_spread_bps <= config.spread.max_spread_bps
    assert "binance" in config.exchange.enabled_exchanges


def _settings(database_url: str) -> Settings:
    return Settings(
        APP_ENV="test",
        DATABASE_URL=database_url,
        REDIS_URL="redis://localhost:6379/0",
        JWT_SECRET=secrets.token_urlsafe(48),
        TELEGRAM_BOT_TOKEN="token",
        TELEGRAM_CHAT_ID="chat",
        EXCHANGE_API_KEYS={"binance": "key"},
        EXCHANGE_API_SECRETS={"binance": "secret"},
    )


def test_database_url_normalizes_bare_postgresql_to_asyncpg():
    settings = _settings("postgresql://user:password@postgres:5432/mmbot")
    assert settings.DATABASE_URL == "postgresql+asyncpg://user:password@postgres:5432/mmbot"


def test_database_url_preserves_explicit_asyncpg():
    settings = _settings("postgresql+asyncpg://user:password@postgres:5432/mmbot")
    assert settings.DATABASE_URL == "postgresql+asyncpg://user:password@postgres:5432/mmbot"


def test_database_url_rejects_sync_postgresql_drivers():
    with pytest.raises(ValidationError):
        _settings("postgresql+psycopg2://user:password@postgres:5432/mmbot")


def test_market_data_exchange_json_array_env_is_decoded(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("JWT_SECRET", secrets.token_urlsafe(48))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat")
    monkeypatch.setenv("EXCHANGE_API_KEYS", '{"binance":"key"}')
    monkeypatch.setenv("EXCHANGE_API_SECRETS", '{"binance":"secret"}')
    monkeypatch.setenv("MARKET_DATA_EXCHANGES", '["binance"]')
    monkeypatch.setenv("MARKET_DATA_SYMBOLS", '["BTC/USDT"]')
    monkeypatch.setenv("MARKET_DATA_STREAMS", '["orderbook","ticker"]')

    settings = Settings()

    assert settings.MARKET_DATA_EXCHANGES == ["binance"]
    assert settings.MARKET_DATA_SYMBOLS == ["BTC/USDT"]
    assert settings.MARKET_DATA_STREAMS == ["orderbook", "ticker"]


def test_market_data_exchange_quoted_json_array_env_is_decoded(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("JWT_SECRET", secrets.token_urlsafe(48))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat")
    monkeypatch.setenv("EXCHANGE_API_KEYS", '{"binance":"key"}')
    monkeypatch.setenv("EXCHANGE_API_SECRETS", '{"binance":"secret"}')
    monkeypatch.setenv("MARKET_DATA_EXCHANGES", '\'["binance"]\'')
    monkeypatch.setenv("MARKET_DATA_SYMBOLS", '"[\\"BTC/USDT\\"]"')
    monkeypatch.setenv("MARKET_DATA_STREAMS", "orderbook,ticker")

    settings = Settings()

    assert settings.MARKET_DATA_EXCHANGES == ["binance"]
    assert settings.MARKET_DATA_SYMBOLS == ["BTC/USDT"]
    assert settings.MARKET_DATA_STREAMS == ["orderbook", "ticker"]
