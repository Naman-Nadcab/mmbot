import jwt

from mmbot.cli.auth import issue_token
from mmbot.core.config import Settings


def _settings() -> Settings:
    return Settings(
        APP_ENV="test",
        DATABASE_URL="sqlite+aiosqlite:///:memory:",
        REDIS_URL="redis://localhost:6379/0",
        JWT_SECRET="x" * 48,
        TELEGRAM_BOT_TOKEN="token",
        TELEGRAM_CHAT_ID="chat",
        EXCHANGE_API_KEYS={"binance": "key"},
        EXCHANGE_API_SECRETS={"binance": "secret"},
    )


def test_issue_token_uses_active_settings_secret_and_claims():
    settings = _settings()
    token = issue_token(settings, "operator", ["platform_admin"], ["operations:read"], 3600)
    payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
    assert payload["sub"] == "operator"
    assert payload["roles"] == ["platform_admin"]
    assert payload["permissions"] == ["operations:read"]
    assert payload["exp"] > payload["iat"]
