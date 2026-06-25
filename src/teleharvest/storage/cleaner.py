"""过期清理：按保留策略定期清理旧文件与孤儿文件。

触发方式：
    - APScheduler 定时任务（默认每日凌晨执行）
    - 磁盘空间不足时主动触发

清理类型：
    1. 过期清理：删除超过 retention_days 的已下载文件，DB 记录标记为 deleted
    2. 孤儿清理：删除文件系统中存在但 DB 无记录的文件
"""

from __future__ import annotations

import contextlib
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from pathlib import Path

    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    from teleharvest.config.schema import StorageSettings
    from teleharvest.db.repositories.media_repo import MediaRepository


class Cleaner:
    """文件清理器，支持过期清理与孤儿文件检测。"""

    def __init__(
        self,
        settings: StorageSettings,
        media_repo: MediaRepository | None = None,
    ) -> None:
        self._settings = settings
        self._repo = media_repo
        self._scheduler: AsyncIOScheduler | None = None

    async def cleanup_expired(self) -> int:
        """清理过期文件。

        流程：
            1. 查询 DB 中早于 cutoff 的 downloaded 记录
            2. 删除对应文件
            3. DB 记录标记为 deleted
            4. 兜底：扫描文件系统按 mtime 清理无 DB 记录的文件

        Returns:
            已清理的文件数量
        """
        if self._settings.retention_days <= 0:
            logger.debug("未配置保留期限，跳过过期清理")
            return 0

        cutoff = datetime.now() - timedelta(days=self._settings.retention_days)
        logger.info("开始清理 {} 之前的文件", cutoff.strftime("%Y-%m-%d"))

        count = 0

        # 1. DB 记录驱动的清理
        if self._repo is not None:
            count += await self._cleanup_db_records(cutoff)

        # 2. 文件系统兜底清理（无 DB 记录的过期文件）
        count += self._cleanup_by_mtime(cutoff)

        logger.info("过期清理完成，共删除 {} 个文件", count)
        return count

    async def _cleanup_db_records(self, cutoff: datetime) -> int:
        """按 DB 记录清理过期文件。"""
        assert self._repo is not None

        expired_records = await self._repo.find_expired(cutoff)
        if not expired_records:
            return 0

        count = 0
        for media in expired_records:
            file_path = self._resolve_path(media.file_path)
            if file_path is not None:
                try:
                    file_path.unlink(missing_ok=True)
                    count += 1
                    logger.debug(
                        "已删除过期文件: {} (media_id={})",
                        file_path.name,
                        media.id,
                    )
                except OSError as exc:
                    logger.warning(
                        "删除文件失败: {} ({})",
                        file_path,
                        exc,
                    )
            # 无论文件删除是否成功，都标记 DB 记录为 deleted
            await self._repo.mark_deleted(media.id)

        logger.info("DB 驱动清理: 删除 {} 个文件，标记 {} 条记录", count, len(expired_records))
        return count

    def _cleanup_by_mtime(self, cutoff: datetime) -> int:
        """按文件修改时间扫描清理（兜底，处理无 DB 记录的文件）。"""
        count = 0
        root = self._settings.root_dir
        if not root.exists():
            return 0

        for file_path in root.rglob("*"):
            if not file_path.is_file():
                continue
            try:
                mtime = datetime.fromtimestamp(file_path.stat().st_mtime)
                if mtime < cutoff:
                    file_path.unlink(missing_ok=True)
                    count += 1
                    logger.debug("已删除过期文件(兜底): {}", file_path)
            except OSError as exc:
                logger.warning("删除文件失败: {} ({})", file_path, exc)

        return count

    async def cleanup_orphans(self) -> int:
        """清理孤儿文件（文件系统中存在但 DB 无记录）。

        Returns:
            已清理的孤儿文件数量
        """
        if self._repo is None:
            logger.debug("无 DB 仓储，跳过孤儿清理")
            return 0

        root = self._settings.root_dir
        if not root.exists():
            return 0

        # 获取 DB 中所有已下载状态的文件路径
        db_paths = await self._repo.find_all_active_paths()
        db_paths_abs = {self._resolve_path(p) for p in db_paths}

        count = 0
        for file_path in root.rglob("*"):
            if not file_path.is_file():
                continue
            if file_path in db_paths_abs:
                continue
            try:
                file_path.unlink(missing_ok=True)
                count += 1
                logger.debug("已删除孤儿文件: {}", file_path)
            except OSError as exc:
                logger.warning("删除孤儿文件失败: {} ({})", file_path, exc)

        if count > 0:
            logger.info("孤儿清理: 删除 {} 个文件", count)
        return count

    def _resolve_path(self, file_path: str) -> Path | None:
        """将 DB 中存储的路径解析为绝对路径。

        DB 中可能存储绝对路径或相对 root_dir 的路径。
        """
        from pathlib import Path

        p = Path(file_path)
        if p.is_absolute():
            return p if p.exists() else None
        # 相对路径：基于 root_dir 解析
        resolved = self._settings.root_dir / p
        return resolved if resolved.exists() else None

    async def cleanup_all(self) -> dict[str, int]:
        """执行完整清理流程（过期 + 孤儿）。

        Returns:
            各类清理的统计: {"expired": N, "orphans": M}
        """
        expired = await self.cleanup_expired()
        orphans = await self.cleanup_orphans()
        return {"expired": expired, "orphans": orphans}

    def start_scheduled(
        self,
        scheduler: AsyncIOScheduler,
        cron: str = "0 3 * * *",
    ) -> None:
        """注册定时清理任务到 APScheduler。

        Args:
            scheduler: APScheduler 异步调度器
            cron: Cron 表达式，默认每天凌晨 3 点
        """
        from apscheduler.triggers.cron import CronTrigger

        trigger = CronTrigger.from_crontab(cron)
        scheduler.add_job(
            self._scheduled_cleanup,
            trigger=trigger,
            id="cleanup_expired",
            replace_existing=True,
        )
        self._scheduler = scheduler
        logger.info("已注册定时清理任务: cron={}", cron)

    async def _scheduled_cleanup(self) -> None:
        """定时任务回调：执行完整清理。"""
        try:
            stats = await self.cleanup_all()
            logger.info(
                "定时清理完成: 过期={}, 孤儿={}",
                stats["expired"],
                stats["orphans"],
            )
        except Exception as exc:
            logger.exception("定时清理任务异常: {}", exc)

    async def stop(self) -> None:
        """停止清理器（移除定时任务）。"""
        if self._scheduler is not None:
            with contextlib.suppress(Exception):
                self._scheduler.remove_job("cleanup_expired")
            self._scheduler = None
        logger.info("文件清理器已停止")
