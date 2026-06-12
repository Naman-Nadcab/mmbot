from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from mmbot.core.config import VolumeSettings


@dataclass(frozen=True)
class VolumeWindowProgress:
    target_notional: float
    executed_notional: float
    remaining_notional: float
    progress_ratio: float
    window_seconds_remaining: float


@dataclass(frozen=True)
class ExecutionPressureSignal:
    enabled: bool
    size_multiplier: float
    spread_multiplier: float
    urgency: float
    reason: str


@dataclass(frozen=True)
class VolumeProgressReport:
    hourly: VolumeWindowProgress
    daily: VolumeWindowProgress
    weekly: VolumeWindowProgress
    participation_rate: float
    pressure: ExecutionPressureSignal


class VolumeEngine:
    def __init__(self, settings: VolumeSettings):
        self.settings = settings
        self._last_pressure_order_at: datetime | None = None

    def update_settings(self, settings: VolumeSettings) -> None:
        self.settings = settings

    def progress(
        self,
        *,
        now: datetime,
        hourly_notional: float,
        daily_notional: float,
        weekly_notional: float,
        external_market_volume_notional: float,
    ) -> VolumeProgressReport:
        now = now.astimezone(timezone.utc)
        hourly = self._window(self.settings.hourly_target_notional, hourly_notional, _seconds_until_next_hour(now))
        daily = self._window(self.settings.daily_target_notional, daily_notional, _seconds_until_next_day(now))
        weekly = self._window(self.settings.weekly_target_notional, weekly_notional, _seconds_until_next_week(now))
        participation = 0.0 if external_market_volume_notional <= 0 else daily_notional / external_market_volume_notional
        pressure = self.pressure_signal(hourly, daily, weekly, participation, external_market_volume_notional)
        return VolumeProgressReport(hourly, daily, weekly, participation, pressure)

    def pressure_signal(
        self,
        hourly: VolumeWindowProgress,
        daily: VolumeWindowProgress,
        weekly: VolumeWindowProgress,
        participation_rate: float,
        external_market_volume_notional: float,
    ) -> ExecutionPressureSignal:
        if not self.settings.enabled:
            return ExecutionPressureSignal(False, 1.0, 1.0, 0.0, "volume_engine_disabled")
        if self.settings.external_volume_required and external_market_volume_notional <= 0:
            return ExecutionPressureSignal(False, 1.0, 1.0, 0.0, "external_volume_required")
        if participation_rate >= self.settings.max_participation_rate:
            return ExecutionPressureSignal(False, 1.0, 1.0, 0.0, "participation_limit_reached")
        urgency = max(self._urgency(hourly), self._urgency(daily), self._urgency(weekly))
        if urgency < self.settings.pressure_threshold:
            return ExecutionPressureSignal(True, 1.0, 1.0, urgency, "on_target")
        size_multiplier = min(self.settings.max_size_multiplier, 1.0 + urgency)
        spread_multiplier = max(0.5, 1.0 - urgency * 0.25)
        return ExecutionPressureSignal(True, size_multiplier, spread_multiplier, urgency, "behind_target")

    def can_apply_pressure_order(self, now: datetime) -> bool:
        if self._last_pressure_order_at is None:
            self._last_pressure_order_at = now
            return True
        elapsed = (now - self._last_pressure_order_at).total_seconds()
        if elapsed < self.settings.min_seconds_between_pressure_orders:
            return False
        self._last_pressure_order_at = now
        return True

    def _window(self, target: float, executed: float, remaining_seconds: float) -> VolumeWindowProgress:
        remaining = max(0.0, target - executed)
        progress = 1.0 if target <= 0 else min(1.0, executed / target)
        return VolumeWindowProgress(target, executed, remaining, progress, max(0.0, remaining_seconds))

    def _urgency(self, window: VolumeWindowProgress) -> float:
        if window.target_notional <= 0:
            return 0.0
        elapsed_ratio = 1.0 - window.window_seconds_remaining / _target_window_seconds(window)
        expected_progress = min(1.0, max(0.0, elapsed_ratio))
        return max(0.0, expected_progress - window.progress_ratio)


def _target_window_seconds(window: VolumeWindowProgress) -> float:
    if window.window_seconds_remaining <= 3600:
        return 3600.0
    if window.window_seconds_remaining <= 86400:
        return 86400.0
    return 604800.0


def _seconds_until_next_hour(now: datetime) -> float:
    next_hour = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    return (next_hour - now).total_seconds()


def _seconds_until_next_day(now: datetime) -> float:
    tomorrow = (now + timedelta(days=1)).date()
    next_day = datetime(tomorrow.year, tomorrow.month, tomorrow.day, tzinfo=timezone.utc)
    return (next_day - now).total_seconds()


def _seconds_until_next_week(now: datetime) -> float:
    start_today = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
    days = 7 - now.weekday()
    return (start_today + timedelta(days=days) - now).total_seconds()
