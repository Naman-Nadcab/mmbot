from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from mmbot.core.exceptions import RateLimitExceededError
from mmbot.exchanges.types import RateLimitRule


@dataclass
class TokenBucket:
    capacity: int
    refill_window_seconds: int
    tokens: float
    updated_at: float


class AsyncRateLimiter:
    def __init__(self, rule: RateLimitRule):
        self.bucket = TokenBucket(rule.requests, rule.window_seconds, float(rule.requests), time.monotonic())
        self._lock = asyncio.Lock()

    async def acquire(self, tokens: int = 1) -> None:
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self.bucket.updated_at
            refill = elapsed * (self.bucket.capacity / self.bucket.refill_window_seconds)
            self.bucket.tokens = min(self.bucket.capacity, self.bucket.tokens + refill)
            self.bucket.updated_at = now
            if self.bucket.tokens < tokens:
                raise RateLimitExceededError("exchange local rate limit exceeded")
            self.bucket.tokens -= tokens
