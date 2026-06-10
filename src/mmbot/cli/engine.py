from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path

from mmbot.core.config import get_settings
from mmbot.engines.runtime import EngineDaemon, read_health


def main() -> None:
    parser = argparse.ArgumentParser(description="Engine command utilities")
    parser.add_argument("command", choices=["run", "health"])
    parser.add_argument("--component-name", default=os.environ.get("COMPONENT_NAME", "market-maker-engine"))
    parser.add_argument("--heartbeat-interval-seconds", type=float, default=float(os.environ.get("ENGINE_HEARTBEAT_INTERVAL_SECONDS", "60")))
    parser.add_argument("--health-dir", default=os.environ.get("ENGINE_HEALTH_DIR", str(Path.home() / ".mmbot" / "health")))
    parser.add_argument("--max-health-age-seconds", type=float, default=float(os.environ.get("ENGINE_MAX_HEALTH_AGE_SECONDS", "180")))
    args = parser.parse_args()
    if args.command == "run":
        settings = get_settings()
        daemon = EngineDaemon(
            settings=settings,
            component_name=args.component_name,
            heartbeat_interval_seconds=args.heartbeat_interval_seconds,
            health_dir=Path(args.health_dir),
        )
        asyncio.run(daemon.run())
    elif args.command == "health":
        snapshot = read_health(args.component_name, Path(args.health_dir), args.max_health_age_seconds)
        print(snapshot)


if __name__ == "__main__":
    main()
