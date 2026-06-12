CREATE TABLE IF NOT EXISTS runtime_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_type TEXT NOT NULL,
    source_component TEXT NOT NULL,
    status TEXT NOT NULL,
    command_id TEXT,
    config_domain TEXT,
    config_version INTEGER,
    correlation_id TEXT,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    acknowledged_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT chk_runtime_events_type_not_blank CHECK (btrim(event_type) <> ''),
    CONSTRAINT chk_runtime_events_source_not_blank CHECK (btrim(source_component) <> ''),
    CONSTRAINT chk_runtime_events_status_not_blank CHECK (btrim(status) <> '')
);

CREATE INDEX IF NOT EXISTS idx_runtime_events_created_at ON runtime_events(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_runtime_events_command_id ON runtime_events(command_id) WHERE command_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_runtime_events_type_status ON runtime_events(event_type, status);
CREATE INDEX IF NOT EXISTS idx_runtime_events_payload_gin ON runtime_events USING GIN (payload);

INSERT INTO permissions(name, resource, action, description) VALUES
    ('operations:read', 'operations', 'read', 'Read operations dashboard state'),
    ('trading:write', 'trading', 'write', 'Operate trading strategy controls'),
    ('incident:write', 'incident', 'write', 'Execute emergency response actions'),
    ('exchange:write', 'exchange', 'write', 'Manage exchange accounts and connectivity')
ON CONFLICT (name) DO UPDATE SET
    resource = EXCLUDED.resource,
    action = EXCLUDED.action,
    description = EXCLUDED.description;

INSERT INTO role_permissions(role_id, permission_id)
SELECT r.id, p.id
FROM roles r
JOIN permissions p ON p.name IN ('config:read', 'risk:read', 'risk:write', 'audit:read')
WHERE r.name = 'risk_manager'
ON CONFLICT DO NOTHING;

INSERT INTO role_permissions(role_id, permission_id)
SELECT r.id, p.id
FROM roles r
JOIN permissions p ON p.name IN ('config:read', 'config:write', 'trading:write')
WHERE r.name = 'trader_operator'
ON CONFLICT DO NOTHING;

INSERT INTO role_permissions(role_id, permission_id)
SELECT r.id, p.id
FROM roles r
JOIN permissions p ON p.name IN ('operations:read', 'risk:read', 'risk:write', 'incident:write', 'audit:read')
WHERE r.name = 'incident_responder'
ON CONFLICT DO NOTHING;
