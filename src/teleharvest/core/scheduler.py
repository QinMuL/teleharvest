"""任务调度器：管理下载任务队列、并发限流、定时任务。

设计要点：
    - 使用 asyncio.Semaphore 控制下载并发数
    - 任务队列基于 asyncio.Queue（进程内内存队列）
    - P1 阶段：仅打印日志验证链路；P2 阶段接入 DownloadEngine
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING

from loguru import logger

from teleharvest.downloader.engine import DownloadError

if TYPE_CHECKING:
    from teleharvest.config.schema import DownloaderSettings
    from teleharvest.core.task import DownloadTask
    from teleharvest.downloader.engine import DownloadEngine


class Scheduler:
    """任务调度器，协调监控模块与下载模块。

    职责：
        1. 接收监控模块产生的下载任务
        2. 按并发上限调度执行
        3. 管理任务生命周期（pending → running → done/failed）
    """

    def __init__(
        self,
        downloader_settings: DownloaderSettings,
        download_engine: DownloadEngine | None = None,
    ) -> None:
        self._semaphore = asyncio.Semaphore(downloader_settings.max_concurrency)
        self._engine = download_engine
        self._queue: asyncio.Queue[DownloadTask] = asyncio.Queue()
        self._running = False
        self._dispatch_task: asyncio.Task[None] | None = None
        self._total_processed = 0
        self._total_succeeded = 0
        self._total_failed = 0

    async def start(self) -> None:
        """启动调度循环。"""
        self._running = True
        self._dispatch_task = asyncio.create_task(self._dispatch_loop())
        logger.info(
            "任务调度器已启动: 最大并发={}",
            self._semaphore._value,
        )

    async def stop(self) -> None:
        """停止调度，等待进行中的任务完成。"""
        self._running = False
        # 等待队列中的任务处理完毕
        await self._queue.join()
        if self._dispatch_task:
            self._dispatch_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._dispatch_task
            self._dispatch_task = None
        logger.info(
            "任务调度器已停止: 处理={}, 成功={}, 失败={}",
            self._total_processed,
            self._total_succeeded,
            self._total_failed,
        )

    async def enqueue(self, task: DownloadTask) -> None:
        """将下载任务加入队列。

        Args:
            task: 下载任务对象
        """
        await self._queue.put(task)
        logger.debug("任务入队: {} (队列长度={})", task.unique_key, self._queue.qsize())

    async def _dispatch_loop(self) -> None:
        """调度循环：从队列取任务，在信号量控制下执行。"""
        while self._running:
            try:
                task = await self._queue.get()
            except asyncio.CancelledError:
                break

            async with self._semaphore:
                try:
                    await self._execute(task)
                    self._total_succeeded += 1
                except Exception as exc:
                    self._total_failed += 1
                    logger.error(
                        "任务执行失败: {}, error={}",
                        task.unique_key,
                        exc,
                    )
                finally:
                    self._total_processed += 1
                    self._queue.task_done()

    async def _execute(self, task: DownloadTask) -> None:
        """执行单个下载任务。

        P1 阶段：仅打印日志，验证监控→调度链路。
        P2 阶段：调用 DownloadEngine 执行真正下载。
        """
        if self._engine is None:
            # 无引擎模式（P1 兼容）：仅记录日志
            logger.info(
                "[无引擎模式] 收到下载任务: {} ({}), 文件大小: {:.1f}MB",
                task.unique_key,
                task.file_name,
                task.size_mb,
            )
            return

        # P2: 调用下载引擎
        result = await self._engine.download(task)

        if result.success:
            if result.error == "already_downloaded":
                logger.info("任务跳过（已下载）: {}", task.unique_key)
            else:
                logger.info(
                    "任务完成: {} -> {} ({} bytes)",
                    task.unique_key,
                    result.file_path,
                    result.file_size,
                )
        else:
            logger.error(
                "任务失败: {}, error={}",
                task.unique_key,
                result.error,
            )
            raise DownloadError(result.error)

    @property
    def stats(self) -> dict[str, int]:
        """调度器统计。"""
        return {
            "queue_size": self._queue.qsize(),
            "processed": self._total_processed,
            "succeeded": self._total_succeeded,
            "failed": self._total_failed,
        }
