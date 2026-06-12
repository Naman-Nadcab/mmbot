from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from mmbot.core.config import get_settings
from mmbot.db.migrations import MigrationRunner
from mmbot.db.seeds import SeedRunner
from mmbot.db.session import Database


async def _run(action: str) -> None:
    database = Database(get_settings())
    try:
        if action in {"migrate", "all"}:
            applied = await MigrationRunner(database.engine, Path("database/migrations")).migrate()
            print({"migrations_applied": applied})
        if action in {"seed", "all"}:
            applied = await SeedRunner(database.engine, Path("database/seeds")).seed()
            print({"seeds_applied": applied})
    finally:
        await database.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Database migration and seed runner")
    parser.add_argument("action", choices=["migrate", "seed", "all"])
    args = parser.parse_args()
    asyncio.run(_run(args.action))


if __name__ == "__main__":
    main()
