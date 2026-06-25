"""Bot 通知器：向指定聊天推送下载进度、完成通知、错误通知。

行为约束：
    - 所有通知方法均吞掉异常，不影响下载主流程
    - 进度消息编辑受 ``progress_interval`` 节流，避免触发 Telegram 限流
    - 完成卡片会替换原进度消息（同一 message_id），无进度消息时单独发送
    - 推送目标为 ``BotSettings.notify_chat_id``（0 表示禁用推送）
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from pyrogram import Client  # type: ignore[attr-defined]
    from pyrogram.types import Message

    from teleharvest.config.schema import BotSettings
    from teleharvest.core.task import DownloadTask


class BotNotifier:
    """Bot 通知器：基于 Pyrogram Client 实现下载相关消息推送。

    生命周期由调用方管理（main.py 在 MonitorClient 启动后创建并注入
    DownloadEngine）。所有方法均为 async 且线程安全（单事件循环内）。
    """

    def __init__(self, client: Client, settings: BotSettings) -> None:
        self._client = client
        self._settings = settings
        # task_id -> 进度消息对象
        self._progress_messages: dict[int, Message] = {}
        # task_id -> 上次编辑时间戳（monotonic）
        self._last_edit_time: dict[int, float] = {}
        # task_id -> 上次编辑的百分比
        self._last_edit_percent: dict[int, int] = {}
        # task_id -> (上次字节数, 上次时间戳)，用于速度估算
        self._speed_state: dict[int, tuple[int, float]] = {}

    @property
    def enabled(self) -> bool:
        """是否启用推送（notify_chat_id > 0 且 BotToken 已配置）。"""
        return self._settings.notify_chat_id > 0

    async def notify_download_start(self, task: DownloadTask, total_bytes: int) -> None:
        """下载开始时创建进度消息。"""
        if not self.enabled or not self._settings.enable_progress_message:
            return

        try:
            text = self._format_start_text(task, total_bytes)
            msg = await self._client.send_message(
                chat_id=self._settings.notify_chat_id,
                text=text,
                disable_web_page_preview=True,
            )
            self._progress_messages[task.message_id] = msg
            self._last_edit_time[task.message_id] = time.monotonic()
            self._last_edit_percent[task.message_id] = 0
            self._speed_state[task.message_id] = (0, time.monotonic())
            logger.debug("已创建进度消息: task={}, msg_id={}", task.message_id, msg.id)
        except Exception as exc:
            logger.warning("创建进度消息失败: task={}, error={}", task.message_id, exc)

    async def notify_progress(self, task: DownloadTask, current: int, total: int) -> None:
        """更新进度消息（节流）。"""
        if not self.enabled or not self._settings.enable_progress_message:
            return

        msg = self._progress_messages.get(task.message_id)
        if msg is None or total <= 0:
            return

        percent = min(100, int(current * 100 / total))

        # 节流：时间间隔 或 百分比步长 或 已完成
        now = time.monotonic()
        last_time = self._last_edit_time.get(task.message_id, 0.0)
        last_percent = self._last_edit_percent.get(task.message_id, 0)
        time_ok = now - last_time >= self._settings.progress_interval
        step_ok = percent - last_percent >= self._settings.progress_percent_step
        if not (time_ok or step_ok or percent >= 100):
            return

        try:
            text = self._format_progress_text(task, current, total, percent)
            await msg.edit_text(text, disable_web_page_preview=True)
            self._last_edit_time[task.message_id] = now
            self._last_edit_percent[task.message_id] = percent
        except Exception as exc:
            # 编辑失败不报错（消息未变化、限流、被删除等）
            logger.debug("编辑进度消息失败: task={}, error={}", task.message_id, exc)

    async def notify_download_complete(
        self, task: DownloadTask, file_path: str, file_size: int
    ) -> None:
        """下载完成时推送通知卡片。"""
        if not self.enabled or not self._settings.notify_on_complete:
            return

        text = self._format_complete_text(task, file_path, file_size)
        msg = self._progress_messages.pop(task.message_id, None)

        try:
            if msg is not None:
                await msg.edit_text(text, disable_web_page_preview=True)
            else:
                await self._client.send_message(
                    chat_id=self._settings.notify_chat_id,
                    text=text,
                    disable_web_page_preview=True,
                )
            logger.info(
                "已推送完成通知: task={}, file={}",
                task.message_id,
                Path(file_path).name,
            )
        except Exception as exc:
            logger.warning("推送完成通知失败: task={}, error={}", task.message_id, exc)
        finally:
            self._cleanup_task_state(task.message_id)

    async def notify_download_error(self, task: DownloadTask, error: str) -> None:
        """下载失败时推送错误通知。"""
        if not self.enabled or not self._settings.notify_on_error:
            return

        text = self._format_error_text(task, error)
        msg = self._progress_messages.pop(task.message_id, None)

        try:
            if msg is not None:
                await msg.edit_text(text, disable_web_page_preview=True)
            else:
                await self._client.send_message(
                    chat_id=self._settings.notify_chat_id,
                    text=text,
                    disable_web_page_preview=True,
                )
            logger.info("已推送错误通知: task={}", task.message_id)
        except Exception as exc:
            logger.warning("推送错误通知失败: task={}, error={}", task.message_id, exc)
        finally:
            self._cleanup_task_state(task.message_id)

    async def stop(self) -> None:
        """清理资源。"""
        self._progress_messages.clear()
        self._last_edit_time.clear()
        self._last_edit_percent.clear()
        self._speed_state.clear()
        logger.info("Bot 通知器已停止")

    def _cleanup_task_state(self, task_id: int) -> None:
        """清理任务运行时状态。"""
        self._last_edit_time.pop(task_id, None)
        self._last_edit_percent.pop(task_id, None)
        self._speed_state.pop(task_id, None)

    # ===== 文本格式化 =====

    def _format_start_text(self, task: DownloadTask, total_bytes: int) -> str:
        """下载开始消息文本。"""
        size_str = _format_bytes(total_bytes)
        return "\n".join(
            [
                "📥 开始下载",
                f"📌 频道: {task.channel_alias}",
                f"🎞️ 文件: {task.file_name}",
                f"📦 大小: {size_str}",
                "⏳ 进度: [░░░░░░░░░░] 0%",
            ]
        )

    def _format_progress_text(
        self,
        task: DownloadTask,
        current: int,
        total: int,
        percent: int,
    ) -> str:
        """进度更新消息文本。"""
        downloaded = _format_bytes(current)
        total_str = _format_bytes(total)
        bar = _progress_bar(percent, width=10)
        speed, eta = self._estimate_speed_eta(task.message_id, current, total)

        lines = [
            "📥 下载中",
            f"📌 频道: {task.channel_alias}",
            f"🎞️ 文件: {task.file_name}",
            f"📦 大小: {downloaded} / {total_str}",
            f"📊 进度: [{bar}] {percent}%",
        ]
        if speed:
            lines.append(f"⚡ 速度: {speed}")
        if eta:
            lines.append(f"⏱️ 剩余: {eta}")
        return "\n".join(lines)

    def _format_complete_text(self, task: DownloadTask, file_path: str, file_size: int) -> str:
        """下载完成通知卡片文本（基于 caption/元数据，不调 TMDB API）。"""
        from teleharvest.bot.card import build_complete_card

        return build_complete_card(task, file_path, file_size)

    def _format_error_text(self, task: DownloadTask, error: str) -> str:
        """错误通知文本。"""
        return "\n".join(
            [
                "❌ 下载失败",
                f"📌 频道: {task.channel_alias}",
                f"🎞️ 文件: {task.file_name}",
                f"⚠️ 错误: {error}",
            ]
        )

    # ===== 速度/ETA 估算 =====

    def _estimate_speed_eta(self, task_id: int, current: int, total: int) -> tuple[str, str]:
        """估算下载速度和剩余时间。

        策略：保留上次的字节与时间戳，差值除以时间差得到速度；
        剩余字节除以速度得到 ETA。首次调用无参照返回空字符串。
        """
        now = time.monotonic()
        last_state = self._speed_state.get(task_id)
        if last_state is None:
            self._speed_state[task_id] = (current, now)
            return ("", "")

        last_bytes, last_time = last_state
        dt = now - last_time
        if dt <= 0:
            return ("", "")

        delta_bytes = current - last_bytes
        self._speed_state[task_id] = (current, now)

        if delta_bytes <= 0:
            return ("", "")

        speed_bps = delta_bytes / dt
        speed_str = f"{_format_bytes(speed_bps)}/s"

        if total <= current or speed_bps <= 0:
            return (speed_str, "")

        remaining = total - current
        eta_sec = max(1, int(remaining / speed_bps))
        return (speed_str, _format_duration(eta_sec))


def _format_bytes(num: float) -> str:
    """字节数格式化为人类可读字符串。"""
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if abs(num) < 1024.0:
            return f"{num:.1f} {unit}"
        num /= 1024.0
    return f"{num:.1f} PB"


def _progress_bar(percent: int, width: int = 10) -> str:
    """生成进度条字符串。"""
    filled = width * percent // 100
    return "█" * filled + "░" * (width - filled)


def _format_duration(seconds: int) -> str:
    """时长格式化为人类可读字符串。"""
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m{seconds % 60}s"
    return f"{seconds // 3600}h{(seconds % 3600) // 60}m"
