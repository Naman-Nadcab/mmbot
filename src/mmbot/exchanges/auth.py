from __future__ import annotations

import base64
import hashlib
import hmac
import time
from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class Credentials:
    api_key: str
    api_secret: str
    passphrase: str | None = None


class HmacSigner:
    def __init__(self, credentials: Credentials, digest: str = "sha256", base64_output: bool = False):
        self.credentials = credentials
        self.digest = digest
        self.base64_output = base64_output

    def sign(self, payload: str) -> str:
        digestmod = getattr(hashlib, self.digest)
        signature = hmac.new(self.credentials.api_secret.encode(), payload.encode(), digestmod).digest()
        return base64.b64encode(signature).decode() if self.base64_output else signature.hex()

    def timestamp_ms(self) -> str:
        return str(int(time.time() * 1000))

    def headers(self, payload: str, key_header: str = "X-API-KEY", signature_header: str = "X-SIGNATURE") -> Mapping[str, str]:
        return {key_header: self.credentials.api_key, signature_header: self.sign(payload)}
