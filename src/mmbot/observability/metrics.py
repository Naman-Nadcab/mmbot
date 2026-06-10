from __future__ import annotations

from dataclasses import dataclass, field
from time import monotonic


@dataclass
class RuntimeMetrics:
    counters: dict[str, float] = field(default_factory=dict)
    gauges: dict[str, float] = field(default_factory=dict)
    started_at: float = field(default_factory=monotonic)

    def increment(self, name: str, value: float = 1.0) -> None:
        self.counters[name] = self.counters.get(name, 0.0) + value

    def set_gauge(self, name: str, value: float) -> None:
        self.gauges[name] = value

    def snapshot(self) -> dict[str, object]:
        uptime = monotonic() - self.started_at
        return {
            "uptime_seconds": uptime,
            "counters": dict(self.counters),
            "gauges": dict(self.gauges),
        }
