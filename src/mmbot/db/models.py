from __future__ import annotations

import enum
import uuid
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Index, Integer, LargeBinary, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import JSON, TypeDecorator


class PortableJSON(TypeDecorator):
    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect):
        return dialect.type_descriptor(JSONB() if dialect.name == "postgresql" else JSON())

    def process_bind_param(self, value, dialect):
        return _json_safe(value)


def _json_safe(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, uuid.UUID):
        return str(value)
    if is_dataclass(value) and not isinstance(value, type):
        return _json_safe(asdict(value))
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return value


class PortableUUID(TypeDecorator):
    impl = String(36)
    cache_ok = True

    def load_dialect_impl(self, dialect):
        return dialect.type_descriptor(UUID(as_uuid=True) if dialect.name == "postgresql" else String(36))

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return value if dialect.name == "postgresql" and isinstance(value, uuid.UUID) else str(value)

    def process_result_value(self, value, dialect):
        if value is None or isinstance(value, uuid.UUID):
            return value
        return uuid.UUID(str(value))


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


class UserStatus(str, enum.Enum):
    active = "active"
    inactive = "inactive"
    locked = "locked"
    pending = "pending"


class BotStatus(str, enum.Enum):
    draft = "draft"
    disabled = "disabled"
    enabled = "enabled"
    suspended = "suspended"
    archived = "archived"


class OrderSide(str, enum.Enum):
    buy = "buy"
    sell = "sell"


class OrderType(str, enum.Enum):
    limit = "limit"
    market = "market"
    post_only = "post_only"
    ioc = "ioc"
    fok = "fok"


class OrderStatus(str, enum.Enum):
    created = "created"
    submitted = "submitted"
    open = "open"
    partially_filled = "partially_filled"
    filled = "filled"
    cancel_pending = "cancel_pending"
    cancelled = "cancelled"
    rejected = "rejected"
    expired = "expired"
    failed = "failed"


class RiskSeverity(str, enum.Enum):
    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"


class PositionSide(str, enum.Enum):
    long = "long"
    short = "short"
    flat = "flat"


def uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(PortableUUID(), primary_key=True, default=uuid.uuid4)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class Role(Base, TimestampMixin):
    __tablename__ = "roles"
    id = uuid_pk()
    name: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    is_system_role: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class Permission(Base, TimestampMixin):
    __tablename__ = "permissions"
    id = uuid_pk()
    name: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    resource: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (UniqueConstraint("resource", "action", name="uq_permissions_resource_action"),)


class User(Base, TimestampMixin):
    __tablename__ = "users"
    id = uuid_pk()
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    password_hash: Mapped[str | None] = mapped_column(Text)
    status: Mapped[UserStatus] = mapped_column(Enum(UserStatus, native_enum=False), default=UserStatus.pending, nullable=False)
    mfa_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UserRole(Base):
    __tablename__ = "user_roles"
    user_id: Mapped[uuid.UUID] = mapped_column(PortableUUID(), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    role_id: Mapped[uuid.UUID] = mapped_column(PortableUUID(), ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True)
    granted_by: Mapped[uuid.UUID | None] = mapped_column(PortableUUID(), ForeignKey("users.id", ondelete="SET NULL"))
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class RolePermission(Base):
    __tablename__ = "role_permissions"
    role_id: Mapped[uuid.UUID] = mapped_column(PortableUUID(), ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True)
    permission_id: Mapped[uuid.UUID] = mapped_column(PortableUUID(), ForeignKey("permissions.id", ondelete="CASCADE"), primary_key=True)
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class BotConfig(Base, TimestampMixin):
    __tablename__ = "bot_configs"
    id = uuid_pk()
    name: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    status: Mapped[BotStatus] = mapped_column(Enum(BotStatus, native_enum=False), default=BotStatus.draft, nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(PortableJSON, default=dict, nullable=False)
    risk_limits: Mapped[dict[str, Any]] = mapped_column(PortableJSON, default=dict, nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(PortableUUID(), ForeignKey("users.id", ondelete="SET NULL"))
    approved_by: Mapped[uuid.UUID | None] = mapped_column(PortableUUID(), ForeignKey("users.id", ondelete="SET NULL"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("name", "version", name="uq_bot_configs_name_version"), Index("idx_bot_configs_status", "status"))


class ExchangeAccount(Base, TimestampMixin):
    __tablename__ = "exchange_accounts"
    id = uuid_pk()
    exchange_name: Mapped[str] = mapped_column(Text, nullable=False)
    account_alias: Mapped[str] = mapped_column(Text, nullable=False)
    environment: Mapped[str] = mapped_column(Text, nullable=False)
    api_key_ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    api_secret_ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    passphrase_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
    encryption_key_id: Mapped[str] = mapped_column(Text, nullable=False)
    permissions: Mapped[list[Any]] = mapped_column(PortableJSON, default=list, nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    __table_args__ = (UniqueConstraint("exchange_name", "account_alias", "environment", name="uq_exchange_accounts_alias"),)


class TradingPair(Base, TimestampMixin):
    __tablename__ = "trading_pairs"
    id = uuid_pk()
    exchange_name: Mapped[str] = mapped_column(Text, nullable=False)
    base_asset: Mapped[str] = mapped_column(Text, nullable=False)
    quote_asset: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_symbol: Mapped[str] = mapped_column(Text, nullable=False)
    venue_symbol: Mapped[str] = mapped_column(Text, nullable=False)
    price_precision: Mapped[int] = mapped_column(Integer, nullable=False)
    quantity_precision: Mapped[int] = mapped_column(Integer, nullable=False)
    min_order_size: Mapped[float | None] = mapped_column(Numeric(38, 18))
    min_notional: Mapped[float | None] = mapped_column(Numeric(38, 18))
    tick_size: Mapped[float | None] = mapped_column(Numeric(38, 18))
    lot_size: Mapped[float | None] = mapped_column(Numeric(38, 18))
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    __table_args__ = (UniqueConstraint("exchange_name", "venue_symbol", name="uq_trading_pairs_exchange_symbol"),)


class Order(Base, TimestampMixin):
    __tablename__ = "orders"
    id = uuid_pk()
    client_order_id: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    exchange_order_id: Mapped[str | None] = mapped_column(Text)
    exchange_account_id: Mapped[uuid.UUID] = mapped_column(PortableUUID(), ForeignKey("exchange_accounts.id"), nullable=False)
    trading_pair_id: Mapped[uuid.UUID] = mapped_column(PortableUUID(), ForeignKey("trading_pairs.id"), nullable=False)
    bot_config_id: Mapped[uuid.UUID | None] = mapped_column(PortableUUID(), ForeignKey("bot_configs.id"))
    side: Mapped[OrderSide] = mapped_column(Enum(OrderSide, native_enum=False), nullable=False)
    order_type: Mapped[OrderType] = mapped_column(Enum(OrderType, native_enum=False), nullable=False)
    status: Mapped[OrderStatus] = mapped_column(Enum(OrderStatus, native_enum=False), default=OrderStatus.created, nullable=False)
    price: Mapped[float | None] = mapped_column(Numeric(38, 18))
    quantity: Mapped[float] = mapped_column(Numeric(38, 18), nullable=False)
    filled_quantity: Mapped[float] = mapped_column(Numeric(38, 18), default=0, nullable=False)
    average_fill_price: Mapped[float | None] = mapped_column(Numeric(38, 18))
    fee_amount: Mapped[float] = mapped_column(Numeric(38, 18), default=0, nullable=False)
    fee_asset: Mapped[str | None] = mapped_column(Text)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_exchange_update_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", PortableJSON, default=dict, nullable=False)


class Trade(Base):
    __tablename__ = "trades"
    id = uuid_pk()
    order_id: Mapped[uuid.UUID] = mapped_column(PortableUUID(), ForeignKey("orders.id"), nullable=False)
    exchange_trade_id: Mapped[str] = mapped_column(Text, nullable=False)
    exchange_account_id: Mapped[uuid.UUID] = mapped_column(PortableUUID(), ForeignKey("exchange_accounts.id"), nullable=False)
    trading_pair_id: Mapped[uuid.UUID] = mapped_column(PortableUUID(), ForeignKey("trading_pairs.id"), nullable=False)
    side: Mapped[OrderSide] = mapped_column(Enum(OrderSide, native_enum=False), nullable=False)
    price: Mapped[float] = mapped_column(Numeric(38, 18), nullable=False)
    quantity: Mapped[float] = mapped_column(Numeric(38, 18), nullable=False)
    fee_amount: Mapped[float] = mapped_column(Numeric(38, 18), default=0, nullable=False)
    fee_asset: Mapped[str | None] = mapped_column(Text)
    traded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", PortableJSON, default=dict, nullable=False)
    __table_args__ = (UniqueConstraint("exchange_account_id", "exchange_trade_id", name="uq_trades_exchange_trade"),)


class Position(Base, TimestampMixin):
    __tablename__ = "positions"
    id = uuid_pk()
    exchange_account_id: Mapped[uuid.UUID] = mapped_column(PortableUUID(), ForeignKey("exchange_accounts.id"), nullable=False)
    trading_pair_id: Mapped[uuid.UUID] = mapped_column(PortableUUID(), ForeignKey("trading_pairs.id"), nullable=False)
    asset: Mapped[str] = mapped_column(Text, nullable=False)
    side: Mapped[PositionSide] = mapped_column(Enum(PositionSide, native_enum=False), default=PositionSide.flat, nullable=False)
    quantity: Mapped[float] = mapped_column(Numeric(38, 18), default=0, nullable=False)
    average_entry_price: Mapped[float | None] = mapped_column(Numeric(38, 18))
    realized_pnl: Mapped[float] = mapped_column(Numeric(38, 18), default=0, nullable=False)
    unrealized_pnl: Mapped[float] = mapped_column(Numeric(38, 18), default=0, nullable=False)
    mark_price: Mapped[float | None] = mapped_column(Numeric(38, 18))
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    __table_args__ = (UniqueConstraint("exchange_account_id", "trading_pair_id", "asset", name="uq_positions_account_pair_asset"),)


class InventorySnapshot(Base):
    __tablename__ = "inventory_snapshots"
    id = uuid_pk()
    exchange_account_id: Mapped[uuid.UUID] = mapped_column(PortableUUID(), ForeignKey("exchange_accounts.id"), nullable=False)
    bot_config_id: Mapped[uuid.UUID | None] = mapped_column(PortableUUID(), ForeignKey("bot_configs.id"))
    asset: Mapped[str] = mapped_column(Text, nullable=False)
    total_balance: Mapped[float] = mapped_column(Numeric(38, 18), nullable=False)
    available_balance: Mapped[float] = mapped_column(Numeric(38, 18), nullable=False)
    reserved_balance: Mapped[float] = mapped_column(Numeric(38, 18), default=0, nullable=False)
    valuation_asset: Mapped[str] = mapped_column(Text, nullable=False)
    valuation_price: Mapped[float | None] = mapped_column(Numeric(38, 18))
    valuation_amount: Mapped[float | None] = mapped_column(Numeric(38, 18))
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", PortableJSON, default=dict, nullable=False)


class MarketData(Base):
    __tablename__ = "market_data"
    id = uuid_pk()
    exchange_name: Mapped[str] = mapped_column(Text, nullable=False)
    trading_pair_id: Mapped[uuid.UUID] = mapped_column(PortableUUID(), ForeignKey("trading_pairs.id"), nullable=False)
    data_type: Mapped[str] = mapped_column(Text, nullable=False)
    bid_price: Mapped[float | None] = mapped_column(Numeric(38, 18))
    bid_size: Mapped[float | None] = mapped_column(Numeric(38, 18))
    ask_price: Mapped[float | None] = mapped_column(Numeric(38, 18))
    ask_size: Mapped[float | None] = mapped_column(Numeric(38, 18))
    last_price: Mapped[float | None] = mapped_column(Numeric(38, 18))
    volume_24h: Mapped[float | None] = mapped_column(Numeric(38, 18))
    source_sequence: Mapped[str | None] = mapped_column(Text)
    source_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(PortableJSON, default=dict, nullable=False)


class RiskEvent(Base):
    __tablename__ = "risk_events"
    id = uuid_pk()
    severity: Mapped[RiskSeverity] = mapped_column(Enum(RiskSeverity, native_enum=False), nullable=False)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    source_component: Mapped[str] = mapped_column(Text, nullable=False)
    exchange_account_id: Mapped[uuid.UUID | None] = mapped_column(PortableUUID(), ForeignKey("exchange_accounts.id"))
    trading_pair_id: Mapped[uuid.UUID | None] = mapped_column(PortableUUID(), ForeignKey("trading_pairs.id"))
    bot_config_id: Mapped[uuid.UUID | None] = mapped_column(PortableUUID(), ForeignKey("bot_configs.id"))
    message: Mapped[str] = mapped_column(Text, nullable=False)
    limit_name: Mapped[str | None] = mapped_column(Text)
    observed_value: Mapped[float | None] = mapped_column(Numeric(38, 18))
    limit_value: Mapped[float | None] = mapped_column(Numeric(38, 18))
    is_circuit_breaker_triggered: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_kill_switch_triggered: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", PortableJSON, default=dict, nullable=False)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id = uuid_pk()
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(PortableUUID(), ForeignKey("users.id"))
    actor_service: Mapped[str | None] = mapped_column(Text)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    resource_type: Mapped[str] = mapped_column(Text, nullable=False)
    resource_id: Mapped[uuid.UUID | None] = mapped_column(PortableUUID())
    request_id: Mapped[str | None] = mapped_column(Text)
    correlation_id: Mapped[str | None] = mapped_column(Text)
    ip_address: Mapped[str | None] = mapped_column(INET().with_variant(String(64), "sqlite"))
    user_agent: Mapped[str | None] = mapped_column(Text)
    before_state: Mapped[dict[str, Any] | None] = mapped_column(PortableJSON)
    after_state: Mapped[dict[str, Any] | None] = mapped_column(PortableJSON)
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", PortableJSON, default=dict, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class SystemHealth(Base):
    __tablename__ = "system_health"
    id = uuid_pk()
    component: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    host: Mapped[str | None] = mapped_column(Text)
    version: Mapped[str | None] = mapped_column(Text)
    latency_ms: Mapped[float | None] = mapped_column(Numeric(20, 6))
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", PortableJSON, default=dict, nullable=False)


class PnlHistory(Base):
    __tablename__ = "pnl_history"
    id = uuid_pk()
    exchange_account_id: Mapped[uuid.UUID] = mapped_column(PortableUUID(), ForeignKey("exchange_accounts.id"), nullable=False)
    trading_pair_id: Mapped[uuid.UUID | None] = mapped_column(PortableUUID(), ForeignKey("trading_pairs.id"))
    bot_config_id: Mapped[uuid.UUID | None] = mapped_column(PortableUUID(), ForeignKey("bot_configs.id"))
    realized_pnl: Mapped[float] = mapped_column(Numeric(38, 18), default=0, nullable=False)
    unrealized_pnl: Mapped[float] = mapped_column(Numeric(38, 18), default=0, nullable=False)
    fees_paid: Mapped[float] = mapped_column(Numeric(38, 18), default=0, nullable=False)
    funding_paid: Mapped[float] = mapped_column(Numeric(38, 18), default=0, nullable=False)
    valuation_asset: Mapped[str] = mapped_column(Text, nullable=False)
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", PortableJSON, default=dict, nullable=False)


class LiquidityMetric(Base):
    __tablename__ = "liquidity_metrics"
    id = uuid_pk()
    exchange_name: Mapped[str] = mapped_column(Text, nullable=False)
    trading_pair_id: Mapped[uuid.UUID] = mapped_column(PortableUUID(), ForeignKey("trading_pairs.id"), nullable=False)
    spread_bps: Mapped[float | None] = mapped_column(Numeric(20, 8))
    top_of_book_depth: Mapped[float | None] = mapped_column(Numeric(38, 18))
    depth_1pct: Mapped[float | None] = mapped_column(Numeric(38, 18))
    depth_5pct: Mapped[float | None] = mapped_column(Numeric(38, 18))
    imbalance_ratio: Mapped[float | None] = mapped_column(Numeric(20, 8))
    slippage_bps: Mapped[float | None] = mapped_column(Numeric(20, 8))
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", PortableJSON, default=dict, nullable=False)


class VolatilityMetric(Base):
    __tablename__ = "volatility_metrics"
    id = uuid_pk()
    exchange_name: Mapped[str] = mapped_column(Text, nullable=False)
    trading_pair_id: Mapped[uuid.UUID] = mapped_column(PortableUUID(), ForeignKey("trading_pairs.id"), nullable=False)
    window_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    realized_volatility: Mapped[float | None] = mapped_column(Numeric(20, 10))
    implied_volatility: Mapped[float | None] = mapped_column(Numeric(20, 10))
    high_price: Mapped[float | None] = mapped_column(Numeric(38, 18))
    low_price: Mapped[float | None] = mapped_column(Numeric(38, 18))
    open_price: Mapped[float | None] = mapped_column(Numeric(38, 18))
    close_price: Mapped[float | None] = mapped_column(Numeric(38, 18))
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", PortableJSON, default=dict, nullable=False)
