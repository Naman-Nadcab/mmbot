from __future__ import annotations

from decimal import Decimal, ROUND_DOWN

from mmbot.execution.models import OrderIntent, SymbolPrecision


class PrecisionError(ValueError):
    """Raised when an order cannot satisfy venue precision filters."""


def quantize_down(value: Decimal, increment: Decimal) -> Decimal:
    if increment <= 0:
        raise PrecisionError("increment must be positive")
    return (value / increment).to_integral_value(rounding=ROUND_DOWN) * increment


def apply_symbol_precision(intent: OrderIntent, precision: SymbolPrecision) -> OrderIntent:
    quantity = quantize_down(intent.quantity, precision.quantity_step)
    if quantity < precision.min_quantity:
        raise PrecisionError(f"quantity {quantity} below minimum {precision.min_quantity} for {precision.symbol}")
    price = quantize_down(intent.price, precision.price_tick) if intent.price is not None else None
    notional = quantity * (price or Decimal("0"))
    if price is not None and notional < precision.min_notional:
        raise PrecisionError(f"notional {notional} below minimum {precision.min_notional} for {precision.symbol}")
    return OrderIntent(
        venue=intent.venue,
        symbol=intent.symbol,
        side=intent.side,
        order_type=intent.order_type,
        quantity=quantity,
        price=price,
        client_order_id=intent.client_order_id,
        time_in_force=intent.time_in_force,
        reduce_only=intent.reduce_only,
        metadata=dict(intent.metadata),
    )


def validate_symbol_precision(intent: OrderIntent, precision: SymbolPrecision) -> None:
    if intent.quantity <= 0:
        raise PrecisionError("quantity must be positive")
    if intent.quantity < precision.min_quantity:
        raise PrecisionError(f"quantity {intent.quantity} below minimum {precision.min_quantity} for {precision.symbol}")
    if quantize_down(intent.quantity, precision.quantity_step) != intent.quantity:
        raise PrecisionError(f"quantity {intent.quantity} does not align to lot size {precision.quantity_step} for {precision.symbol}")
    if intent.price is not None:
        if intent.price <= 0:
            raise PrecisionError("price must be positive")
        if quantize_down(intent.price, precision.price_tick) != intent.price:
            raise PrecisionError(f"price {intent.price} does not align to tick size {precision.price_tick} for {precision.symbol}")
        notional = intent.quantity * intent.price
        if notional < precision.min_notional:
            raise PrecisionError(f"notional {notional} below minimum {precision.min_notional} for {precision.symbol}")


def decimal_to_exchange(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(value.normalize(), "f")
