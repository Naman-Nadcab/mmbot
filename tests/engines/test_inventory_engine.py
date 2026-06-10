from mmbot.core.config import default_runtime_config
from mmbot.engines.inventory.engine import AssetBalance, InventoryEngine


def test_inventory_report_and_target_delta():
    config = default_runtime_config()
    engine = InventoryEngine(config.inventory)
    balances = [AssetBalance("BTC", 1.0, 0.8, 0.2, 100000.0), AssetBalance("USDT", 100000.0, 90000.0, 10000.0, 1.0)]
    report = engine.report(balances, "BTC")
    assert report.total_notional == 200000.0
    assert report.target_base_notional == 100000.0
    assert engine.inventory_target_delta(balances, "BTC") == 0.0
