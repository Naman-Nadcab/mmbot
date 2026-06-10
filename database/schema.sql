-- PostgreSQL schema foundation. Defines durable data structures only; no trading logic.

CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS citext;

CREATE TYPE user_status AS ENUM ('active', 'inactive', 'locked', 'pending');
CREATE TYPE bot_status AS ENUM ('draft', 'disabled', 'enabled', 'suspended', 'archived');
CREATE TYPE order_side AS ENUM ('buy', 'sell');
CREATE TYPE order_type AS ENUM ('limit', 'market', 'post_only', 'ioc', 'fok');
CREATE TYPE order_status AS ENUM ('created', 'submitted', 'open', 'partially_filled', 'filled', 'cancel_pending', 'cancelled', 'rejected', 'expired', 'failed');
CREATE TYPE alert_severity AS ENUM ('info', 'warning', 'critical', 'emergency');
CREATE TYPE alert_status AS ENUM ('open', 'acknowledged', 'resolved', 'suppressed');
CREATE TYPE risk_severity AS ENUM ('low', 'medium', 'high', 'critical');
CREATE TYPE health_status AS ENUM ('healthy', 'degraded', 'unhealthy', 'unknown');
CREATE TYPE position_side AS ENUM ('long', 'short', 'flat');

CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TABLE roles (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    description TEXT,
    is_system_role BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_roles_name UNIQUE (name),
    CONSTRAINT chk_roles_name_not_blank CHECK (btrim(name) <> '')
);

CREATE TABLE permissions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    resource TEXT NOT NULL,
    action TEXT NOT NULL,
    description TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_permissions_name UNIQUE (name),
    CONSTRAINT uq_permissions_resource_action UNIQUE (resource, action),
    CONSTRAINT chk_permissions_resource_not_blank CHECK (btrim(resource) <> ''),
    CONSTRAINT chk_permissions_action_not_blank CHECK (btrim(action) <> '')
);

CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email CITEXT NOT NULL,
    display_name TEXT NOT NULL,
    password_hash TEXT,
    status user_status NOT NULL DEFAULT 'pending',
    mfa_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    last_login_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_users_email UNIQUE (email),
    CONSTRAINT chk_users_display_name_not_blank CHECK (btrim(display_name) <> '')
);

CREATE TABLE user_roles (
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role_id UUID NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    granted_by UUID REFERENCES users(id) ON DELETE SET NULL,
    granted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, role_id)
);

CREATE TABLE role_permissions (
    role_id UUID NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    permission_id UUID NOT NULL REFERENCES permissions(id) ON DELETE CASCADE,
    granted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (role_id, permission_id)
);

CREATE TABLE bot_configs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    status bot_status NOT NULL DEFAULT 'draft',
    config JSONB NOT NULL DEFAULT '{}'::jsonb,
    risk_limits JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_by UUID REFERENCES users(id) ON DELETE SET NULL,
    approved_by UUID REFERENCES users(id) ON DELETE SET NULL,
    approved_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_bot_configs_name_version UNIQUE (name, version),
    CONSTRAINT chk_bot_configs_name_not_blank CHECK (btrim(name) <> ''),
    CONSTRAINT chk_bot_configs_version_positive CHECK (version > 0)
);

CREATE TABLE exchange_accounts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    exchange_name TEXT NOT NULL,
    account_alias TEXT NOT NULL,
    environment TEXT NOT NULL,
    api_key_ciphertext BYTEA NOT NULL,
    api_secret_ciphertext BYTEA NOT NULL,
    passphrase_ciphertext BYTEA,
    encryption_key_id TEXT NOT NULL,
    permissions JSONB NOT NULL DEFAULT '[]'::jsonb,
    is_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    created_by UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_exchange_accounts_alias UNIQUE (exchange_name, account_alias, environment),
    CONSTRAINT chk_exchange_accounts_exchange_not_blank CHECK (btrim(exchange_name) <> ''),
    CONSTRAINT chk_exchange_accounts_alias_not_blank CHECK (btrim(account_alias) <> ''),
    CONSTRAINT chk_exchange_accounts_environment CHECK (environment IN ('sandbox', 'staging', 'production'))
);

CREATE TABLE trading_pairs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    exchange_name TEXT NOT NULL,
    base_asset TEXT NOT NULL,
    quote_asset TEXT NOT NULL,
    normalized_symbol TEXT NOT NULL,
    venue_symbol TEXT NOT NULL,
    price_precision INTEGER NOT NULL,
    quantity_precision INTEGER NOT NULL,
    min_order_size NUMERIC(38, 18),
    min_notional NUMERIC(38, 18),
    tick_size NUMERIC(38, 18),
    lot_size NUMERIC(38, 18),
    is_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_trading_pairs_exchange_symbol UNIQUE (exchange_name, venue_symbol),
    CONSTRAINT uq_trading_pairs_normalized_exchange UNIQUE (exchange_name, normalized_symbol),
    CONSTRAINT chk_trading_pairs_assets_different CHECK (base_asset <> quote_asset),
    CONSTRAINT chk_trading_pairs_precision_non_negative CHECK (price_precision >= 0 AND quantity_precision >= 0)
);

CREATE TABLE orders (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_order_id TEXT NOT NULL,
    exchange_order_id TEXT,
    exchange_account_id UUID NOT NULL REFERENCES exchange_accounts(id) ON DELETE RESTRICT,
    trading_pair_id UUID NOT NULL REFERENCES trading_pairs(id) ON DELETE RESTRICT,
    bot_config_id UUID REFERENCES bot_configs(id) ON DELETE SET NULL,
    side order_side NOT NULL,
    order_type order_type NOT NULL,
    status order_status NOT NULL DEFAULT 'created',
    price NUMERIC(38, 18),
    quantity NUMERIC(38, 18) NOT NULL,
    filled_quantity NUMERIC(38, 18) NOT NULL DEFAULT 0,
    average_fill_price NUMERIC(38, 18),
    fee_amount NUMERIC(38, 18) NOT NULL DEFAULT 0,
    fee_asset TEXT,
    submitted_at TIMESTAMPTZ,
    last_exchange_update_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT uq_orders_client_order_id UNIQUE (client_order_id),
    CONSTRAINT uq_orders_exchange_order UNIQUE (exchange_account_id, exchange_order_id),
    CONSTRAINT chk_orders_quantity_positive CHECK (quantity > 0),
    CONSTRAINT chk_orders_filled_quantity_valid CHECK (filled_quantity >= 0 AND filled_quantity <= quantity),
    CONSTRAINT chk_orders_price_required_for_limit CHECK (order_type NOT IN ('limit', 'post_only') OR price IS NOT NULL),
    CONSTRAINT chk_orders_price_positive CHECK (price IS NULL OR price > 0),
    CONSTRAINT chk_orders_fee_non_negative CHECK (fee_amount >= 0)
);

CREATE TABLE trades (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id UUID NOT NULL REFERENCES orders(id) ON DELETE RESTRICT,
    exchange_trade_id TEXT NOT NULL,
    exchange_account_id UUID NOT NULL REFERENCES exchange_accounts(id) ON DELETE RESTRICT,
    trading_pair_id UUID NOT NULL REFERENCES trading_pairs(id) ON DELETE RESTRICT,
    side order_side NOT NULL,
    price NUMERIC(38, 18) NOT NULL,
    quantity NUMERIC(38, 18) NOT NULL,
    fee_amount NUMERIC(38, 18) NOT NULL DEFAULT 0,
    fee_asset TEXT,
    traded_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT uq_trades_exchange_trade UNIQUE (exchange_account_id, exchange_trade_id),
    CONSTRAINT chk_trades_price_positive CHECK (price > 0),
    CONSTRAINT chk_trades_quantity_positive CHECK (quantity > 0),
    CONSTRAINT chk_trades_fee_non_negative CHECK (fee_amount >= 0)
);

CREATE TABLE positions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    exchange_account_id UUID NOT NULL REFERENCES exchange_accounts(id) ON DELETE RESTRICT,
    trading_pair_id UUID NOT NULL REFERENCES trading_pairs(id) ON DELETE RESTRICT,
    asset TEXT NOT NULL,
    side position_side NOT NULL DEFAULT 'flat',
    quantity NUMERIC(38, 18) NOT NULL DEFAULT 0,
    average_entry_price NUMERIC(38, 18),
    realized_pnl NUMERIC(38, 18) NOT NULL DEFAULT 0,
    unrealized_pnl NUMERIC(38, 18) NOT NULL DEFAULT 0,
    mark_price NUMERIC(38, 18),
    as_of TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_positions_account_pair_asset UNIQUE (exchange_account_id, trading_pair_id, asset),
    CONSTRAINT chk_positions_quantity_non_negative CHECK (quantity >= 0)
);

CREATE TABLE inventory_snapshots (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    exchange_account_id UUID NOT NULL REFERENCES exchange_accounts(id) ON DELETE RESTRICT,
    bot_config_id UUID REFERENCES bot_configs(id) ON DELETE SET NULL,
    asset TEXT NOT NULL,
    total_balance NUMERIC(38, 18) NOT NULL,
    available_balance NUMERIC(38, 18) NOT NULL,
    reserved_balance NUMERIC(38, 18) NOT NULL DEFAULT 0,
    valuation_asset TEXT NOT NULL,
    valuation_price NUMERIC(38, 18),
    valuation_amount NUMERIC(38, 18),
    captured_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT chk_inventory_balances_non_negative CHECK (total_balance >= 0 AND available_balance >= 0 AND reserved_balance >= 0),
    CONSTRAINT chk_inventory_available_not_exceed_total CHECK (available_balance <= total_balance)
);

CREATE TABLE market_data (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    exchange_name TEXT NOT NULL,
    trading_pair_id UUID NOT NULL REFERENCES trading_pairs(id) ON DELETE RESTRICT,
    data_type TEXT NOT NULL,
    bid_price NUMERIC(38, 18),
    bid_size NUMERIC(38, 18),
    ask_price NUMERIC(38, 18),
    ask_size NUMERIC(38, 18),
    last_price NUMERIC(38, 18),
    volume_24h NUMERIC(38, 18),
    source_sequence TEXT,
    source_timestamp TIMESTAMPTZ NOT NULL,
    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT chk_market_data_type CHECK (data_type IN ('ticker', 'order_book', 'trade', 'candle')),
    CONSTRAINT chk_market_data_spread_valid CHECK (bid_price IS NULL OR ask_price IS NULL OR bid_price <= ask_price)
);

CREATE TABLE risk_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    severity risk_severity NOT NULL,
    event_type TEXT NOT NULL,
    source_component TEXT NOT NULL,
    exchange_account_id UUID REFERENCES exchange_accounts(id) ON DELETE SET NULL,
    trading_pair_id UUID REFERENCES trading_pairs(id) ON DELETE SET NULL,
    bot_config_id UUID REFERENCES bot_configs(id) ON DELETE SET NULL,
    message TEXT NOT NULL,
    limit_name TEXT,
    observed_value NUMERIC(38, 18),
    limit_value NUMERIC(38, 18),
    is_circuit_breaker_triggered BOOLEAN NOT NULL DEFAULT FALSE,
    is_kill_switch_triggered BOOLEAN NOT NULL DEFAULT FALSE,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT chk_risk_events_type_not_blank CHECK (btrim(event_type) <> ''),
    CONSTRAINT chk_risk_events_message_not_blank CHECK (btrim(message) <> '')
);

CREATE TABLE alerts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    severity alert_severity NOT NULL,
    status alert_status NOT NULL DEFAULT 'open',
    channel TEXT NOT NULL,
    title TEXT NOT NULL,
    message TEXT NOT NULL,
    risk_event_id UUID REFERENCES risk_events(id) ON DELETE SET NULL,
    acknowledged_by UUID REFERENCES users(id) ON DELETE SET NULL,
    acknowledged_at TIMESTAMPTZ,
    resolved_by UUID REFERENCES users(id) ON DELETE SET NULL,
    resolved_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT chk_alerts_channel CHECK (channel IN ('telegram', 'email', 'pager', 'dashboard', 'webhook')),
    CONSTRAINT chk_alerts_title_not_blank CHECK (btrim(title) <> ''),
    CONSTRAINT chk_alerts_message_not_blank CHECK (btrim(message) <> '')
);

CREATE TABLE audit_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    actor_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    actor_service TEXT,
    action TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    resource_id UUID,
    request_id TEXT,
    correlation_id TEXT,
    ip_address INET,
    user_agent TEXT,
    before_state JSONB,
    after_state JSONB,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_audit_logs_actor_present CHECK (actor_user_id IS NOT NULL OR actor_service IS NOT NULL),
    CONSTRAINT chk_audit_logs_action_not_blank CHECK (btrim(action) <> ''),
    CONSTRAINT chk_audit_logs_resource_type_not_blank CHECK (btrim(resource_type) <> '')
);

CREATE TABLE system_health (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    component TEXT NOT NULL,
    status health_status NOT NULL,
    host TEXT,
    version TEXT,
    latency_ms NUMERIC(20, 6),
    cpu_percent NUMERIC(8, 4),
    memory_percent NUMERIC(8, 4),
    disk_percent NUMERIC(8, 4),
    checked_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT chk_system_health_component_not_blank CHECK (btrim(component) <> ''),
    CONSTRAINT chk_system_health_latency_non_negative CHECK (latency_ms IS NULL OR latency_ms >= 0)
);

CREATE TABLE pnl_history (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    exchange_account_id UUID NOT NULL REFERENCES exchange_accounts(id) ON DELETE RESTRICT,
    trading_pair_id UUID REFERENCES trading_pairs(id) ON DELETE SET NULL,
    bot_config_id UUID REFERENCES bot_configs(id) ON DELETE SET NULL,
    realized_pnl NUMERIC(38, 18) NOT NULL DEFAULT 0,
    unrealized_pnl NUMERIC(38, 18) NOT NULL DEFAULT 0,
    fees_paid NUMERIC(38, 18) NOT NULL DEFAULT 0,
    funding_paid NUMERIC(38, 18) NOT NULL DEFAULT 0,
    valuation_asset TEXT NOT NULL,
    period_start TIMESTAMPTZ NOT NULL,
    period_end TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT chk_pnl_history_period_valid CHECK (period_end > period_start),
    CONSTRAINT chk_pnl_history_fees_non_negative CHECK (fees_paid >= 0)
);

CREATE TABLE liquidity_metrics (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    exchange_name TEXT NOT NULL,
    trading_pair_id UUID NOT NULL REFERENCES trading_pairs(id) ON DELETE RESTRICT,
    spread_bps NUMERIC(20, 8),
    top_of_book_depth NUMERIC(38, 18),
    depth_1pct NUMERIC(38, 18),
    depth_5pct NUMERIC(38, 18),
    imbalance_ratio NUMERIC(20, 8),
    slippage_bps NUMERIC(20, 8),
    captured_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT chk_liquidity_metrics_spread_non_negative CHECK (spread_bps IS NULL OR spread_bps >= 0),
    CONSTRAINT chk_liquidity_metrics_depth_non_negative CHECK ((top_of_book_depth IS NULL OR top_of_book_depth >= 0) AND (depth_1pct IS NULL OR depth_1pct >= 0) AND (depth_5pct IS NULL OR depth_5pct >= 0))
);

CREATE TABLE volatility_metrics (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    exchange_name TEXT NOT NULL,
    trading_pair_id UUID NOT NULL REFERENCES trading_pairs(id) ON DELETE RESTRICT,
    window_seconds INTEGER NOT NULL,
    realized_volatility NUMERIC(20, 10),
    implied_volatility NUMERIC(20, 10),
    high_price NUMERIC(38, 18),
    low_price NUMERIC(38, 18),
    open_price NUMERIC(38, 18),
    close_price NUMERIC(38, 18),
    captured_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT chk_volatility_metrics_window_positive CHECK (window_seconds > 0),
    CONSTRAINT chk_volatility_metrics_non_negative CHECK ((realized_volatility IS NULL OR realized_volatility >= 0) AND (implied_volatility IS NULL OR implied_volatility >= 0))
);

CREATE TRIGGER trg_roles_updated_at BEFORE UPDATE ON roles FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER trg_permissions_updated_at BEFORE UPDATE ON permissions FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER trg_users_updated_at BEFORE UPDATE ON users FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER trg_bot_configs_updated_at BEFORE UPDATE ON bot_configs FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER trg_exchange_accounts_updated_at BEFORE UPDATE ON exchange_accounts FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER trg_trading_pairs_updated_at BEFORE UPDATE ON trading_pairs FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER trg_orders_updated_at BEFORE UPDATE ON orders FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER trg_positions_updated_at BEFORE UPDATE ON positions FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER trg_alerts_updated_at BEFORE UPDATE ON alerts FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE INDEX idx_users_status ON users(status);
CREATE INDEX idx_user_roles_role_id ON user_roles(role_id);
CREATE INDEX idx_role_permissions_permission_id ON role_permissions(permission_id);
CREATE INDEX idx_bot_configs_status ON bot_configs(status);
CREATE INDEX idx_exchange_accounts_exchange_enabled ON exchange_accounts(exchange_name, is_enabled);
CREATE INDEX idx_trading_pairs_enabled ON trading_pairs(is_enabled);
CREATE INDEX idx_orders_account_pair_status ON orders(exchange_account_id, trading_pair_id, status);
CREATE INDEX idx_orders_created_at ON orders(created_at DESC);
CREATE INDEX idx_orders_exchange_order_id ON orders(exchange_order_id) WHERE exchange_order_id IS NOT NULL;
CREATE INDEX idx_trades_order_id ON trades(order_id);
CREATE INDEX idx_trades_traded_at ON trades(traded_at DESC);
CREATE INDEX idx_positions_account_pair ON positions(exchange_account_id, trading_pair_id);
CREATE INDEX idx_inventory_snapshots_account_asset_time ON inventory_snapshots(exchange_account_id, asset, captured_at DESC);
CREATE INDEX idx_market_data_pair_type_time ON market_data(trading_pair_id, data_type, source_timestamp DESC);
CREATE INDEX idx_risk_events_severity_time ON risk_events(severity, occurred_at DESC);
CREATE INDEX idx_risk_events_breakers ON risk_events(is_circuit_breaker_triggered, is_kill_switch_triggered) WHERE is_circuit_breaker_triggered OR is_kill_switch_triggered;
CREATE INDEX idx_alerts_status_severity ON alerts(status, severity, created_at DESC);
CREATE INDEX idx_audit_logs_actor_time ON audit_logs(actor_user_id, occurred_at DESC);
CREATE INDEX idx_audit_logs_resource ON audit_logs(resource_type, resource_id, occurred_at DESC);
CREATE INDEX idx_audit_logs_correlation_id ON audit_logs(correlation_id) WHERE correlation_id IS NOT NULL;
CREATE INDEX idx_system_health_component_time ON system_health(component, checked_at DESC);
CREATE INDEX idx_pnl_history_account_period ON pnl_history(exchange_account_id, period_start, period_end);
CREATE INDEX idx_liquidity_metrics_pair_time ON liquidity_metrics(trading_pair_id, captured_at DESC);
CREATE INDEX idx_volatility_metrics_pair_window_time ON volatility_metrics(trading_pair_id, window_seconds, captured_at DESC);
CREATE INDEX idx_bot_configs_config_gin ON bot_configs USING GIN (config);
CREATE INDEX idx_market_data_payload_gin ON market_data USING GIN (payload);
CREATE INDEX idx_risk_events_metadata_gin ON risk_events USING GIN (metadata);
CREATE INDEX idx_audit_logs_metadata_gin ON audit_logs USING GIN (metadata);
