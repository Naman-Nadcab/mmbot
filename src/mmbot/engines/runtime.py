from __future__ import annotations

import asyncio
import json
import logging
import signal
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mmbot.core.config import Settings, default_runtime_config
from mmbot.db.repositories import ConfigRepository
from mmbot.db.session import Database
from mmbot.engines.market_data.engine import MarketDataEngine
from mmbot.engines.market_data.runtime import MarketDataRuntime
from mmbot.engines.market_making.engine import QuoteEngine
from mmbot.engines.market_making.runtime import MarketMakerRuntime
from mmbot.observability.logging import configure_logging
from mmbot.observability.metrics import RuntimeMetrics
from mmbot.redis.manager import CacheManager, EngineCommunicationLayer, PubSubFramework, RedisManager

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EngineHealthSnapshot:
    component_name: str
    status: str
    started_at: str
    last_heartbeat_at: str
    uptime_seconds: float
    loop_iterations: int
    consecutive_errors: int
    last_error: str | None


class EngineDaemon:
    def __init__(
        self,
        settings: Settings,
        component_name: str,
        heartbeat_interval_seconds: float = 60.0,
        health_dir: Path | None = None,
    ):
        self.settings = settings
        self.component_name = component_name
        self.heartbeat_interval_seconds = heartbeat_interval_seconds
        self.health_dir = health_dir or Path.home() / ".mmbot" / "health"
        self.health_file = self.health_dir / f'{component_name}.health.json'
        self.started_at = datetime.now(timezone.utc)
        self.last_heartbeat_at = self.started_at
        self.loop_iterations = 0
        self.consecutive_errors = 0
        self.last_error: str | None = None
        self._shutdown_event = asyncio.Event()
        self.database: Database | None = None
        self.session: Any | None = None
        self.redis: RedisManager | None = None
        self.engine: MarketDataEngine | QuoteEngine | None = None
        self.runtime: MarketDataRuntime | MarketMakerRuntime | None = None
        self.metrics = RuntimeMetrics()

    async def run(self) -> None:
        configure_logging(self.settings.LOG_LEVEL)
        self._install_signal_handlers()
        self.health_dir.mkdir(parents=True, exist_ok=True)
        await self._initialize_dependencies()
        self._log_startup()
        await self._write_health('starting')
        try:
            while not self._shutdown_event.is_set():
                try:
                    await self._service_tick()
                    self.consecutive_errors = 0
                    self.last_error = None
                    await self._write_health('healthy')
                    self._log_heartbeat('healthy')
                    await self._wait_for_shutdown(self.heartbeat_interval_seconds)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    if self.session is not None:
                        await self.session.rollback()
                    self.consecutive_errors += 1
                    self.last_error = f'{exc.__class__.__name__}: {exc}'
                    logger.exception(
                        'engine_runtime_error',
                        extra={
                            'component_name': self.component_name,
                            'consecutive_errors': self.consecutive_errors,
                        },
                    )
                    await self._write_health('degraded')
                    await self._publish_heartbeat(redis_ok=self.redis is not None, database_ok=False, status='degraded')
                    await self._wait_for_shutdown(min(30.0, max(1.0, self.consecutive_errors)))
        finally:
            await self._shutdown()

    async def stop(self) -> None:
        self._shutdown_event.set()

    async def _initialize_dependencies(self) -> None:
        self.redis = RedisManager(self.settings)
        cache = CacheManager(self.redis.client)
        bus = EngineCommunicationLayer(PubSubFramework(self.redis.client), cache)
        self.database = Database(self.settings)
        self.session = self.database.session_factory()
        try:
            runtime_config = await ConfigRepository(self.session).runtime_config()
        except Exception:
            await self.session.rollback()
            runtime_config = default_runtime_config()
        if self.component_name == 'market-data-engine':
            self.engine = MarketDataEngine(runtime_config.liquidity, bus)
            self.runtime = MarketDataRuntime(self.settings, self.session, bus, self.engine, self.metrics)
        elif self.component_name == 'market-maker-engine':
            self.engine = QuoteEngine(runtime_config.spread, runtime_config.order_size, runtime_config.inventory, runtime_config.order_layers)
            self.runtime = MarketMakerRuntime(self.settings, self.session, bus, self.engine, self.metrics, runtime_config)
        else:
            raise ValueError(f'Unsupported engine component: {self.component_name}')

    async def _service_tick(self) -> None:
        self.loop_iterations += 1
        if self.redis is None:
            raise RuntimeError('Redis manager is not initialized')
        redis_ok = await self.redis.health_check()
        database_ok = True
        if self.database is not None:
            database_ok = await self.database.health_check()
        if not redis_ok or not database_ok:
            raise RuntimeError(f'dependency health failed redis={redis_ok} database={database_ok}')
        if self.runtime is not None:
            await self.runtime.tick()
        if self.session is not None:
            await self.session.commit()
        await self._publish_heartbeat(redis_ok=redis_ok, database_ok=database_ok, status='healthy')

    async def _publish_heartbeat(self, redis_ok: bool, database_ok: bool, status: str) -> None:
        if self.redis is None:
            return
        runtime_health = self.runtime.health() if self.runtime is not None else {}
        payload = self._snapshot(status).__dict__ | {'redis_ok': redis_ok, 'database_ok': database_ok, 'runtime': runtime_health}
        await self.redis.client.set(f'engine:health:{self.component_name}', json.dumps(payload, separators=(',', ':'), default=str), ex=max(120, int(self.heartbeat_interval_seconds * 3)))

    def _snapshot(self, status: str) -> EngineHealthSnapshot:
        now = datetime.now(timezone.utc)
        self.last_heartbeat_at = now
        return EngineHealthSnapshot(
            component_name=self.component_name,
            status=status,
            started_at=self.started_at.isoformat(),
            last_heartbeat_at=now.isoformat(),
            uptime_seconds=(now - self.started_at).total_seconds(),
            loop_iterations=self.loop_iterations,
            consecutive_errors=self.consecutive_errors,
            last_error=self.last_error,
        )

    async def _write_health(self, status: str) -> None:
        snapshot = self._snapshot(status)
        tmp_file = self.health_file.with_suffix('.tmp')
        tmp_file.write_text(json.dumps(asdict(snapshot), separators=(',', ':'), default=str), encoding='utf-8')
        tmp_file.replace(self.health_file)

    def _log_startup(self) -> None:
        if self.component_name == 'market-data-engine':
            message = 'Market Data Engine Started'
        elif self.component_name == 'market-maker-engine':
            message = 'Market Maker Engine Started'
        else:
            message = f'{self.component_name} Started'
        logger.info(message, extra={'component_name': self.component_name})

    def _log_heartbeat(self, status: str) -> None:
        uptime = (datetime.now(timezone.utc) - self.started_at).total_seconds()
        logger.info(
            'engine_heartbeat',
            extra={
                'component_name': self.component_name,
                'uptime_seconds': round(uptime, 3),
                'health_status': status,
                'loop_iterations': self.loop_iterations,
            },
        )

    async def _wait_for_shutdown(self, timeout: float) -> None:
        try:
            await asyncio.wait_for(self._shutdown_event.wait(), timeout=timeout)
        except TimeoutError:
            return

    async def _shutdown(self) -> None:
        logger.info('engine_shutdown_started', extra={'component_name': self.component_name})
        await self._write_health('stopping')
        if self.runtime is not None:
            await self.runtime.stop()
        if self.session is not None:
            await self.session.close()
        if self.redis is not None:
            await self.redis.close()
        if self.database is not None:
            await self.database.close()
        await self._write_health('stopped')
        logger.info('engine_shutdown_completed', extra={'component_name': self.component_name})

    def _install_signal_handlers(self) -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, self._shutdown_event.set)
            except NotImplementedError:
                signal.signal(sig, lambda _signum, _frame: self._shutdown_event.set())


def read_health(component_name: str, health_dir: Path | None = None, max_age_seconds: float = 180.0) -> EngineHealthSnapshot:
    health_file = (health_dir or Path.home() / ".mmbot" / "health") / f'{component_name}.health.json'
    payload: dict[str, Any] = json.loads(health_file.read_text(encoding='utf-8'))
    snapshot = EngineHealthSnapshot(**payload)
    last_heartbeat = datetime.fromisoformat(snapshot.last_heartbeat_at)
    age = (datetime.now(timezone.utc) - last_heartbeat).total_seconds()
    if snapshot.status != 'healthy':
        raise RuntimeError(f'{component_name} health status is {snapshot.status}')
    if age > max_age_seconds:
        raise RuntimeError(f'{component_name} heartbeat is stale: age={age:.3f}s')
    return snapshot
