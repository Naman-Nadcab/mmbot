from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from mmbot.core.config import Settings


class SecretCipher:
    def __init__(self, settings: Settings):
        digest = hashlib.sha256(settings.JWT_SECRET.encode("utf-8")).digest()
        self.key_id = hashlib.sha256(digest).hexdigest()[:16]
        self.fernet = Fernet(base64.urlsafe_b64encode(digest))

    def encrypt(self, value: str | None) -> bytes | None:
        if value is None:
            return None
        return self.fernet.encrypt(value.encode("utf-8"))

    def decrypt(self, value: bytes | None) -> str | None:
        if value is None:
            return None
        try:
            return self.fernet.decrypt(value).decode("utf-8")
        except InvalidToken as exc:
            raise ValueError("stored secret cannot be decrypted with the active key") from exc
