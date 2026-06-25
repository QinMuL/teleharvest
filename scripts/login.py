#!/usr/bin/env python3
"""TeleHarvest 首次登录脚本。

用于在本地完成 Telegram 用户账号登录，生成会话文件。
会话文件生成后可复制到 Docker 容器中使用，避免在容器内交互式登录。

使用方式：
    python scripts/login.py

流程：
    1. 加载配置（API ID / API Hash）
    2. 创建 Pyrogram Client
    3. 交互式输入手机号 → 验证码 → （可选）两步验证密码
    4. 生成会话文件到 data/sessions/teleharvest.session
    5. 验证登录成功

注意：
    - 使用 Bot Token 时无需此脚本，直接启动即可
    - 会话文件包含敏感信息，请勿泄露或提交到版本控制
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# 将 src 加入路径
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from teleharvest.config.settings import load_settings
from teleharvest.utils.logger import setup_logging


async def login() -> None:
    """执行交互式登录。"""
    from loguru import logger
    from pyrogram import Client

    # 加载配置
    try:
        settings = load_settings()
    except Exception as exc:
        print(f"[FATAL] 配置加载失败: {exc}", file=sys.stderr)
        sys.exit(1)

    setup_logging(settings.logging)

    # Bot Token 模式无需交互登录
    if settings.bot_token:
        logger.info("检测到 Bot Token 配置，无需交互式登录")
        logger.info("直接运行 `python -m teleharvest` 即可，Pyrogram 会自动验证 Token")
        return

    # 检查会话文件是否已存在
    session_path = settings.monitor.session_dir / "teleharvest"
    session_file = Path(f"{session_path}.session")

    if session_file.exists():
        logger.info("会话文件已存在: {}", session_file)
        answer = input("是否重新登录？(y/N): ").strip().lower()
        if answer != "y":
            logger.info("取消登录，保留现有会话文件")
            return

    # 确保会话目录存在
    settings.monitor.session_dir.mkdir(parents=True, exist_ok=True)

    logger.info("开始 Telegram 用户账号登录")
    logger.info("API ID: {}", settings.api_id)
    logger.info("会话文件将保存至: {}", session_file)
    logger.info("---")

    # 创建客户端（Pyrogram 会在 start() 时交互式提示输入手机号和验证码）
    client = Client(
        name=str(session_path),
        api_id=settings.api_id,
        api_hash=settings.api_hash,
        in_memory=False,
    )

    logger.info("正在启动客户端，请按提示输入...")
    await client.start()

    # 验证登录
    me = await client.get_me()
    logger.info("---")
    logger.info("登录成功！")
    logger.info("  用户 ID: {}", me.id)
    logger.info("  姓名: {} {}", me.first_name or "", me.last_name or "")
    logger.info("  用户名: @{}", me.username or "(无)")
    logger.info("  会话文件: {}", session_file)
    logger.info("")
    logger.info("现在可以运行 `python -m teleharvest` 启动服务")

    await client.stop()


def main() -> None:
    """入口。"""
    try:
        asyncio.run(login())
    except KeyboardInterrupt:
        print("\n登录已取消")
    except Exception as exc:
        print(f"[ERROR] 登录失败: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
