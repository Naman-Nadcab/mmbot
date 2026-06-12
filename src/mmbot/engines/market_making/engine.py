from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from mmbot.core.config import InventorySettings, OrderLayerSettings, OrderSizeSettings, SpreadSettings


@dataclass(frozen=True)
class MarketState:
    symbol: str
    mid_price: float
    spread_bps: float
    volatility: float
    liquidity_imbalance: float


@dataclass(frozen=True)
class InventoryState:
    base_ratio: float
    target_base_ratio: float
    exposure_notional: float


@dataclass(frozen=True)
class Quote:
    side: str
    price: float
    quantity: float
    level: int
    client_order_id: str


@dataclass(frozen=True)
class ReplacementDecision:
    replace: bool
    reason: str
    desired: Quote
    existing_order_id: str | None = None


class SpreadEngine:
    def __init__(self, settings: SpreadSettings):
        self.settings = settings

    def adjusted_spread_bps(self, market: MarketState) -> float:
        volatility_component = market.volatility * 10000 * self.settings.volatility_multiplier
        liquidity_component = abs(market.liquidity_imbalance) * self.settings.base_spread_bps
        return min(self.settings.max_spread_bps, max(self.settings.min_spread_bps, self.settings.base_spread_bps + volatility_component + liquidity_component))


class InventorySkewEngine:
    def __init__(self, settings: InventorySettings):
        self.settings = settings

    def skew_bps(self, inventory: InventoryState) -> float:
        deviation = inventory.base_ratio - inventory.target_base_ratio
        return deviation * self.settings.skew_intensity * 100


class VolatilityAdjustmentEngine:
    def price_buffer_bps(self, volatility: float) -> float:
        return max(0.0, volatility * 10000)


class PriceProtectionLayer:
    def protect(self, side: str, price: float, mid_price: float, max_distance_bps: float) -> float:
        max_distance = mid_price * max_distance_bps / 10000
        if side == "buy":
            return min(price, mid_price + max_distance)
        return max(price, mid_price - max_distance)


class LiquidityPlacementLayer:
    def improve_for_imbalance(self, side: str, price: float, mid_price: float, imbalance: float) -> float:
        adjustment = mid_price * abs(imbalance) / 10000
        if imbalance > 0 and side == "sell":
            return price - adjustment
        if imbalance < 0 and side == "buy":
            return price + adjustment
        return price


class OrderLadderEngine:
    def __init__(self, settings: OrderSizeSettings, layers: OrderLayerSettings):
        self.settings = settings
        self.layers = layers

    def build(self, symbol: str, mid_price: float, spread_bps: float, inventory_skew_bps: float, volatility_buffer_bps: float, liquidity_imbalance: float) -> list[Quote]:
        quotes: list[Quote] = []
        protection = PriceProtectionLayer()
        liquidity = LiquidityPlacementLayer()
        levels = min(self.settings.ladder_levels, self.layers.enabled_levels, self.layers.max_active_orders_per_side)
        for level in range(1, levels + 1):
            spacing = self.layers.spacing_bps * (level - 1) * self.layers.outer_level_multiplier
            level_spread = spread_bps + spacing + volatility_buffer_bps
            quantity = min(self.settings.max_order_size, max(self.settings.min_order_size, self.settings.base_order_size * (self.settings.ladder_size_multiplier ** (level - 1))))
            bid_bps = level_spread / 2 + inventory_skew_bps
            ask_bps = level_spread / 2 - inventory_skew_bps
            bid_price = mid_price * (1 - bid_bps / 10000)
            ask_price = mid_price * (1 + ask_bps / 10000)
            bid_price = liquidity.improve_for_imbalance("buy", bid_price, mid_price, liquidity_imbalance)
            ask_price = liquidity.improve_for_imbalance("sell", ask_price, mid_price, liquidity_imbalance)
            bid_price = protection.protect("buy", bid_price, mid_price, max(level_spread, 1))
            ask_price = protection.protect("sell", ask_price, mid_price, max(level_spread, 1))
            quotes.append(Quote("buy", round(bid_price, 8), quantity, level, self._client_id(symbol, "buy", level)))
            quotes.append(Quote("sell", round(ask_price, 8), quantity, level, self._client_id(symbol, "sell", level)))
        return quotes

    def _client_id(self, symbol: str, side: str, level: int) -> str:
        return f"mm-{symbol.replace('/', '').lower()}-{side}-{level}-{uuid.uuid4().hex[:12]}"


class QuoteEngine:
    def __init__(self, spread: SpreadSettings, order_size: OrderSizeSettings, inventory: InventorySettings, order_layers: OrderLayerSettings | None = None):
        if order_layers is None:
            order_layers = OrderLayerSettings(enabled_levels=order_size.ladder_levels, spacing_bps=spread.base_spread_bps, outer_level_multiplier=1.25, refresh_threshold_bps=5, max_active_orders_per_side=order_size.ladder_levels)
        self.spread_settings = spread
        self.order_size_settings = order_size
        self.inventory_settings = inventory
        self.order_layer_settings = order_layers
        self.spread_engine = SpreadEngine(spread)
        self.inventory_skew = InventorySkewEngine(inventory)
        self.volatility_adjustment = VolatilityAdjustmentEngine()
        self.ladder = OrderLadderEngine(order_size, order_layers)

    def update_settings(self, spread: SpreadSettings, order_size: OrderSizeSettings, inventory: InventorySettings, order_layers: OrderLayerSettings) -> None:
        self.spread_settings = spread
        self.order_size_settings = order_size
        self.inventory_settings = inventory
        self.order_layer_settings = order_layers
        self.spread_engine = SpreadEngine(spread)
        self.inventory_skew = InventorySkewEngine(inventory)
        self.ladder = OrderLadderEngine(order_size, order_layers)

    def generate_quotes(self, market: MarketState, inventory: InventoryState) -> list[Quote]:
        spread_bps = self.spread_engine.adjusted_spread_bps(market)
        skew_bps = self.inventory_skew.skew_bps(inventory)
        volatility_buffer = self.volatility_adjustment.price_buffer_bps(market.volatility)
        return self.ladder.build(market.symbol, market.mid_price, spread_bps, skew_bps, volatility_buffer, market.liquidity_imbalance)


class RefreshEngine:
    def should_refresh(self, last_refresh: datetime, interval_seconds: int) -> bool:
        return (datetime.now(timezone.utc) - last_refresh).total_seconds() >= interval_seconds


class OrderReplacementEngine:
    def decisions(self, desired: list[Quote], existing: dict[tuple[str, int], dict[str, float]], price_threshold_bps: float) -> list[ReplacementDecision]:
        decisions: list[ReplacementDecision] = []
        for quote in desired:
            key = (quote.side, quote.level)
            current = existing.get(key)
            if current is None:
                decisions.append(ReplacementDecision(True, "missing_order", quote))
                continue
            current_price = current["price"]
            diff_bps = abs(quote.price - current_price) / quote.price * 10000
            if diff_bps >= price_threshold_bps or abs(quote.quantity - current["quantity"]) > 1e-12:
                decisions.append(ReplacementDecision(True, "price_or_size_drift", quote, str(current.get("order_id"))))
            else:
                decisions.append(ReplacementDecision(False, "within_threshold", quote, str(current.get("order_id"))))
        return decisions
