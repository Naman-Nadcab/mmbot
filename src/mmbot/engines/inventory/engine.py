from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from mmbot.core.config import InventorySettings


@dataclass(frozen=True)
class AssetBalance:
    asset: str
    total: float
    available: float
    reserved: float
    price: float


@dataclass(frozen=True)
class Exposure:
    asset: str
    notional: float
    ratio: float
    alert: bool


@dataclass(frozen=True)
class InventoryReport:
    total_notional: float
    exposures: list[Exposure]
    target_base_notional: float
    skew_bps: float


class InventoryEngine:
    def __init__(self, settings: InventorySettings):
        self.settings = settings

    def exposures(self, balances: Iterable[AssetBalance]) -> list[Exposure]:
        balances_list = list(balances)
        total = sum(balance.total * balance.price for balance in balances_list)
        if total <= 0:
            return [Exposure(balance.asset, 0.0, 0.0, False) for balance in balances_list]
        results: list[Exposure] = []
        for balance in balances_list:
            notional = balance.total * balance.price
            ratio = notional / total
            results.append(Exposure(balance.asset, notional, ratio, notional >= self.settings.max_asset_exposure * self.settings.alert_threshold_ratio))
        return results

    def report(self, balances: Iterable[AssetBalance], base_asset: str) -> InventoryReport:
        balances_list = list(balances)
        exposures = self.exposures(balances_list)
        total = sum(item.notional for item in exposures)
        base = next((item for item in exposures if item.asset == base_asset), Exposure(base_asset, 0.0, 0.0, False))
        skew_bps = (base.ratio - self.settings.target_base_ratio) * self.settings.skew_intensity * 100
        return InventoryReport(total, exposures, total * self.settings.target_base_ratio, skew_bps)

    def inventory_target_delta(self, balances: Iterable[AssetBalance], base_asset: str) -> float:
        report = self.report(balances, base_asset)
        current = next((item.notional for item in report.exposures if item.asset == base_asset), 0.0)
        return report.target_base_notional - current
