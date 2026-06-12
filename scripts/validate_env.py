#!/usr/bin/env python3
"""Validate required environment variables for the platform foundation."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple
from urllib.error import URLError
from urllib.request import urlopen

REQUIRED_KEYS = (
    "DATABASE_URL",
    "REDIS_URL",
    "JWT_SECRET",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
    "EXCHANGE_API_KEYS",
    "EXCHANGE_API_SECRETS",
    "SERVER_IP",
    "SERVER_PORT",
    "LOG_LEVEL",
    "APP_ENV",
)

REQUIRED_JWT_ROLES = {"platform_admin", "risk_manager", "trader_operator", "incident_responder"}
REQUIRED_JWT_PERMISSIONS = {"operations:read", "config:write", "risk:write", "incident:write"}
REQUIRED_OPENAPI_PATHS = (
    "/operations/runtime-config",
    "/operations/runtime-events",
    "/admin/strategy/command",
    "/operations/volume",
    "/admin/coinstore/accounts",
    "/admin/coinstore/health",
    "/admin/coinstore/balance-sync",
    "/admin/emergency/cancel-all-orders",
    "/admin/emergency/close-positions",
    "/admin/emergency/shutdown",
)

ALLOWED_APP_ENVS = {"development", "staging", "production", "test"}
ALLOWED_LOG_LEVELS = {"TRACE", "DEBUG", "INFO", "WARNING", "WARN", "ERROR", "CRITICAL"}
TEMPLATE_VALUE_PATTERNS = (
    re.compile(r"change[-_ ]?me", re.IGNORECASE),
    re.compile(r"sample[-_ ]?value", re.IGNORECASE),
    re.compile(r"^\$\{[A-Z0-9_]+\}$"),
)


def parse_env_file(path: Path) -> Dict[str, str]:
    values: Dict[str, str] = {}
    if not path.exists():
        raise FileNotFoundError(f"Environment file not found: {path}")
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"{path}:{line_number}: expected KEY=VALUE format")
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if not key:
            raise ValueError(f"{path}:{line_number}: empty environment key")
        values[key] = value
    return values


def merged_environment(env_file: Path | None) -> Dict[str, str]:
    values = dict(os.environ)
    if env_file:
        values.update(parse_env_file(env_file))
    return values


def is_template_value(value: str) -> bool:
    return any(pattern.search(value) for pattern in TEMPLATE_VALUE_PATTERNS)


def parse_json_object(name: str, value: str) -> Tuple[Dict[str, str] | None, str | None]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        return None, f"{name} must be a valid JSON object: {exc}"
    if not isinstance(parsed, dict) or not parsed:
        return None, f"{name} must be a non-empty JSON object keyed by exchange/account alias"
    bad_keys = [key for key in parsed if not isinstance(key, str) or not key.strip()]
    if bad_keys:
        return None, f"{name} contains empty or non-string keys"
    bad_values = [key for key, item in parsed.items() if not isinstance(item, str) or not item.strip()]
    if bad_values:
        return None, f"{name} contains empty or non-string values for keys: {', '.join(bad_values)}"
    return parsed, None


def validate(values: Dict[str, str], allow_template_values: bool) -> List[str]:
    errors: List[str] = []
    for key in REQUIRED_KEYS:
        value = values.get(key, "")
        if not value:
            errors.append(f"Missing required environment variable: {key}")
        elif not allow_template_values and is_template_value(value):
            errors.append(f"{key} contains a template value")

    app_env = values.get("APP_ENV", "")
    if app_env and app_env not in ALLOWED_APP_ENVS:
        errors.append(f"APP_ENV must be one of: {', '.join(sorted(ALLOWED_APP_ENVS))}")

    log_level = values.get("LOG_LEVEL", "").upper()
    if log_level and log_level not in ALLOWED_LOG_LEVELS:
        errors.append(f"LOG_LEVEL must be one of: {', '.join(sorted(ALLOWED_LOG_LEVELS))}")

    try:
        port = int(values.get("SERVER_PORT", ""))
        if not 1 <= port <= 65535:
            errors.append("SERVER_PORT must be between 1 and 65535")
    except ValueError:
        errors.append("SERVER_PORT must be an integer")

    jwt_secret = values.get("JWT_SECRET", "")
    if jwt_secret and not allow_template_values and len(jwt_secret) < 32:
        errors.append("JWT_SECRET must be at least 32 characters")

    exchange_keys_value = values.get("EXCHANGE_API_KEYS", "{}")
    exchange_secrets_value = values.get("EXCHANGE_API_SECRETS", "{}")
    if allow_template_values and (is_template_value(exchange_keys_value) or is_template_value(exchange_secrets_value)):
        return errors

    api_keys, key_error = parse_json_object("EXCHANGE_API_KEYS", exchange_keys_value)
    api_secrets, secret_error = parse_json_object("EXCHANGE_API_SECRETS", exchange_secrets_value)
    if key_error:
        errors.append(key_error)
    if secret_error:
        errors.append(secret_error)
    if api_keys is not None and api_secrets is not None:
        if set(api_keys) != set(api_secrets):
            missing_secrets = sorted(set(api_keys) - set(api_secrets))
            missing_keys = sorted(set(api_secrets) - set(api_keys))
            if missing_secrets:
                errors.append(f"EXCHANGE_API_SECRETS missing aliases: {', '.join(missing_secrets)}")
            if missing_keys:
                errors.append(f"EXCHANGE_API_KEYS missing aliases: {', '.join(missing_keys)}")
    return errors


def validate_release_files(root: Path) -> List[str]:
    errors: List[str] = []
    migrations_dir = root / "database" / "migrations"
    schema = root / "database" / "schema.sql"
    openapi = root / "api" / "openapi.yaml"
    compose = root / "docker-compose.yml"
    migrations = sorted(path.name for path in migrations_dir.glob("*.sql")) if migrations_dir.exists() else []
    if "0001_initial_schema.sql" not in migrations:
        errors.append("Missing database/migrations/0001_initial_schema.sql")
    if "0002_runtime_events_and_rbac.sql" not in migrations:
        errors.append("Missing database/migrations/0002_runtime_events_and_rbac.sql")
    if migrations and migrations != sorted(migrations):
        errors.append("Database migrations are not lexicographically ordered")
    schema_text = schema.read_text(encoding="utf-8") if schema.exists() else ""
    migration_text = (migrations_dir / "0002_runtime_events_and_rbac.sql").read_text(encoding="utf-8") if (migrations_dir / "0002_runtime_events_and_rbac.sql").exists() else ""
    if "CREATE TABLE runtime_events" not in schema_text:
        errors.append("runtime_events table is missing from database/schema.sql")
    if "CREATE TABLE IF NOT EXISTS runtime_events" not in migration_text:
        errors.append("runtime_events migration is missing from 0002_runtime_events_and_rbac.sql")
    for permission in ("operations:read", "trading:write", "incident:write", "exchange:write"):
        if permission not in migration_text:
            errors.append(f"RBAC migration missing permission {permission}")
    openapi_text = openapi.read_text(encoding="utf-8") if openapi.exists() else ""
    for path in REQUIRED_OPENAPI_PATHS:
        if path not in openapi_text:
            errors.append(f"OpenAPI contract missing {path}")
    compose_text = compose.read_text(encoding="utf-8") if compose.exists() else ""
    if "redis-server" in compose_text and "appendonly" not in compose_text:
        errors.append("docker-compose Redis service does not enable appendonly persistence")
    return errors


def validate_jwt_claims(claims: dict[str, Any]) -> List[str]:
    errors: List[str] = []
    roles = set(claims.get("roles") or [])
    permissions = set(claims.get("permissions") or [])
    if not roles.intersection(REQUIRED_JWT_ROLES):
        errors.append(f"JWT roles must include at least one of: {', '.join(sorted(REQUIRED_JWT_ROLES))}")
    if not permissions.intersection(REQUIRED_JWT_PERMISSIONS):
        errors.append(f"JWT permissions must include at least one of: {', '.join(sorted(REQUIRED_JWT_PERMISSIONS))}")
    if "exp" not in claims:
        errors.append("JWT claims should include exp")
    return errors


def validate_health_endpoints(api_base_url: str) -> List[str]:
    errors: List[str] = []
    base = api_base_url.rstrip("/")
    for path in ("/health", "/ready"):
        url = f"{base}{path}"
        try:
            with urlopen(url, timeout=5) as response:
                if response.status >= 400:
                    errors.append(f"{url} returned HTTP {response.status}")
        except URLError as exc:
            errors.append(f"{url} failed: {exc}")
    return errors


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate platform environment variables")
    parser.add_argument("--file", dest="env_file", type=Path, help="Optional .env file to validate")
    parser.add_argument("--allow-template-values", action="store_true", help="Allow template values in committed environment files")
    parser.add_argument("--release-checks", action="store_true", help="Validate release-candidate repository files")
    parser.add_argument("--repo-root", type=Path, default=Path.cwd(), help="Repository root for release checks")
    parser.add_argument("--jwt-claims-file", type=Path, help="JSON file containing decoded JWT claims to validate")
    parser.add_argument("--api-base-url", help="Validate /health and /ready on this API base URL")
    args = parser.parse_args(argv)
    try:
        errors = validate(merged_environment(args.env_file), args.allow_template_values)
        if args.release_checks:
            errors.extend(validate_release_files(args.repo_root))
        if args.jwt_claims_file:
            errors.extend(validate_jwt_claims(json.loads(args.jwt_claims_file.read_text(encoding="utf-8"))))
        if args.api_base_url:
            errors.extend(validate_health_endpoints(args.api_base_url))
    except Exception as exc:
        print(f"Environment validation failed: {exc}", file=sys.stderr)
        return 2
    if errors:
        print("Environment validation failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    source = str(args.env_file) if args.env_file else "process environment"
    print(f"Environment validation passed for {source}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
