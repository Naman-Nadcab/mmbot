from mmbot.exchanges.registry import EXCHANGE_DEFINITIONS


def test_required_exchanges_have_transport_capabilities():
    required = {"binance", "coinstore", "mexc", "gate", "bitmart", "kucoin"}
    assert required == set(EXCHANGE_DEFINITIONS)
    for definition in EXCHANGE_DEFINITIONS.values():
        assert definition.rest_base_url.startswith("https://")
        assert definition.websocket_url.startswith("wss://")
        assert definition.capabilities.rest is True
        assert definition.capabilities.websocket is True
