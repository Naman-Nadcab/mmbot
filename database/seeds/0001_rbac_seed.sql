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
    ('risk:read', 'risk', 'read', 'Read risk state'),
    ('risk:write', 'risk', 'write', 'Modify risk controls'),
    ('audit:read', 'audit', 'read', 'Read audit logs')
ON CONFLICT (name) DO NOTHING;

INSERT INTO role_permissions(role_id, permission_id)
SELECT r.id, p.id
FROM roles r
CROSS JOIN permissions p
WHERE r.name = 'platform_admin'
ON CONFLICT DO NOTHING;
