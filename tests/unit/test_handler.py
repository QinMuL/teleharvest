"""单元测试：MessageHandler 消息处理与入队逻辑。"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from teleharvest.config.schema import ChannelConfig, MediaFilter
from teleharvest.monitor.handler import MessageHandler

if TYPE_CHECKING:
    from teleharvest.core.task import DownloadTask


@pytest.fixture
def mock_scheduler() -> MagicMock:
    """模拟调度器。"""
    scheduler = MagicMock()
    scheduler.enqueue = AsyncMock()
    return scheduler


@pytest.fixture
def video_message() -> MagicMock:
    """构造含视频的消息 mock。"""
    msg = MagicMock()
    msg.id = 100
    msg.video.file_name = "test_video.mp4"
    msg.video.file_size = 50 * 1024 * 1024  # 50MB
    msg.audio = None
    msg.document = None
    msg.photo = None
    msg.caption = "精彩视频"
    msg.text = ""
    msg.date = MagicMock()
    return msg


class TestMessageHandler:
    """MessageHandler 测试。"""

    @pytest.mark.asyncio
    async def test_message_passes_filter_and_enqueues(
        self,
        mock_scheduler: MagicMock,
        video_message: MagicMock,
    ) -> None:
        """通过过滤的消息应入队。"""
        channel = ChannelConfig(id="@test", alias="测试")
        handler = MessageHandler(
            channel=channel,
            default_filters=MediaFilter(),
            scheduler=mock_scheduler,
        )

        await handler.handle(MagicMock(), video_message)

        # 验证任务入队
        mock_scheduler.enqueue.assert_awaited_once()
        task: DownloadTask = mock_scheduler.enqueue.await_args.args[0]
        assert task.message_id == 100
        assert task.channel_alias == "测试"
        assert task.media_type == "video"
        assert task.file_name == "test_video.mp4"

        # 验证统计
        assert handler.stats["processed"] == 1
        assert handler.stats["downloaded"] == 1
        assert handler.stats["skipped"] == 0

    @pytest.mark.asyncio
    async def test_message_filtered_out(
        self,
        mock_scheduler: MagicMock,
        video_message: MagicMock,
    ) -> None:
        """未通过过滤的消息不应入队。"""
        channel = ChannelConfig(id="@test", alias="测试")
        handler = MessageHandler(
            channel=channel,
            default_filters=MediaFilter(types=["audio"]),  # 仅接受音频
            scheduler=mock_scheduler,
        )

        await handler.handle(MagicMock(), video_message)

        mock_scheduler.enqueue.assert_not_awaited()
        assert handler.stats["processed"] == 1
        assert handler.stats["downloaded"] == 0
        assert handler.stats["skipped"] == 1

    @pytest.mark.asyncio
    async def test_no_media_skipped(
        self,
        mock_scheduler: MagicMock,
    ) -> None:
        """无媒体内容的消息应被跳过。"""
        msg = MagicMock()
        msg.id = 200
        msg.video = None
        msg.audio = None
        msg.document = None
        msg.photo = None
        msg.caption = ""
        msg.text = "纯文本消息"
        msg.date = MagicMock()

        channel = ChannelConfig(id="@test", alias="测试")
        handler = MessageHandler(
            channel=channel,
            default_filters=MediaFilter(),
            scheduler=mock_scheduler,
        )

        await handler.handle(MagicMock(), msg)

        mock_scheduler.enqueue.assert_not_awaited()
        assert handler.stats["skipped"] == 1

    @pytest.mark.asyncio
    async def test_channel_specific_filters_override_default(
        self,
        mock_scheduler: MagicMock,
        video_message: MagicMock,
    ) -> None:
        """频道专属过滤规则应覆盖默认规则。"""
        channel = ChannelConfig(
            id="@test",
            alias="测试",
            filters=MediaFilter(types=["audio"]),  # 频道专属：仅音频
        )
        # 默认规则：接受所有
        handler = MessageHandler(
            channel=channel,
            default_filters=MediaFilter(),  # 默认接受所有
            scheduler=mock_scheduler,
        )

        await handler.handle(MagicMock(), video_message)

        # 频道专属规则生效，视频被拒绝
        mock_scheduler.enqueue.assert_not_awaited()
        assert handler.stats["skipped"] == 1

    @pytest.mark.asyncio
    async def test_updates_last_message_id(
        self,
        mock_scheduler: MagicMock,
        video_message: MagicMock,
    ) -> None:
        """处理消息后应更新频道的最后消息 ID。"""
        channel = ChannelConfig(id="@test", alias="测试")
        channel_repo = MagicMock()
        channel_repo.update_last_message_id = AsyncMock()

        handler = MessageHandler(
            channel=channel,
            default_filters=MediaFilter(),
            scheduler=mock_scheduler,
            channel_repo=channel_repo,
        )

        await handler.handle(MagicMock(), video_message)

        channel_repo.update_last_message_id.assert_awaited_once_with("@test", 100)
