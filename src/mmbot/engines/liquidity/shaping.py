from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LiquidityBand:
    level: int
    min_distance_bps: float
    max_distance_bps: float
    target_depth: float
    size_multiplier: float


@dataclass(frozen=True)
class ShapedOrder:
    side: str
    level: int
    distance_bps: float
    price: float
    quantity: float


@dataclass(frozen=True)
class LiquidityShape:
    orders: list[ShapedOrder]
    concentration_ratio: float
    target_depth: float


class LiquidityBandBuilder:
    def build_bands(self, levels: int, base_distance_bps: float, target_depth: float, volatility: float) -> list[LiquidityBand]:
        spacing_multiplier = 1.0 + min(2.0, volatility * 25.0)
        bands: list[LiquidityBand] = []
        for level in range(1, levels + 1):
            min_distance = base_distance_bps * level * spacing_multiplier
            max_distance = min_distance + base_distance_bps * spacing_multiplier
            depth = target_depth / levels
            size_multiplier = 1.0 + (level - 1) * 0.2
            bands.append(LiquidityBand(level, min_distance, max_distance, depth, size_multiplier))
        return bands


class DepthShaper:
    def shape(self, mid_price: float, base_quantity: float, bands: list[LiquidityBand], inventory_skew_bps: float, imbalance: float) -> LiquidityShape:
        orders: list[ShapedOrder] = []
        total_depth = sum(band.target_depth for band in bands)
        near_depth = 0.0
        for band in bands:
            distance = (band.min_distance_bps + band.max_distance_bps) / 2
            bid_distance = max(1.0, distance + inventory_skew_bps + max(0.0, imbalance) * 10)
            ask_distance = max(1.0, distance - inventory_skew_bps + max(0.0, -imbalance) * 10)
            bid_qty = base_quantity * band.size_multiplier * (0.75 if imbalance < -0.5 else 1.0)
            ask_qty = base_quantity * band.size_multiplier * (0.75 if imbalance > 0.5 else 1.0)
            bid_price = mid_price * (1 - bid_distance / 10000)
            ask_price = mid_price * (1 + ask_distance / 10000)
            orders.append(ShapedOrder("buy", band.level, bid_distance, round(bid_price, 8), bid_qty))
            orders.append(ShapedOrder("sell", band.level, ask_distance, round(ask_price, 8), ask_qty))
            if band.level == 1:
                near_depth += band.target_depth
        concentration = 0.0 if total_depth == 0 else near_depth / total_depth
        return LiquidityShape(orders, concentration, total_depth)


class SyntheticOrderBookShaper:
    def synthetic_depth(self, shape: LiquidityShape) -> dict[str, float]:
        bid_depth = sum(order.quantity for order in shape.orders if order.side == "buy")
        ask_depth = sum(order.quantity for order in shape.orders if order.side == "sell")
        return {"bid_depth": bid_depth, "ask_depth": ask_depth, "imbalance": 0.0 if bid_depth + ask_depth == 0 else (bid_depth - ask_depth) / (bid_depth + ask_depth)}


class LiquidityShapingEngine:
    def __init__(self, levels: int, base_distance_bps: float, target_depth: float):
        self.levels = levels
        self.base_distance_bps = base_distance_bps
        self.target_depth = target_depth
        self.band_builder = LiquidityBandBuilder()
        self.depth_shaper = DepthShaper()
        self.synthetic = SyntheticOrderBookShaper()

    def build_shape(self, mid_price: float, base_quantity: float, volatility: float, inventory_skew_bps: float, imbalance: float) -> tuple[LiquidityShape, dict[str, float]]:
        bands = self.band_builder.build_bands(self.levels, self.base_distance_bps, self.target_depth, volatility)
        shape = self.depth_shaper.shape(mid_price, base_quantity, bands, inventory_skew_bps, imbalance)
        return shape, self.synthetic.synthetic_depth(shape)
