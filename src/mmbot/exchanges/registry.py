from __future__ import annotations

from mmbot.exchanges.types import ExchangeCapabilities, ExchangeDefinition, ExchangeName, RateLimitRule

EXCHANGE_DEFINITIONS: dict[str, ExchangeDefinition] = {
    "binance": ExchangeDefinition(ExchangeName.binance, "https://api.binance.com", "wss://stream.binance.com:9443/ws", RateLimitRule(1200, 60), ExchangeCapabilities(True, True, True, True, True, True, True, True), "/api/v3/ping"),
    "coinstore": ExchangeDefinition(ExchangeName.coinstore, "https://api.coinstore.com", "wss://ws.coinstore.com/s/ws", RateLimitRule(300, 60), ExchangeCapabilities(True, True, True, True, True, True, True, True), "/api/v1/public/time"),
    "mexc": ExchangeDefinition(ExchangeName.mexc, "https://api.mexc.com", "wss://wbs.mexc.com/ws", RateLimitRule(1200, 60), ExchangeCapabilities(True, True, True, True, True, True, True, True), "/api/v3/ping"),
    "gate": ExchangeDefinition(ExchangeName.gate, "https://api.gateio.ws/api/v4", "wss://api.gateio.ws/ws/v4/", RateLimitRule(600, 60), ExchangeCapabilities(True, True, True, True, True, True, True, True), "/spot/time"),
    "bitmart": ExchangeDefinition(ExchangeName.bitmart, "https://api-cloud.bitmart.com", "wss://ws-manager-compress.bitmart.com/api?protocol=1.1", RateLimitRule(600, 60), ExchangeCapabilities(True, True, True, True, True, True, True, True), "/system/time"),
    "kucoin": ExchangeDefinition(ExchangeName.kucoin, "https://api.kucoin.com", "wss://ws-api-spot.kucoin.com", RateLimitRule(1800, 60), ExchangeCapabilities(True, True, True, True, True, True, True, True), "/api/v1/timestamp"),
}


def get_exchange_definition(name: str) -> ExchangeDefinition:
    return EXCHANGE_DEFINITIONS[name.lower()]
