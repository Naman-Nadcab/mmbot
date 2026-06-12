from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from mmbot.core.config import Settings, get_settings

bearer_scheme = HTTPBearer(auto_error=False)
logger = logging.getLogger(__name__)


def _auth_metadata(request: Request | None, token: str | None, raw_authorization: str | None = None) -> dict[str, Any]:
    value = token or ""
    raw_value = raw_authorization or ""
    return {
        "path": request.url.path if request else None,
        "raw_authorization_present": raw_authorization is not None,
        "raw_authorization_length": len(raw_value),
        "raw_authorization_prefix": raw_value[:12],
        "raw_authorization_suffix": raw_value[-12:],
        "token_length": len(value),
        "token_segments": len(value.split(".")) if value else 0,
        "token_prefix": value[:12],
        "token_suffix": value[-12:],
    }


def decode_token(token: str, settings: Settings, request: Request | None = None, raw_authorization: str | None = None) -> dict[str, Any]:
    metadata = _auth_metadata(request, token, raw_authorization)
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
    except jwt.PyJWTError as exc:
        logger.warning("jwt_decode_failed", extra=metadata | {"reason": exc.__class__.__name__})
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid token") from exc
    expires = payload.get("exp")
    if expires is not None and datetime.fromtimestamp(float(expires), tz=timezone.utc) < datetime.now(timezone.utc):
        logger.warning("jwt_decode_failed", extra=metadata | {"reason": "expired"})
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="token expired")
    logger.info("jwt_decode_success", extra=metadata | {"roles": payload.get("roles", []), "permissions": payload.get("permissions", [])})
    return payload


def _missing_credentials(request: Request, reason: str) -> HTTPException:
    logger.warning("jwt_decode_failed", extra=_auth_metadata(request, None, request.headers.get("authorization")) | {"reason": reason})
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid token")


def require_admin(request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme), settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    if credentials is None:
        raise _missing_credentials(request, "missing_or_malformed_authorization")
    logger.info("jwt_auth_request", extra=_auth_metadata(request, credentials.credentials, request.headers.get("authorization")))
    payload = decode_token(credentials.credentials, settings, request, request.headers.get("authorization"))
    permissions = set(payload.get("permissions", []))
    roles = set(payload.get("roles", []))
    if "platform_admin" not in roles and "config:write" not in permissions:
        logger.warning("jwt_authorization_failed", extra=_auth_metadata(request, credentials.credentials, request.headers.get("authorization")) | {"reason": "insufficient_admin_permissions", "roles": list(roles), "permissions": list(permissions)})
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="insufficient permissions")
    return payload


def _require_access(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None,
    settings: Settings,
    *,
    allowed_roles: set[str],
    allowed_permissions: set[str],
    reason: str,
) -> dict[str, Any]:
    if credentials is None:
        raise _missing_credentials(request, "missing_or_malformed_authorization")
    logger.info("jwt_auth_request", extra=_auth_metadata(request, credentials.credentials, request.headers.get("authorization")))
    payload = decode_token(credentials.credentials, settings, request, request.headers.get("authorization"))
    permissions = set(payload.get("permissions", []))
    roles = set(payload.get("roles", []))
    if not roles.intersection(allowed_roles) and not permissions.intersection(allowed_permissions):
        logger.warning("jwt_authorization_failed", extra=_auth_metadata(request, credentials.credentials, request.headers.get("authorization")) | {"reason": reason, "roles": list(roles), "permissions": list(permissions)})
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="insufficient permissions")
    return payload


def require_operations_access(request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme), settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    return _require_access(
        request,
        credentials,
        settings,
        allowed_roles={"platform_admin", "risk_manager", "trader_operator", "incident_responder", "read_only_analyst"},
        allowed_permissions={"operations:read", "config:read", "risk:read"},
        reason="insufficient_operations_permissions",
    )


def require_config_write(request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme), settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    return _require_access(
        request,
        credentials,
        settings,
        allowed_roles={"platform_admin", "risk_manager", "trader_operator"},
        allowed_permissions={"config:write"},
        reason="insufficient_config_write_permissions",
    )


def require_risk_write(request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme), settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    return _require_access(
        request,
        credentials,
        settings,
        allowed_roles={"platform_admin", "risk_manager"},
        allowed_permissions={"risk:write"},
        reason="insufficient_risk_write_permissions",
    )


def require_trading_control(request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme), settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    return _require_access(
        request,
        credentials,
        settings,
        allowed_roles={"platform_admin", "trader_operator"},
        allowed_permissions={"trading:write", "config:write"},
        reason="insufficient_trading_control_permissions",
    )


def require_incident_response(request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme), settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    return _require_access(
        request,
        credentials,
        settings,
        allowed_roles={"platform_admin", "incident_responder", "risk_manager"},
        allowed_permissions={"incident:write", "risk:write"},
        reason="insufficient_incident_response_permissions",
    )
