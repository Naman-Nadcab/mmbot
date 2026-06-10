#!/usr/bin/env python3
"""Validate required environment variables for the platform foundation."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

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

ALLOWED_APP_ENVS = {"development", "staging", "production", "test"}
ALLOWED_LOG_LEVELS = {"TRACE", "DEBUG", "INFO", "WARNING", "WARN", "ERROR", "CRITICAL"}
PLACEHOLDER_PATTERNS = (
    re.compile(r"change[-_ ]?me", re.IGNORECASE),
    re.compile(r"placeholder", re.IGNORECASE),
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


def is_placeholder(value: str) -> bool:
    return any(pattern.search(value) for pattern in PLACEHOLDER_PATTERNS)


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


def validate(values: Dict[str, str], allow_placeholders: bool) -> List[str]:
    errors: List[str] = []
    for key in REQUIRED_KEYS:
        value = values.get(key, "")
        if not value:
            errors.append(f"Missing required environment variable: {key}")
        elif not allow_placeholders and is_placeholder(value):
            errors.append(f"{key} contains a placeholder value")

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
    if jwt_secret and not allow_placeholders and len(jwt_secret) < 32:
        errors.append("JWT_SECRET must be at least 32 characters")

    exchange_keys_value = values.get("EXCHANGE_API_KEYS", "{}")
    exchange_secrets_value = values.get("EXCHANGE_API_SECRETS", "{}")
    if allow_placeholders and (is_placeholder(exchange_keys_value) or is_placeholder(exchange_secrets_value)):
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


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate platform environment variables")
    parser.add_argument("--file", dest="env_file", type=Path, help="Optional .env file to validate")
    parser.add_argument("--allow-placeholders", action="store_true", help="Allow placeholder/template values")
    args = parser.parse_args(argv)
    try:
        errors = validate(merged_environment(args.env_file), args.allow_placeholders)
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
