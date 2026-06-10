from __future__ import annotations

import asyncio
import statistics
import time
from dataclasses import dataclass
from typing import Awaitable, Callable

from mmbot.engines.risk.engine import OrderIntent, RiskEngine


@dataclass(frozen=True)
class StressResult:
    name: str
    operations: int
    errors: int
    latency_p50_ms: float
    latency_p95_ms: float
    throughput_per_second: float


class StressHarness:
    async def high_frequency_orders(self, name: str, count: int, operation: Callable[[int], Awaitable[None]]) -> StressResult:
        latencies: list[float] = []
        errors = 0
        start = time.perf_counter()
        for index in range(count):
            op_start = time.perf_counter()
            try:
                await operation(index)
            except Exception:
                errors += 1
            latencies.append((time.perf_counter() - op_start) * 1000)
        elapsed = max(time.perf_counter() - start, 1e-9)
        return self._result(name, count, errors, latencies, elapsed)

    async def exchange_disconnect(self, reconnect_operation: Callable[[], Awaitable[None]], attempts: int) -> StressResult:
        async def op(_: int) -> None:
            await reconnect_operation()
        return await self.high_frequency_orders("exchange_disconnect", attempts, op)

    async def latency_test(self, operation: Callable[[], Awaitable[None]], samples: int) -> StressResult:
        async def op(_: int) -> None:
            await operation()
        return await self.high_frequency_orders("latency", samples, op)

    async def orderbook_burst(self, handler: Callable[[dict], Awaitable[None]], bursts: int, messages_per_burst: int) -> StressResult:
        async def op(index: int) -> None:
            await asyncio.gather(*(handler({"sequence": index * messages_per_burst + item, "bids": [], "asks": []}) for item in range(messages_per_burst)))
        return await self.high_frequency_orders("orderbook_burst", bursts, op)

    def risk_trigger_test(self, risk: RiskEngine, intent: OrderIntent, iterations: int) -> StressResult:
        latencies: list[float] = []
        errors = 0
        start = time.perf_counter()
        for _ in range(iterations):
            op_start = time.perf_counter()
            try:
                risk.evaluate_order(intent, position_notional=0.0, total_exposure=0.0, open_orders=0, daily_pnl=0.0)
            except Exception:
                errors += 1
            latencies.append((time.perf_counter() - op_start) * 1000)
        return self._result("risk_trigger", iterations, errors, latencies, max(time.perf_counter() - start, 1e-9))

    def circuit_breaker_test(self, risk: RiskEngine, breaker_name: str, failures: int) -> bool:
        for _ in range(failures):
            risk.record_failure(breaker_name)
        return risk.breakers[breaker_name].is_open()

    def _result(self, name: str, operations: int, errors: int, latencies: list[float], elapsed: float) -> StressResult:
        sorted_latencies = sorted(latencies) or [0.0]
        p50 = statistics.median(sorted_latencies)
        p95 = sorted_latencies[min(len(sorted_latencies) - 1, int(len(sorted_latencies) * 0.95))]
        return StressResult(name, operations, errors, p50, p95, operations / elapsed)
