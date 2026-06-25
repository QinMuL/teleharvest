"""消息处理器：接收新消息，过滤后构造 DownloadTask 入队。

每个订阅的频道对应一个 MessageHandler 实例。
处理器负责：
    1. 从 Pyrogram Message 提取媒体元数据
    2. 应用过滤规则（类型/扩展名/大小/关键词）
    3. 构造 DownloadTask 并入队调度器
    4. 更新频道最后处理的消息 ID（断点续传）
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from loguru import logger

from teleharvest.core.task import DownloadTask
from teleharvest.monitor.filters import MessageFilter

if TYPE_CHECKING:
    from typing import Literal

    from pyrogram import Client  # type: ignore[attr-defined]
    from pyrogram.types import Message

    from teleharvest.config.schema import ChannelConfig, MediaFilter
    from teleharvest.core.scheduler import Scheduler
    from teleharvest.db.repositories.channel_repo import ChannelRepository


class MessageHandler:
    """消息处理器：绑定到频道，过滤消息并生成下载任务。"""

    def __init__(
        self,
        channel: ChannelConfig,
        default_filters: MediaFilter,
        scheduler: Scheduler,
        channel_repo: ChannelRepository | None = None,
    ) -> None:
        # 频道专属规则优先，否则使用全局默认规则
        rules = channel.filters or default_filters
        self._filter = MessageFilter(rules)
        self._channel = channel
        self._scheduler = scheduler
        self._channel_repo = channel_repo
        self._processed_count = 0
        self._downloaded_count = 0
        self._skipped_count = 0

    async def handle(self, client: Client, message: Message) -> None:
        """处理单条消息（Pyrogram 回调入口）。

        Args:
            client: Pyrogram 客户端
            message: 收到的消息
        """
        self._processed_count += 1

        # 1. 过滤判定
        if not self._filter.should_download(message):
            self._skipped_count += 1
            logger.debug(
                "频道 {} 消息 {} 未通过过滤，跳过",
                self._channel.alias,
                message.id,
            )
            await self._update_last_message(message.id)
            return

        # 2. 提取媒体信息并构造下载任务
        task = self._build_task(message)
        if task is None:
            self._skipped_count += 1
            logger.debug(
                "频道 {} 消息 {} 无法提取媒体信息，跳过",
                self._channel.alias,
                message.id,
            )
            await self._update_last_message(message.id)
            return

        # 3. 入队
        self._downloaded_count += 1
        logger.info(
            "频道 {} 消息 {} 通过过滤，加入下载队列: {}",
            self._channel.alias,
            message.id,
            task,
        )
        await self._scheduler.enqueue(task)
        await self._update_last_message(message.id)

    def _build_task(self, message: Message) -> DownloadTask | None:
        """从 Pyrogram Message 构造 DownloadTask。

        Args:
            message: Pyrogram 消息对象

        Returns:
            下载任务，或 None（无法提取媒体信息）
        """
        info = self._filter.extract_media_info(message)
        if info is None:
            return None
        media_type, file_name, file_size = info

        return DownloadTask(
            message_id=message.id,
            channel_id=self._channel.id,
            channel_alias=self._channel.alias,
            media_type=cast("Literal['audio', 'video', 'document', 'photo']", media_type),
            file_name=file_name,
            file_size=file_size,
            caption=message.caption or message.text or "",
            message_date=message.date,
            _message_ref=message,
        )

    async def _update_last_message(self, message_id: int) -> None:
        """更新频道最后处理的消息 ID。"""
        if self._channel_repo is None:
            return
        try:
            await self._channel_repo.update_last_message_id(self._channel.id, message_id)
        except Exception as exc:
            logger.warning(
                "更新频道 {} 最后消息 ID 失败: {}",
                self._channel.alias,
                exc,
            )

    @property
    def stats(self) -> dict[str, int]:
        """返回处理统计。"""
        return {
            "processed": self._processed_count,
            "downloaded": self._downloaded_count,
            "skipped": self._skipped_count,
        }
