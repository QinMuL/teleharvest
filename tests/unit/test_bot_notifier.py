"""单元测试：BotNotifier 通知器。

验证进度消息创建、节流编辑、完成/错误通知、状态清理等行为。
使用 MagicMock 模拟 Pyrogram Client，避免真实网络调用。
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from teleharvest.bot.notifier import (
    BotNotifier,
    _format_bytes,
    _format_duration,
    _progress_bar,
)
from teleharvest.config.schema import BotSettings
from teleharvest.core.task import DownloadTask

if TYPE_CHECKING:
    from collections.abc import Callable


@pytest.fixture
def bot_settings() -> BotSettings:
    """已启用 Bot 推送配置。"""
    return BotSettings(
        notify_chat_id=123456,
        progress_interval=2.0,
        progress_percent_step=5,
        notify_on_complete=True,
        notify_on_error=True,
        enable_progress_message=True,
    )


@pytest.fixture
def disabled_settings() -> BotSettings:
    """未配置 chat_id（推送禁用）。"""
    return BotSettings(notify_chat_id=0)


@pytest.fixture
def sample_task() -> DownloadTask:
    """示例下载任务。"""
    return DownloadTask(
        message_id=100,
        channel_id="@test",
        channel_alias="测试频道",
        media_type="video",
        file_name="test.mp4",
        file_size=1024 * 1024,
    )


def _make_mock_client() -> MagicMock:
    """构造 mock Pyrogram Client，预设 send_message 返回可编辑的 msg。"""
    client = MagicMock()
    client.send_message = AsyncMock(return_value=MagicMock(edit_text=AsyncMock()))
    return client


@pytest.fixture
def make_notifier(
    bot_settings: BotSettings,
) -> Callable[[BotSettings | None], tuple[BotNotifier, MagicMock]]:
    """构造带 mock Client 的 notifier 工厂，返回 (notifier, mock_client)。"""

    def _factory(
        settings: BotSettings | None = None,
    ) -> tuple[BotNotifier, MagicMock]:
        client = _make_mock_client()
        notifier = BotNotifier(client=client, settings=settings or bot_settings)
        return notifier, client

    return _factory


class TestFormatBytes:
    def test_bytes(self) -> None:
        assert _format_bytes(0) == "0.0 B"
        assert _format_bytes(1024) == "1.0 KB"

    def test_gb(self) -> None:
        assert _format_bytes(1024**3) == "1.0 GB"


class TestProgressBar:
    def test_zero(self) -> None:
        assert _progress_bar(0, 10) == "░" * 10

    def test_full(self) -> None:
        assert _progress_bar(100, 10) == "█" * 10

    def test_half(self) -> None:
        assert _progress_bar(50, 10) == "█" * 5 + "░" * 5


class TestFormatDuration:
    def test_seconds(self) -> None:
        assert _format_duration(30) == "30s"

    def test_minutes(self) -> None:
        assert _format_duration(90) == "1m30s"

    def test_hours(self) -> None:
        assert _format_duration(3700) == "1h1m"


class TestBotNotifierEnabled:
    def test_enabled_when_chat_id_set(self, bot_settings: BotSettings) -> None:
        notifier = BotNotifier(client=MagicMock(), settings=bot_settings)
        assert notifier.enabled is True

    def test_disabled_when_chat_id_zero(self, disabled_settings: BotSettings) -> None:
        notifier = BotNotifier(client=MagicMock(), settings=disabled_settings)
        assert notifier.enabled is False


class TestBotNotifierNotifications:
    """通知器调用测试。"""

    async def test_notify_start_creates_message(
        self,
        make_notifier: Callable[[BotSettings | None], tuple[BotNotifier, MagicMock]],
        bot_settings: BotSettings,
        sample_task: DownloadTask,
    ) -> None:
        notifier, client = make_notifier(bot_settings)
        expected_msg = client.send_message.return_value

        await notifier.notify_download_start(sample_task, 1024 * 1024)

        client.send_message.assert_awaited_once()
        assert notifier._progress_messages[sample_task.message_id] is expected_msg

    async def test_notify_start_disabled_no_op(
        self,
        make_notifier: Callable[[BotSettings | None], tuple[BotNotifier, MagicMock]],
        disabled_settings: BotSettings,
        sample_task: DownloadTask,
    ) -> None:
        notifier, client = make_notifier(disabled_settings)
        await notifier.notify_download_start(sample_task, 1024)
        client.send_message.assert_not_awaited()

    async def test_notify_progress_skipped_when_no_msg(
        self,
        make_notifier: Callable[[BotSettings | None], tuple[BotNotifier, MagicMock]],
        bot_settings: BotSettings,
        sample_task: DownloadTask,
    ) -> None:
        """没有进度消息（未调用 notify_download_start）时静默跳过。"""
        notifier, _client = make_notifier(bot_settings)
        # 不抛异常即可
        await notifier.notify_progress(sample_task, 100, 1000)

    async def test_notify_progress_edits_message(
        self,
        make_notifier: Callable[[BotSettings | None], tuple[BotNotifier, MagicMock]],
        bot_settings: BotSettings,
        sample_task: DownloadTask,
    ) -> None:
        """进度更新时编辑消息（满足节流条件）。"""
        notifier, client = make_notifier(bot_settings)
        msg = client.send_message.return_value
        # 模拟已创建进度消息
        notifier._progress_messages[sample_task.message_id] = msg
        # 强制时间通过：上次编辑时间为很久以前
        notifier._last_edit_time[sample_task.message_id] = 0.0
        notifier._last_edit_percent[sample_task.message_id] = 0

        await notifier.notify_progress(sample_task, 500, 1000)

        msg.edit_text.assert_awaited_once()

    async def test_notify_progress_throttled_by_time(
        self,
        make_notifier: Callable[[BotSettings | None], tuple[BotNotifier, MagicMock]],
        bot_settings: BotSettings,
        sample_task: DownloadTask,
    ) -> None:
        """节流：短时间内更新且未达百分比步长时不触发编辑。"""
        import time

        notifier, client = make_notifier(bot_settings)
        msg = client.send_message.return_value
        notifier._progress_messages[sample_task.message_id] = msg
        # 上次编辑时间为现在（不满足时间间隔），且上次百分比为 11
        notifier._last_edit_time[sample_task.message_id] = time.monotonic()
        notifier._last_edit_percent[sample_task.message_id] = 11

        # 当前百分比为 11（11.2/1000*100=1，未达 11+5=16）
        # 这里需要 total 足够大让百分比变化小
        await notifier.notify_progress(sample_task, 1120, 100000)
        msg.edit_text.assert_not_awaited()

    async def test_notify_complete_edits_progress_msg(
        self,
        make_notifier: Callable[[BotSettings | None], tuple[BotNotifier, MagicMock]],
        bot_settings: BotSettings,
        sample_task: DownloadTask,
    ) -> None:
        """下载完成时编辑原进度消息。"""
        notifier, client = make_notifier(bot_settings)
        msg = client.send_message.return_value
        notifier._progress_messages[sample_task.message_id] = msg

        await notifier.notify_download_complete(sample_task, "/tmp/test.mp4", 1024)

        msg.edit_text.assert_awaited_once()
        # 状态已清理
        assert sample_task.message_id not in notifier._progress_messages

    async def test_notify_complete_sends_new_when_no_progress_msg(
        self,
        make_notifier: Callable[[BotSettings | None], tuple[BotNotifier, MagicMock]],
        bot_settings: BotSettings,
        sample_task: DownloadTask,
    ) -> None:
        """无进度消息时单独发送完成通知。"""
        notifier, client = make_notifier(bot_settings)
        await notifier.notify_download_complete(sample_task, "/tmp/test.mp4", 1024)
        client.send_message.assert_awaited_once()

    async def test_notify_error_clears_state(
        self,
        make_notifier: Callable[[BotSettings | None], tuple[BotNotifier, MagicMock]],
        bot_settings: BotSettings,
        sample_task: DownloadTask,
    ) -> None:
        """下载失败通知清理任务运行时状态。"""
        notifier, client = make_notifier(bot_settings)
        msg = client.send_message.return_value
        notifier._progress_messages[sample_task.message_id] = msg
        notifier._last_edit_time[sample_task.message_id] = 100.0
        notifier._last_edit_percent[sample_task.message_id] = 50

        await notifier.notify_download_error(sample_task, "test error")

        msg.edit_text.assert_awaited_once()
        assert sample_task.message_id not in notifier._progress_messages
        assert sample_task.message_id not in notifier._last_edit_time
        assert sample_task.message_id not in notifier._last_edit_percent

    async def test_notify_start_exception_swallowed(
        self,
        make_notifier: Callable[[BotSettings | None], tuple[BotNotifier, MagicMock]],
        bot_settings: BotSettings,
        sample_task: DownloadTask,
    ) -> None:
        """send_message 失败时不抛异常（不影响下载主流程）。"""
        notifier, client = make_notifier(bot_settings)
        # 重新配置为抛异常
        client.send_message = AsyncMock(side_effect=RuntimeError("network error"))
        # 不抛异常
        await notifier.notify_download_start(sample_task, 1024)


class TestBotNotifierSpeedEta:
    """速度/ETA 估算测试。"""

    def test_first_call_returns_empty(self, bot_settings: BotSettings) -> None:
        """首次调用无参照，返回空字符串。"""
        notifier = BotNotifier(client=MagicMock(), settings=bot_settings)
        speed, eta = notifier._estimate_speed_eta(1, 100, 1000)
        assert speed == ""
        assert eta == ""
