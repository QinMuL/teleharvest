"""下载引擎：基于 Pyrogram download_media 的异步下载核心。

完整下载流程：
    1. 磁盘空间检查
    2. 消息级去重（channel + message_id 是否已下载）
    3. 构建目标存储路径（通过 StorageManager）
    4. 断点续传检查
    5. 调用 Pyrogram message.download()（带进度回调）
    6. 失败重试与指数退避
    7. 计算文件哈希
    8. 哈希级去重（相同内容不同消息）
    9. 写入 Media 记录到数据库
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, cast

from loguru import logger

from teleharvest.downloader.progress import ProgressTracker
from teleharvest.downloader.resume import ResumeManager

if TYPE_CHECKING:
    from pathlib import Path

    from pyrogram.types import Message

    from teleharvest.bot.notifier import BotNotifier
    from teleharvest.config.schema import DownloaderSettings
    from teleharvest.core.task import DownloadTask
    from teleharvest.db.repositories.channel_repo import ChannelRepository
    from teleharvest.db.repositories.media_repo import MediaRepository
    from teleharvest.storage.dedup import DedupChecker
    from teleharvest.storage.manager import StorageManager


class DownloadEngine:
    """下载引擎，封装完整的媒体下载链路。"""

    def __init__(
        self,
        settings: DownloaderSettings,
        storage_manager: StorageManager,
        dedup_checker: DedupChecker | None = None,
        media_repo: MediaRepository | None = None,
        channel_repo: ChannelRepository | None = None,
    ) -> None:
        self._settings = settings
        self._storage = storage_manager
        self._dedup = dedup_checker
        self._media_repo = media_repo
        self._channel_repo = channel_repo
        # 延迟注入：由 main.py 在 MonitorClient 启动后调用 set_notifier()
        self._notifier: BotNotifier | None = None
        self._active_downloads: dict[int, DownloadContext] = {}
        # 暂停状态：暂停时拒绝新下载任务（进行中的不中断）
        self._paused: bool = False

    def set_notifier(self, notifier: BotNotifier) -> None:
        """注入 Bot 通知器，启用下载进度与完成/错误通知。"""
        self._notifier = notifier
        logger.info("已注入 Bot 通知器")

    async def download(self, task: DownloadTask) -> DownloadResult:
        """执行完整的下载流程。

        Args:
            task: 下载任务（包含消息引用和元数据）

        Returns:
            下载结果
        """
        message: Message | None = cast("Message | None", task._message_ref)
        if message is None:
            return DownloadResult(success=False, error="任务缺少消息引用")

        # 0. 暂停检查：暂停时拒绝新任务（进行中的不中断）
        if self._paused:
            logger.info("下载引擎已暂停，拒绝任务: {}", task.unique_key)
            return DownloadResult(success=False, error="engine_paused")

        # 1. 消息级去重：检查是否已下载过
        if self._dedup is not None:
            already = await self._dedup.is_message_downloaded(task.channel_id, task.message_id)
            if already:
                logger.info("消息已下载过，跳过: {}", task.unique_key)
                return DownloadResult(
                    success=True, file_path="", file_size=0, error="already_downloaded"
                )

        # 2. 磁盘空间检查
        free_gb = self._storage.check_free_space()
        if free_gb < self._settings.chunk_size_mb / 1024:
            return DownloadResult(success=False, error=f"磁盘空间不足: {free_gb:.2f}GB")

        # 3. 构建目标路径
        dest_path = self._storage.build_path(
            channel_alias=task.channel_alias,
            media_type=task.media_type,
            original_name=task.file_name,
            message_id=task.message_id,
            timestamp=task.message_date,
        )

        # 4. 断点续传检查
        if self._settings.enable_resume:
            existing_size = ResumeManager.check_partial(dest_path, task.file_size)
            if existing_size == task.file_size and task.file_size > 0:
                logger.info("文件已完整存在，跳过下载: {}", dest_path.name)
                await self._record_media(task, dest_path, task.file_size)
                return DownloadResult(
                    success=True, file_path=str(dest_path), file_size=task.file_size
                )

        # 5. 带重试的下载
        ctx = DownloadContext(task.message_id, task=task)
        self._active_downloads[task.message_id] = ctx

        # 通知下载开始（创建进度消息）
        if self._notifier is not None:
            await self._notifier.notify_download_start(task, task.file_size)

        try:
            result = await self._download_with_retry(task, message, dest_path, ctx)
        finally:
            self._active_downloads.pop(task.message_id, None)

        # 通知下载结果（跳过去重场景，避免无意义消息）
        if self._notifier is not None and result.error != "already_downloaded":
            if result.success:
                await self._notifier.notify_download_complete(
                    task, result.file_path, result.file_size
                )
            else:
                await self._notifier.notify_download_error(task, result.error)

        return result

    async def _download_with_retry(
        self,
        task: DownloadTask,
        message: Message,
        dest_path: Path,
        ctx: DownloadContext,
    ) -> DownloadResult:
        """带指数退避重试的下载。

        重试策略：
            - 最多 max_retries 次
            - 间隔 = retry_base_delay * 2^attempt
            - FloodWait 异常遵守 Telegram 要求的等待时间
        """
        max_retries = self._settings.max_retries
        base_delay = self._settings.retry_base_delay

        # 进度回调闭包：桥接到 Bot 通知器
        async def _on_progress(current: int, total: int) -> None:
            if self._notifier is not None:
                await self._notifier.notify_progress(task, current, total)

        for attempt in range(max_retries + 1):
            if ctx.cancelled:
                return DownloadResult(success=False, error="cancelled")

            try:
                logger.info(
                    "开始下载: {} -> {} (尝试 {}/{})",
                    task.unique_key,
                    dest_path.name,
                    attempt + 1,
                    max_retries + 1,
                )

                # 进度追踪器（注入 BotNotifier 回调）
                progress = ProgressTracker(
                    callback=_on_progress,
                    log_interval_percent=10,
                )

                # 调用 Pyrogram 下载
                await asyncio.wait_for(
                    message.download(
                        file_name=str(dest_path),
                        progress=progress.on_progress,
                    ),
                    timeout=self._settings.timeout,
                )

                file_size = dest_path.stat().st_size if dest_path.exists() else 0
                logger.info(
                    "下载完成: {} ({} bytes)",
                    dest_path.name,
                    file_size,
                )

                # 6. 智能重命名（从 caption 或视频元数据提取标题）
                dest_path = self._smart_rename(task, dest_path)

                # 7. 计算哈希并记录
                await self._record_media(task, dest_path, file_size)

                return DownloadResult(
                    success=True,
                    file_path=str(dest_path),
                    file_size=file_size,
                )

            except TimeoutError:
                error_msg = f"下载超时 (>{self._settings.timeout}s)"
                logger.warning("下载超时: {}, {}", task.unique_key, error_msg)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                error_msg = str(exc)
                # 检测 FloodWait
                wait_time = self._extract_flood_wait(exc)
                if wait_time:
                    logger.warning(
                        "触发 Telegram 限流: {}, 等待 {}s",
                        task.unique_key,
                        wait_time,
                    )
                    await asyncio.sleep(wait_time)
                    continue
                logger.warning(
                    "下载失败: {}, attempt={}, error={}",
                    task.unique_key,
                    attempt + 1,
                    error_msg,
                )

            # 重试退避
            if attempt < max_retries:
                delay = base_delay * (2**attempt)
                logger.info("等待 {:.1f}s 后重试...", delay)
                await asyncio.sleep(delay)
            else:
                logger.error("下载失败，重试已耗尽: {}", task.unique_key)
                return DownloadResult(success=False, error=error_msg or "unknown error")

        return DownloadResult(success=False, error="max retries exceeded")

    def _smart_rename(self, task: DownloadTask, file_path: Path) -> Path:
        """根据标题智能重命名文件。

        优先级：消息 caption 第一行 > ffprobe 元数据 title > 保留原名。

        Args:
            task: 下载任务
            file_path: 已下载文件路径

        Returns:
            重命名后的路径（若无重命名则返回原路径）
        """
        from teleharvest.utils.naming import (
            build_smart_filename,
            extract_title_from_caption,
            extract_title_from_metadata,
        )

        # 1. 从消息 caption 提取标题
        title = extract_title_from_caption(task.caption)

        # 2. 从视频/音频元数据提取标题
        if not title:
            title = extract_title_from_metadata(file_path)

        # 3. 无有效标题，保持原文件名
        if not title:
            return file_path

        # 4. 生成新文件名
        new_name = build_smart_filename(
            original_name=task.file_name,
            title=title,
            media_type=task.media_type,
            message_id=task.message_id,
            timestamp=task.message_date,
        )

        if new_name == file_path.name:
            return file_path

        # 5. 重命名（处理冲突）
        new_path = file_path.parent / new_name
        if new_path.exists():
            logger.debug("目标文件已存在，跳过重命名: {}", new_path)
            return file_path

        try:
            file_path.rename(new_path)
            logger.info("智能重命名: {} -> {}", file_path.name, new_path.name)
            return new_path
        except OSError as exc:
            logger.warning("重命名失败: {} ({})", file_path.name, exc)
            return file_path

    async def _record_media(
        self,
        task: DownloadTask,
        file_path: Path,
        file_size: int,
    ) -> None:
        """下载成功后计算哈希并写入 Media 记录。

        Args:
            task: 下载任务
            file_path: 已下载文件路径
            file_size: 文件大小
        """
        if self._media_repo is None:
            return

        # 计算哈希
        file_hash = ""
        if self._dedup is not None:
            try:
                file_hash = self._dedup.compute_hash(file_path)
            except Exception as exc:
                logger.warning("计算文件哈希失败: {}, {}", file_path.name, exc)

        # 哈希级去重
        if file_hash and self._dedup is not None and await self._dedup.is_duplicate(file_hash):
            logger.info("哈希重复，删除新文件: hash={}", file_hash[:16])
            file_path.unlink(missing_ok=True)
            return

        # 写入数据库记录
        try:
            from teleharvest.db.models import Media

            # 查找数据库中的频道 ID（外键）
            db_channel_id: int | None = None
            if self._channel_repo is not None:
                channel = await self._channel_repo.get_by_channel_id(task.channel_id)
                if channel is not None:
                    db_channel_id = channel.id

            if db_channel_id is None:
                logger.warning(
                    "未找到频道记录，跳过媒体入库: channel_id={}",
                    task.channel_id,
                )
                return

            media = Media(
                channel_id=db_channel_id,
                message_id=task.message_id,
                media_type=task.media_type,
                original_name=task.file_name,
                file_path=str(file_path),
                file_size=file_size,
                file_hash=file_hash,
                status="downloaded",
            )
            await self._media_repo.create(media)
            logger.debug("已写入媒体记录: {}, hash={}", file_path.name, file_hash[:16] or "N/A")
        except Exception as exc:
            logger.warning("写入媒体记录失败: {}, {}", file_path.name, exc)

    @staticmethod
    def _extract_flood_wait(exc: Exception) -> int | None:
        """从异常中提取 FloodWait 等待时间（秒）。

        Pyrogram 的 FloodWait 异常包含 value 属性表示等待秒数。
        """
        # Pyrogram FloodWait
        if hasattr(exc, "value"):
            try:
                return int(exc.value)
            except (TypeError, ValueError):
                pass
        return None

    @property
    def active_count(self) -> int:
        """当前活跃下载数量。"""
        return len(self._active_downloads)

    @property
    def is_paused(self) -> bool:
        """下载引擎是否已暂停。"""
        return self._paused

    def pause(self) -> None:
        """暂停下载引擎（拒绝新任务，进行中的不中断）。"""
        self._paused = True
        logger.info("下载引擎已暂停: 活跃任务={} 个", len(self._active_downloads))

    def resume(self) -> None:
        """恢复下载引擎。"""
        self._paused = False
        logger.info("下载引擎已恢复")

    def get_active_tasks(self) -> list[DownloadContext]:
        """获取所有活跃下载的上下文（用于状态查询）。"""
        return list(self._active_downloads.values())

    async def cancel(self, message_id: int) -> None:
        """取消正在进行的下载。"""
        if message_id in self._active_downloads:
            ctx = self._active_downloads[message_id]
            ctx.cancelled = True
            logger.info("已请求取消下载: message_id={}", message_id)

    async def stop(self) -> None:
        """停止所有下载。"""
        for msg_id in list(self._active_downloads):
            await self.cancel(msg_id)
        logger.info("下载引擎已停止")


class DownloadContext:
    """单个下载任务的运行时上下文。"""

    def __init__(self, message_id: int, task: DownloadTask | None = None) -> None:
        self.message_id = message_id
        self.task = task
        self.cancelled = False
        self.downloaded_bytes = 0
        self.total_bytes = 0
        self.retries = 0
        self.started_at: float | None = None


class DownloadResult:
    """下载结果。"""

    def __init__(
        self,
        success: bool,
        file_path: str = "",
        file_size: int = 0,
        error: str = "",
    ) -> None:
        self.success = success
        self.file_path = file_path
        self.file_size = file_size
        self.error = error

    def __str__(self) -> str:
        if self.success:
            return f"DownloadResult(ok, {self.file_size} bytes, {self.file_path})"
        return f"DownloadResult(failed: {self.error})"


class DownloadError(Exception):
    """下载失败异常。"""
