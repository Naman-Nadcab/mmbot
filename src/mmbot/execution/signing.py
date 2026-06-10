from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

from mmbot.execution.models import ExecutionVenue
from mmbot.execution.specs import SigningStyle


@dataclass(frozen=True)
class ExecutionCredentials:
    api_key: str
    api_secret: str
    passphrase: str | None = None
    memo: str | None = None


@dataclass(frozen=True)
class SignedRequest:
    path: str
    params: dict[str, Any]
    body: dict[str, Any] | None
    headers: dict[str, str]


def sign_request(style: SigningStyle, method: str, path: str, params: dict[str, Any], body: dict[str, Any] | None, credentials: ExecutionCredentials) -> SignedRequest:
    if style in {SigningStyle.binance_query, SigningStyle.mexc_query}:
        return _sign_binance_like(path, params, body, credentials)
    if style is SigningStyle.gate_v4:
        return _sign_gate(method, path, params, body, credentials)
    if style is SigningStyle.kucoin_v2:
        return _sign_kucoin(method, path, params, body, credentials)
    if style is SigningStyle.bitmart_v2:
        return _sign_bitmart(path, params, body, credentials)
    if style is SigningStyle.coinstore_hmac:
        return _sign_coinstore(method, path, params, body, credentials)
    raise ValueError(f"unsupported signing style: {style}")


def _sign_binance_like(path: str, params: dict[str, Any], body: dict[str, Any] | None, credentials: ExecutionCredentials) -> SignedRequest:
    signed_params = {k: v for k, v in params.items() if v is not None}
    signed_params["timestamp"] = int(time.time() * 1000)
    signed_params.setdefault("recvWindow", 5000)
    query = urlencode(signed_params)
    signature = hmac.new(credentials.api_secret.encode(), query.encode(), hashlib.sha256).hexdigest()
    signed_params["signature"] = signature
    return SignedRequest(path, signed_params, body, {"X-MBX-APIKEY": credentials.api_key})


def _sign_gate(method: str, path: str, params: dict[str, Any], body: dict[str, Any] | None, credentials: ExecutionCredentials) -> SignedRequest:
    timestamp = str(int(time.time()))
    query = urlencode({k: v for k, v in params.items() if v is not None})
    payload = json.dumps(body or {}, separators=(",", ":")) if body else ""
    hashed_payload = hashlib.sha512(payload.encode()).hexdigest()
    message = "
".join([method.upper(), path, query, hashed_payload, timestamp])
    signature = hmac.new(credentials.api_secret.encode(), message.encode(), hashlib.sha512).hexdigest()
    return SignedRequest(path, params, body, {"KEY": credentials.api_key, "Timestamp": timestamp, "SIGN": signature})


def _sign_kucoin(method: str, path: str, params: dict[str, Any], body: dict[str, Any] | None, credentials: ExecutionCredentials) -> SignedRequest:
    timestamp = str(int(time.time() * 1000))
    query = urlencode({k: v for k, v in params.items() if v is not None})
    endpoint = f"{path}?{query}" if query else path
    payload = json.dumps(body or {}, separators=(",", ":")) if body else ""
    prehash = f"{timestamp}{method.upper()}{endpoint}{payload}"
    signature = base64.b64encode(hmac.new(credentials.api_secret.encode(), prehash.encode(), hashlib.sha256).digest()).decode()
    headers = {"KC-API-KEY": credentials.api_key, "KC-API-SIGN": signature, "KC-API-TIMESTAMP": timestamp, "KC-API-KEY-VERSION": "2"}
    if credentials.passphrase:
        headers["KC-API-PASSPHRASE"] = base64.b64encode(hmac.new(credentials.api_secret.encode(), credentials.passphrase.encode(), hashlib.sha256).digest()).decode()
    return SignedRequest(path, params, body, headers)


def _sign_bitmart(path: str, params: dict[str, Any], body: dict[str, Any] | None, credentials: ExecutionCredentials) -> SignedRequest:
    timestamp = str(int(time.time() * 1000))
    memo = credentials.memo or credentials.passphrase or ""
    payload = json.dumps(body or params or {}, separators=(",", ":"))
    message = f"{timestamp}#{memo}#{payload}"
    signature = hmac.new(credentials.api_secret.encode(), message.encode(), hashlib.sha256).hexdigest()
    return SignedRequest(path, params, body, {"X-BM-KEY": credentials.api_key, "X-BM-TIMESTAMP": timestamp, "X-BM-SIGN": signature})


def _sign_coinstore(method: str, path: str, params: dict[str, Any], body: dict[str, Any] | None, credentials: ExecutionCredentials) -> SignedRequest:
    timestamp = str(int(time.time() * 1000))
    payload = json.dumps(body or params or {}, separators=(",", ":"), sort_keys=True)
    message = f"{timestamp}{method.upper()}{path}{payload}"
    signature = hmac.new(credentials.api_secret.encode(), message.encode(), hashlib.sha256).hexdigest()
    return SignedRequest(path, params, body, {"X-CS-APIKEY": credentials.api_key, "X-CS-TIMESTAMP": timestamp, "X-CS-SIGN": signature})
