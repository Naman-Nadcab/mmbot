from pathlib import Path

from mmbot.api.main import create_app


def test_dashboard_api_paths_are_registered_backend_routes():
    app = create_app()
    route_paths = {getattr(route, "path", "") for route in app.routes}
    dashboard_js = Path("services/dashboard/app.js").read_text(encoding="utf-8")
    expected = {
        "/health",
        "/version",
        "/ready",
        "/admin/config/{domain}",
        "/operations/engines",
        "/operations/infrastructure",
        "/operations/exchanges",
        "/operations/orders",
        "/operations/trades",
        "/operations/positions",
        "/operations/inventory",
        "/operations/pnl",
        "/operations/risk-events",
        "/operations/audit-logs",
        "/operations/runtime-events",
        "/operations/volume",
        "/operations/kill-switch",
        "/admin/coinstore/accounts",
        "/admin/coinstore/health",
        "/admin/coinstore/balance-sync",
        "/admin/coinstore/accounts/{account_id}/status",
        "/admin/emergency/cancel-all-orders",
        "/admin/emergency/disable-trading",
        "/admin/emergency/enable-trading",
        "/admin/emergency/close-positions",
        "/admin/emergency/runtime-restart",
        "/admin/emergency/shutdown",
        "/admin/strategy/command",
        "/ws/operations",
    }
    missing = expected - route_paths
    assert not missing
    for path in ("/operations/runtime-events", "/admin/coinstore/health", "/admin/emergency/close-positions", "/ws/operations"):
        assert path in dashboard_js
