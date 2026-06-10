from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import os
import struct
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any

import httpx
from fastapi import HTTPException, Request, status
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response


class SecretScope(str, Enum):
    exchange = "exchange"
    alerting = "alerting"
    jwt = "jwt"
    database = "database"


@dataclass(frozen=True)
class SecretRecord:
    key: str
    value: str
    version: str
    scope: SecretScope


class EnvSecretProvider:
    def get_secret(self, key: str, scope: SecretScope) -> SecretRecord:
        value = os.environ[key]
        return SecretRecord(key, value, os.environ.get(f"{key}_VERSION", "env"), scope)


class VaultSecretProvider:
    def __init__(self, base_url: str, token: str, mount: str = "secret"):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.mount = mount.strip("/")

    async def get_secret(self, key: str, scope: SecretScope) -> SecretRecord:
        path = f"{self.base_url}/v1/{self.mount}/data/{scope.value}/{key}"
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(path, headers={"X-Vault-Token": self.token})
        response.raise_for_status()
        payload = response.json()["data"]
        value = payload["data"]["value"]
        version = str(payload.get("metadata", {}).get("version", "vault"))
        return SecretRecord(key, value, version, scope)


class SecretRotationManager:
    def __init__(self):
        self.active_versions: dict[str, str] = {}

    def observe(self, secret: SecretRecord) -> bool:
        previous = self.active_versions.get(secret.key)
        self.active_versions[secret.key] = secret.version
        return previous is not None and previous != secret.version


class TotpMfaVerifier:
    def __init__(self, window: int = 1, digits: int = 6, interval: int = 30):
        self.window = window
        self.digits = digits
        self.interval = interval

    def verify(self, secret_base32: str, code: str, at_time: int | None = None) -> bool:
        at_time = int(time.time()) if at_time is None else at_time
        for offset in range(-self.window, self.window + 1):
            if hmac.compare_digest(self.generate(secret_base32, at_time + offset * self.interval), code):
                return True
        return False

    def generate(self, secret_base32: str, at_time: int | None = None) -> str:
        at_time = int(time.time()) if at_time is None else at_time
        key = base64.b32decode(secret_base32.upper() + "=" * ((8 - len(secret_base32) % 8) % 8))
        counter = int(at_time / self.interval)
        digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
        offset = digest[-1] & 0x0F
        binary = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
        return str(binary % (10 ** self.digits)).zfill(self.digits)


class IPAllowlistMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, cidrs: list[str]):
        super().__init__(app)
        self.networks = [ipaddress.ip_network(cidr) for cidr in cidrs]

    async def dispatch(self, request: Request, call_next) -> Response:
        if self.networks:
            if request.client is None:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="client address unavailable")
            client_ip = ipaddress.ip_address(request.client.host)
            if not any(client_ip in network for network in self.networks):
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="ip not allowed")
        return await call_next(request)


class InMemoryRateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, limit: int, window_seconds: int):
        super().__init__(app)
        self.limit = limit
        self.window_seconds = window_seconds
        self.buckets: dict[tuple[str, int], int] = {}

    async def dispatch(self, request: Request, call_next) -> Response:
        identity = request.headers.get("authorization") or (request.client.host if request.client else "anonymous")
        bucket = int(time.time() // self.window_seconds)
        key = (identity, bucket)
        self.buckets[key] = self.buckets.get(key, 0) + 1
        if self.buckets[key] > self.limit:
            raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="rate limit exceeded")
        return await call_next(request)


@dataclass(frozen=True)
class RbacPolicy:
    role: str
    resource: str
    action: str
    requires_mfa: bool = False


class AdvancedRbacEngine:
    def __init__(self, policies: list[RbacPolicy]):
        self.policies = policies

    def authorize(self, roles: set[str], resource: str, action: str, mfa_verified: bool) -> bool:
        for policy in self.policies:
            if policy.role in roles and policy.resource == resource and policy.action == action:
                if policy.requires_mfa and not mfa_verified:
                    return False
                return True
        return False


@dataclass(frozen=True)
class SignedAuditRecord:
    sequence: int
    payload: dict[str, Any]
    previous_signature: str
    signature: str


class SignedAuditChain:
    def __init__(self, signing_key: str):
        self.signing_key = signing_key.encode()
        self.previous_signature = "GENESIS"
        self.sequence = 0

    def append(self, payload: dict[str, Any]) -> SignedAuditRecord:
        self.sequence += 1
        canonical = json.dumps({"sequence": self.sequence, "payload": payload, "previous_signature": self.previous_signature}, sort_keys=True, separators=(",", ":"))
        signature = hmac.new(self.signing_key, canonical.encode(), hashlib.sha256).hexdigest()
        record = SignedAuditRecord(self.sequence, payload, self.previous_signature, signature)
        self.previous_signature = signature
        return record

    def verify(self, records: list[SignedAuditRecord]) -> bool:
        previous = "GENESIS"
        for record in records:
            canonical = json.dumps({"sequence": record.sequence, "payload": record.payload, "previous_signature": previous}, sort_keys=True, separators=(",", ":"))
            expected = hmac.new(self.signing_key, canonical.encode(), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(expected, record.signature):
                return False
            previous = record.signature
        return True


class TamperEvidentLog:
    def __init__(self, signing_key: str):
        self.chain = SignedAuditChain(signing_key)
        self.records: list[SignedAuditRecord] = []

    def write(self, event: dict[str, Any]) -> SignedAuditRecord:
        record = self.chain.append(event)
        self.records.append(record)
        return record

    def verify(self) -> bool:
        verifier = SignedAuditChain(self.chain.signing_key.decode())
        return verifier.verify(self.records)
