"""下载进度追踪：实时记录下载进度，支持 async 回调通知。

作为 Pyrogram ``message.download()`` 的 ``progress`` 回调入口。
Pyrogram 进度回调本身为 async function，因此本模块的 callback 也使用
``Awaitable`` 签名，支持向 Bot 通知器等异步消费方实时推送进度。

异常处理策略：
    - 回调中抛出的异常被吞掉（仅记录 debug 日志），避免中断下载主流程
    - 进度日志按百分比间隔记录，避免刷屏
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    # Pyrogram 进度回调签名为 async (current, total) -> None
    ProgressCallback = Callable[[int, int], Awaitable[None]]


class ProgressTracker:
    """下载进度追踪器。

    作为 Pyrogram ``download()`` 的 ``progress`` 回调入口，
    记录已下载字节数并触发外部 async 回调（例如 Bot 通知器）。
    """

    def __init__(
        self,
        callback: ProgressCallback | None = None,
        log_interval_percent: int = 10,
    ) -> None:
        self._callback = callback
        self._log_interval = log_interval_percent
        self._last_logged_percent = -1

    async def on_progress(self, current: int, total: int) -> None:
        """Pyrogram 进度回调入口。

        Args:
            current: 已下载字节数
            total: 总字节数
        """
        # 触发外部 async 回调（如 Bot 通知器编辑进度消息）
        if self._callback is not None:
            try:
                await self._callback(current, total)
            except Exception as exc:
                # 回调失败不中断下载主流程
                logger.debug("进度回调异常: {}", exc)

        # 按百分比间隔记录日志，避免刷屏
        if total > 0:
            percent = int(current * 100 / total)
            if percent >= self._last_logged_percent + self._log_interval:
                self._last_logged_percent = percent
                logger.debug(
                    "下载进度: {}/{} ({}%)",
                    _format_bytes(current),
                    _format_bytes(total),
                    percent,
                )


def _format_bytes(num: float) -> str:
    """字节数格式化为人类可读字符串。"""
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if abs(num) < 1024.0:
            return f"{num:.1f} {unit}"
        num /= 1024.0
    return f"{num:.1f} PB"
