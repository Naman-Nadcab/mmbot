ALTER TABLE exchange_accounts ADD COLUMN IF NOT EXISTS rest_connected BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE exchange_accounts ADD COLUMN IF NOT EXISTS websocket_connected BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE exchange_accounts ADD COLUMN IF NOT EXISTS private_ws_connected BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE exchange_accounts ADD COLUMN IF NOT EXISTS connection_status TEXT NOT NULL DEFAULT 'disconnected';
ALTER TABLE exchange_accounts ADD COLUMN IF NOT EXISTS last_tested_at TIMESTAMPTZ;
ALTER TABLE exchange_accounts ADD COLUMN IF NOT EXISTS last_success_at TIMESTAMPTZ;
ALTER TABLE exchange_accounts ADD COLUMN IF NOT EXISTS last_failure_at TIMESTAMPTZ;
ALTER TABLE exchange_accounts ADD COLUMN IF NOT EXISTS last_error_message TEXT;

CREATE INDEX IF NOT EXISTS idx_exchange_accounts_status ON exchange_accounts(exchange_name, connection_status, is_enabled);
