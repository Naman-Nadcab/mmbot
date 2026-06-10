from mmbot.core.config import RuntimeConfig, default_runtime_config


def test_default_runtime_config_is_valid():
    config = default_runtime_config()
    assert isinstance(config, RuntimeConfig)
    assert config.spread.min_spread_bps <= config.spread.base_spread_bps <= config.spread.max_spread_bps
    assert "binance" in config.exchange.enabled_exchanges
