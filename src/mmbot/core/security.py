from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from mmbot.core.config import Settings, get_settings

bearer_scheme = HTTPBearer(auto_error=True)


def decode_token(token: str, settings: Settings) -> dict[str, Any]:
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid token") from exc
    expires = payload.get("exp")
    if expires is not None and datetime.fromtimestamp(float(expires), tz=timezone.utc) < datetime.now(timezone.utc):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="token expired")
    return payload


def require_admin(credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme), settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    payload = decode_token(credentials.credentials, settings)
    permissions = set(payload.get("permissions", []))
    roles = set(payload.get("roles", []))
    if "platform_admin" not in roles and "config:write" not in permissions:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="insufficient permissions")
    return payload
