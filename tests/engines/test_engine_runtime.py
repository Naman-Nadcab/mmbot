import asyncio
import secrets

import pytest

from mmbot.core.config import Settings
from mmbot.engines.runtime import EngineDaemon, read_health


async def _handle_redis(reader, writer):
    try:
        while True:
            line = await reader.readline()
            if not line:
                break
            if line.startswith(b"*"):
                count = int(line[1:].strip() or b"0")
                parts = []
                for _ in range(count):
                    header = await reader.readline()
                    size = int(header[1:].strip() or b"0")
                    data = await reader.readexactly(size)
                    await reader.readexactly(2)
                    parts.append(data.upper())
                command = parts[0] if parts else b""
            else:
                command = line.strip().split(b" ")[0].upper()
            if command == b"PING":
                writer.write(b"+PONG\r\n")
            elif command == b"SET":
                writer.write(b"+OK\r\n")
            else:
                writer.write(b"+OK\r\n")
            await writer.drain()
    finally:
        writer.close()
        await writer.wait_closed()


def _settings(redis_url: str) -> Settings:
    return Settings(
        APP_ENV="test",
        DATABASE_URL="sqlite+aiosqlite:///:memory:",
        REDIS_URL=redis_url,
        JWT_SECRET=secrets.token_urlsafe(48),
        TELEGRAM_BOT_TOKEN="token",
        TELEGRAM_CHAT_ID="chat",
        EXCHANGE_API_KEYS={"binance": "key"},
        EXCHANGE_API_SECRETS={"binance": "secret"},
    )


@pytest.mark.asyncio
async def test_market_data_daemon_runs_until_stopped(tmp_path):
    server = await asyncio.start_server(_handle_redis, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    daemon = EngineDaemon(_settings(f"redis://127.0.0.1:{port}/0"), "market-data-engine", heartbeat_interval_seconds=0.05, health_dir=tmp_path)
    task = asyncio.create_task(daemon.run())
    try:
        for _ in range(60):
            await asyncio.sleep(0.025)
            if daemon.loop_iterations >= 2:
                break
        assert not task.done()
        snapshot = read_health("market-data-engine", tmp_path, max_age_seconds=5)
        assert snapshot.status == "healthy"
        assert snapshot.loop_iterations >= 1
    finally:
        await daemon.stop()
        await asyncio.wait_for(task, timeout=5)
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_market_maker_daemon_runs_until_stopped(tmp_path):
    server = await asyncio.start_server(_handle_redis, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    daemon = EngineDaemon(_settings(f"redis://127.0.0.1:{port}/0"), "market-maker-engine", heartbeat_interval_seconds=0.05, health_dir=tmp_path)
    task = asyncio.create_task(daemon.run())
    try:
        for _ in range(60):
            await asyncio.sleep(0.025)
            if daemon.loop_iterations >= 2:
                break
        assert not task.done()
        snapshot = read_health("market-maker-engine", tmp_path, max_age_seconds=5)
        assert snapshot.status == "healthy"
        assert snapshot.loop_iterations >= 1
    finally:
        await daemon.stop()
        await asyncio.wait_for(task, timeout=5)
        server.close()
        await server.wait_closed()
