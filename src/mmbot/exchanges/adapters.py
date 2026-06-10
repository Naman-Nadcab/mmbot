from __future__ import annotations

from dataclasses import asdict
from typing import Any

from mmbot.core.config import Settings
from mmbot.exchanges.auth import Credentials, HmacSigner
from mmbot.exchanges.client import RestClient, WebSocketClient
from mmbot.exchanges.registry import EXCHANGE_DEFINITIONS, get_exchange_definition
from mmbot.exchanges.types import ExchangeCapabilities, ExchangeDefinition


class BaseExchangeAdapter:
    def __init__(self, definition: ExchangeDefinition, settings: Settings, credential_alias: str | None = None):
        signer = None
        if credential_alias is not None and credential_alias in settings.EXCHANGE_API_KEYS:
            key, secret = settings.exchange_credentials(credential_alias)
            signer = HmacSigner(Credentials(key, secret))
        self.definition = definition
        self.rest = RestClient(definition, settings.HTTP_TIMEOUT_SECONDS, signer)
        self.websocket = WebSocketClient(definition, settings.EXCHANGE_RECONNECT_MAX_DELAY_SECONDS, settings.exchange.heartbeat_interval_seconds if hasattr(settings, "exchange") else 20)

    async def close(self) -> None:
        await self.rest.close()
        self.websocket.stop()

    async def discover_capabilities(self) -> ExchangeCapabilities:
        await self.rest.health()
        return self.definition.capabilities

    async def rest_request(self, method: str, path: str, *, params: dict[str, Any] | None = None, json_body: dict[str, Any] | None = None, signed: bool = False) -> Any:
        return await self.rest.request(method, path, params=params, json_body=json_body, signed=signed)

    def metadata(self) -> dict[str, Any]:
        payload = asdict(self.definition)
        payload["name"] = self.definition.name.value
        return payload


class BinanceAdapter(BaseExchangeAdapter):
    def __init__(self, settings: Settings, credential_alias: str | None = "binance"):
        super().__init__(get_exchange_definition("binance"), settings, credential_alias)


class CoinstoreAdapter(BaseExchangeAdapter):
    def __init__(self, settings: Settings, credential_alias: str | None = "coinstore"):
        super().__init__(get_exchange_definition("coinstore"), settings, credential_alias)


class MexcAdapter(BaseExchangeAdapter):
    def __init__(self, settings: Settings, credential_alias: str | None = "mexc"):
        super().__init__(get_exchange_definition("mexc"), settings, credential_alias)


class GateAdapter(BaseExchangeAdapter):
    def __init__(self, settings: Settings, credential_alias: str | None = "gate"):
        super().__init__(get_exchange_definition("gate"), settings, credential_alias)


class BitmartAdapter(BaseExchangeAdapter):
    def __init__(self, settings: Settings, credential_alias: str | None = "bitmart"):
        super().__init__(get_exchange_definition("bitmart"), settings, credential_alias)


class KucoinAdapter(BaseExchangeAdapter):
    def __init__(self, settings: Settings, credential_alias: str | None = "kucoin"):
        super().__init__(get_exchange_definition("kucoin"), settings, credential_alias)


ADAPTERS = {
    "binance": BinanceAdapter,
    "coinstore": CoinstoreAdapter,
    "mexc": MexcAdapter,
    "gate": GateAdapter,
    "bitmart": BitmartAdapter,
    "kucoin": KucoinAdapter,
}


def create_adapter(name: str, settings: Settings, credential_alias: str | None = None) -> BaseExchangeAdapter:
    return ADAPTERS[name.lower()](settings, credential_alias or name.lower())


def supported_exchanges() -> dict[str, dict[str, Any]]:
    return {name: asdict(definition) for name, definition in EXCHANGE_DEFINITIONS.items()}
