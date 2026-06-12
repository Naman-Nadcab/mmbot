from __future__ import annotations

import argparse
import time
from typing import Iterable

import jwt

from mmbot.core.config import Settings, get_settings


DEFAULT_ROLES = ["platform_admin"]
DEFAULT_PERMISSIONS = ["operations:read", "config:write", "risk:read", "risk:write", "incident:write", "trading:write"]


def issue_token(settings: Settings, subject: str, roles: list[str], permissions: list[str], expires_in_seconds: int) -> str:
    now = int(time.time())
    payload = {
        "sub": subject,
        "roles": roles,
        "permissions": permissions,
        "iat": now,
        "exp": now + expires_in_seconds,
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def _csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def main(argv: Iterable[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Issue an operator JWT signed with the active backend JWT_SECRET")
    parser.add_argument("--subject", default="operator")
    parser.add_argument("--roles", default=",".join(DEFAULT_ROLES), help="Comma-separated roles")
    parser.add_argument("--permissions", default=",".join(DEFAULT_PERMISSIONS), help="Comma-separated permissions")
    parser.add_argument("--expires-in-seconds", type=int, default=86400)
    args = parser.parse_args(list(argv) if argv is not None else None)
    token = issue_token(get_settings(), args.subject, _csv(args.roles), _csv(args.permissions), args.expires_in_seconds)
    print(token)


if __name__ == "__main__":
    main()
