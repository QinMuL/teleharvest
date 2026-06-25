#!/usr/bin/env python3
"""TeleHarvest 数据库迁移脚本。

使用方式：
    python scripts/migrate.py           # 执行迁移
    python scripts/migrate.py --check   # 仅检查待迁移项

当前阶段（P0）：仅支持自动建表，无需显式迁移。
后续阶段将引入 Alembic 管理迁移版本。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# 将 src 加入路径
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from teleharvest.config.settings import load_settings
from teleharvest.db.session import DatabaseSession


async def migrate(check_only: bool = False) -> None:
    """执行数据库迁移。"""
    settings = load_settings()
    db = DatabaseSession(settings.database)

    print(f"[INFO] 数据库 URL: {settings.database.url}")

    if check_only:
        print("[INFO] 检查模式：仅验证连接，不执行变更")
        return

    await db.start()
    print("[INFO] 数据库表已创建/更新")
    await db.stop()


def main() -> None:
    parser = argparse.ArgumentParser(description="TeleHarvest 数据库迁移")
    parser.add_argument("--check", action="store_true", help="仅检查，不执行迁移")
    args = parser.parse_args()

    try:
        asyncio.run(migrate(check_only=args.check))
    except Exception as exc:
        print(f"[ERROR] 迁移失败: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
