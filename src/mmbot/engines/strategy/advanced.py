from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class MarketRegime(str, Enum):
    stable = "stable"
    trending = "trending"
    volatile = "volatile"
    stressed = "stressed"


@dataclass(frozen=True)
class MicrostructureSnapshot:
    mid_price: float
    spread_bps: float
    realized_volatility: float
    orderbook_imbalance: float
    trade_imbalance: float
    top_of_book_depth: float
    depth_slope: float
    short_horizon_return_bps: float


@dataclass(frozen=True)
class InventoryProfile:
    base_ratio: float
    target_base_ratio: float
    exposure_notional: float
    max_exposure_notional: float


@dataclass(frozen=True)
class StrategyDecision:
    regime: MarketRegime
    bid_spread_bps: float
    ask_spread_bps: float
    bid_size_multiplier: float
    ask_size_multiplier: float
    quote_skew_bps: float
    placement_offset_bps: float
    neutralization_required: bool
    reduction_side: str | None


class MarketRegimeDetector:
    def detect(self, micro: MicrostructureSnapshot) -> MarketRegime:
        if micro.realized_volatility > 0.05 or abs(micro.short_horizon_return_bps) > 150:
            return MarketRegime.stressed
        if micro.realized_volatility > 0.02 or abs(micro.orderbook_imbalance) > 0.6:
            return MarketRegime.volatile
        if abs(micro.short_horizon_return_bps) > 35 or abs(micro.trade_imbalance) > 0.4:
            return MarketRegime.trending
        return MarketRegime.stable


class DynamicSpreadModel:
    def spread(self, base_spread_bps: float, micro: MicrostructureSnapshot, regime: MarketRegime) -> float:
        regime_multiplier = {MarketRegime.stable: 0.85, MarketRegime.trending: 1.1, MarketRegime.volatile: 1.6, MarketRegime.stressed: 2.5}[regime]
        volatility_component = micro.realized_volatility * 10000
        liquidity_component = max(0.0, 1.0 - min(1.0, micro.top_of_book_depth / 100000.0)) * base_spread_bps
        imbalance_component = abs(micro.orderbook_imbalance) * base_spread_bps
        return max(1.0, (base_spread_bps + volatility_component + liquidity_component + imbalance_component) * regime_multiplier)


class InventoryAwareSpreadModel:
    def skew(self, inventory: InventoryProfile) -> float:
        deviation = inventory.base_ratio - inventory.target_base_ratio
        pressure = min(1.0, abs(inventory.exposure_notional) / max(inventory.max_exposure_notional, 1.0))
        return deviation * 100.0 * (1.0 + pressure)


class AdaptiveQuotePlacement:
    def placement_offset(self, micro: MicrostructureSnapshot, regime: MarketRegime) -> float:
        stability_bonus = 2.0 if regime is MarketRegime.stable else 0.0
        adverse_selection_penalty = abs(micro.trade_imbalance) * 8.0 + max(0.0, -micro.depth_slope) * 5.0
        return max(-5.0, min(25.0, adverse_selection_penalty - stability_bonus))


class InventoryNeutralization:
    def decision(self, inventory: InventoryProfile) -> tuple[bool, str | None]:
        utilization = abs(inventory.exposure_notional) / max(inventory.max_exposure_notional, 1.0)
        if utilization < 0.7:
            return False, None
        if inventory.base_ratio > inventory.target_base_ratio:
            return True, "sell"
        return True, "buy"


class InstitutionalStrategyEngine:
    def __init__(self, base_spread_bps: float):
        self.base_spread_bps = base_spread_bps
        self.regime_detector = MarketRegimeDetector()
        self.spread_model = DynamicSpreadModel()
        self.inventory_model = InventoryAwareSpreadModel()
        self.placement = AdaptiveQuotePlacement()
        self.neutralization = InventoryNeutralization()

    def decide(self, micro: MicrostructureSnapshot, inventory: InventoryProfile) -> StrategyDecision:
        regime = self.regime_detector.detect(micro)
        spread = self.spread_model.spread(self.base_spread_bps, micro, regime)
        skew = self.inventory_model.skew(inventory)
        placement_offset = self.placement.placement_offset(micro, regime)
        neutralize, reduction_side = self.neutralization.decision(inventory)
        imbalance_adjustment = micro.orderbook_imbalance * 5.0
        bid_spread = max(1.0, spread / 2 + skew + imbalance_adjustment + placement_offset)
        ask_spread = max(1.0, spread / 2 - skew - imbalance_adjustment + placement_offset)
        if regime is MarketRegime.stable:
            bid_spread *= 0.9
            ask_spread *= 0.9
        if regime in {MarketRegime.volatile, MarketRegime.stressed}:
            bid_spread *= 1.25
            ask_spread *= 1.25
        bid_size = 1.0 if reduction_side != "sell" else 0.4
        ask_size = 1.0 if reduction_side != "buy" else 0.4
        return StrategyDecision(regime, bid_spread, ask_spread, bid_size, ask_size, skew, placement_offset, neutralize, reduction_side)
