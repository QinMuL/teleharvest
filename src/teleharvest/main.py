"""应用主入口：生命周期管理、信号处理、模块编排。

职责：
    1. 加载并校验配置
    2. 初始化日志、数据库、代理、监控、调度等组件
    3. 注册信号处理（SIGINT/SIGTERM）实现优雅停止
    4. 启动主事件循环并等待终止信号

组件初始化顺序（依赖关系自底向上）：
    Logger → Database → ProxyManager → StorageManager → DedupChecker → DownloadEngine
    → Cleaner → Scheduler → MonitorClient
停止顺序与初始化相反。
"""

from __future__ import annotations

import asyncio
import signal
import sys
from typing import TYPE_CHECKING

from teleharvest import __version__
from teleharvest.config.settings import load_settings
from teleharvest.utils.logger import setup_logging

if TYPE_CHECKING:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    from teleharvest.bot.notifier import BotNotifier
    from teleharvest.config.settings import AppSettings  # type: ignore[attr-defined]
    from teleharvest.core.scheduler import Scheduler
    from teleharvest.db.session import DatabaseSession
    from teleharvest.downloader.engine import DownloadEngine
    from teleharvest.monitor.client import MonitorClient
    from teleharvest.proxy.manager import ProxyManager
    from teleharvest.storage.cleaner import Cleaner
    from teleharvest.storage.manager import StorageManager


async def run(settings: AppSettings) -> None:
    """主异步入口：编排各模块启动与停止。

    Args:
        settings: 已校验的应用配置
    """
    from loguru import logger

    from teleharvest.core.scheduler import Scheduler
    from teleharvest.db.repositories.channel_repo import ChannelRepository
    from teleharvest.db.repositories.media_repo import MediaRepository
    from teleharvest.db.session import DatabaseSession
    from teleharvest.downloader.engine import DownloadEngine
    from teleharvest.monitor.client import MonitorClient
    from teleharvest.proxy.manager import ProxyManager
    from teleharvest.storage.cleaner import Cleaner
    from teleharvest.storage.dedup import DedupChecker
    from teleharvest.storage.manager import StorageManager

    logger.info("TeleHarvest v{} 启动中...", __version__)
    logger.info(
        "配置概览: 监控频道数={}, 下载并发={}, 存储根目录={}",
        len(settings.monitor.channels),
        settings.downloader.max_concurrency,
        settings.storage.root_dir,
    )

    # ===== 组件引用 =====
    db: DatabaseSession | None = None
    proxy: ProxyManager | None = None
    storage: StorageManager | None = None
    downloader: DownloadEngine | None = None
    scheduler: Scheduler | None = None
    monitor: MonitorClient | None = None
    cleaner: Cleaner | None = None
    cleanup_scheduler: AsyncIOScheduler | None = None
    notifier: BotNotifier | None = None

    try:
        # 1. 初始化数据库
        db = DatabaseSession(settings.database)
        await db.start()

        # 2. 同步频道配置到数据库
        channel_repo = ChannelRepository(db)
        sync_result = await channel_repo.sync_from_config(settings.monitor.channels)
        logger.info(
            "频道配置同步完成: 新增={}, 更新={}, 禁用={}",
            sync_result["inserted"],
            sync_result["updated"],
            sync_result["disabled"],
        )

        # 3. 初始化代理管理器
        proxy = ProxyManager(settings.proxy)
        await proxy.start()

        # 4. 初始化存储管理器
        storage = StorageManager(settings.storage)
        free_gb = storage.check_free_space()
        logger.info("存储就绪: root={}, 可用空间={:.2f}GB", settings.storage.root_dir, free_gb)

        # 5. 初始化去重检查器与媒体仓储
        media_repo = MediaRepository(db)
        dedup_checker = DedupChecker(
            algorithm=settings.storage.dedup_algorithm,
            media_repo=media_repo if settings.storage.enable_dedup else None,
        )

        # 6. 初始化下载引擎
        downloader = DownloadEngine(
            settings=settings.downloader,
            storage_manager=storage,
            dedup_checker=dedup_checker if settings.storage.enable_dedup else None,
            media_repo=media_repo,
            channel_repo=channel_repo,
        )
        logger.info(
            "下载引擎就绪: 并发={}, 重试={}, 断点续传={}",
            settings.downloader.max_concurrency,
            settings.downloader.max_retries,
            settings.downloader.enable_resume,
        )

        # 7. 初始化文件清理器与定时任务
        if settings.storage.retention_days > 0 or settings.storage.cleanup_orphans:
            from apscheduler.schedulers.asyncio import AsyncIOScheduler

            cleaner = Cleaner(
                settings=settings.storage,
                media_repo=media_repo,
            )
            cleanup_scheduler = AsyncIOScheduler()
            cleanup_scheduler.start()
            cleaner.start_scheduled(
                cleanup_scheduler,
                cron=settings.storage.cleanup_cron,
            )
            logger.info(
                "文件清理器就绪: 保留={}天, 孤儿清理={}, cron={}",
                settings.storage.retention_days,
                settings.storage.cleanup_orphans,
                settings.storage.cleanup_cron,
            )

        # 8. 初始化任务调度器（接入下载引擎）
        scheduler = Scheduler(settings.downloader, download_engine=downloader)
        await scheduler.start()

        # 9. 初始化监控客户端并订阅频道
        monitor = MonitorClient(
            settings=settings,
            scheduler=scheduler,
            proxy_manager=proxy,
            channel_repo=channel_repo,
        )
        await monitor.start()

        # 10. 初始化 Bot 通知器（在监控客户端启动后注入下载引擎）
        if settings.bot_token and settings.bot.notify_chat_id and downloader is not None:
            from teleharvest.bot.commands import COMMAND_LIST, BotCommandHandler
            from teleharvest.bot.notifier import BotNotifier

            notifier = BotNotifier(
                client=monitor.client,
                settings=settings.bot,
            )
            downloader.set_notifier(notifier)
            logger.info(
                "Bot 通知器就绪: chat_id={}, 进度间隔={}s, 完成通知={}, 错误通知={}",
                settings.bot.notify_chat_id,
                settings.bot.progress_interval,
                settings.bot.notify_on_complete,
                settings.bot.notify_on_error,
            )

            # 11. 注册 Bot 命令处理器（Phase C+D+E：/start /help /status /history /dl /pause /resume /stats）
            command_handler = BotCommandHandler(
                settings=settings,
                media_repo=media_repo,
                channel_repo=channel_repo,
                download_engine=downloader,
                client=monitor.client,
                scheduler=scheduler,
            )
            await monitor.register_bot_command_handler(
                handler=command_handler,
                chat_id=settings.bot.notify_chat_id,
                commands=COMMAND_LIST,
            )
        elif settings.bot_token and not settings.bot.notify_chat_id:
            logger.info("Bot 通知器未启用：未配置 bot.notify_chat_id，跳过推送通知")

        await monitor.subscribe_channels()

        logger.info("TeleHarvest 已就绪，等待消息... (Ctrl+C 退出)")

        # 保持运行直到被取消
        await asyncio.Event().wait()

    except asyncio.CancelledError:
        logger.info("收到停止信号，开始优雅关闭...")
        raise
    except Exception as exc:
        logger.exception("TeleHarvest 运行异常: {}", exc)
        raise
    finally:
        # 按逆序停止组件
        logger.info("正在关闭各组件...")
        if monitor:
            await monitor.stop()
        if scheduler:
            await scheduler.stop()
        if cleaner:
            await cleaner.stop()
        if cleanup_scheduler:
            cleanup_scheduler.shutdown(wait=False)
        if downloader:
            await downloader.stop()
        if notifier:
            await notifier.stop()
        if proxy:
            await proxy.stop()
        if db:
            await db.stop()
        logger.info("所有组件已关闭")


def main() -> None:
    """同步入口点：加载配置、启动事件循环。"""
    # 1. 加载配置（失败即退出，fail-fast）
    try:
        settings = load_settings()
    except Exception as exc:
        print(f"[FATAL] 配置加载失败: {exc}", file=sys.stderr)
        sys.exit(1)

    # 2. 初始化日志系统
    setup_logging(settings.logging)

    # 3. 启动事件循环
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    main_task = loop.create_task(run(settings))

    def _shutdown(signum: int, _frame: object) -> None:
        from loguru import logger as _logger

        _logger.info("收到信号 {}，取消主任务", signum)
        if not main_task.done():
            main_task.cancel()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _shutdown, sig, None)
        except NotImplementedError:
            # Windows 不支持 add_signal_handler，使用 signal.signal 兜底
            signal.signal(sig, _shutdown)

    try:
        loop.run_until_complete(main_task)
    except asyncio.CancelledError:
        pass
    except KeyboardInterrupt:
        pass
    finally:
        loop.close()
        from loguru import logger as _logger

        _logger.info("TeleHarvest 已停止")


if __name__ == "__main__":
    main()
