INSERT INTO roles(name, description, is_system_role) VALUES
    ('platform_admin', 'Full platform administration', TRUE),
    ('risk_manager', 'Risk control administration', TRUE),
    ('trader_operator', 'Trading operations user', TRUE),
    ('read_only_analyst', 'Read-only analytics user', TRUE),
    ('incident_responder', 'Incident response operator', TRUE),
    ('service_account', 'Service-to-service identity', TRUE)
ON CONFLICT (name) DO NOTHING;

INSERT INTO permissions(name, resource, action, description) VALUES
    ('config:read', 'config', 'read', 'Read runtime configuration'),
    ('config:write', 'config', 'write', 'Modify runtime configuration'),
    ('operations:read', 'operations', 'read', 'Read operations dashboard state'),
    ('risk:read', 'risk', 'read', 'Read risk state'),
    ('risk:write', 'risk', 'write', 'Modify risk controls'),
    ('trading:write', 'trading', 'write', 'Operate trading strategy controls'),
    ('incident:write', 'incident', 'write', 'Execute emergency response actions'),
    ('exchange:write', 'exchange', 'write', 'Manage exchange accounts and connectivity'),
    ('audit:read', 'audit', 'read', 'Read audit logs')
ON CONFLICT (name) DO NOTHING;

INSERT INTO role_permissions(role_id, permission_id)
SELECT r.id, p.id
FROM roles r
CROSS JOIN permissions p
WHERE r.name = 'platform_admin'
ON CONFLICT DO NOTHING;

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
