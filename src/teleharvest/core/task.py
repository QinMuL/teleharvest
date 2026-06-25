"""下载任务数据结构：监控模块与下载模块之间的传递对象。

DownloadTask 封装了从 Telegram 消息中提取的下载所需全部信息，
避免下载引擎直接依赖 Pyrogram Message 对象（解耦 + 便于测试）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from datetime import datetime

MediaKind = Literal["audio", "video", "document", "photo"]
TaskStatus = Literal["pending", "running", "done", "failed", "cancelled"]


@dataclass
class DownloadTask:
    """单个下载任务的完整描述。

    Attributes:
        message_id: Telegram 消息 ID
        channel_id: 频道 ID（数字或用户名）
        channel_alias: 频道别名（用于存储目录命名）
        media_type: 媒体类型
        file_name: 原始文件名
        file_size: 文件大小（字节）
        caption: 消息附带的文字说明
        message_date: 消息发送时间
        status: 任务状态
        retries: 已重试次数
        downloaded_bytes: 已下载字节数（断点续传用）
        error: 失败时的错误信息
    """

    message_id: int
    channel_id: str | int
    channel_alias: str
    media_type: MediaKind
    file_name: str
    file_size: int
    caption: str = ""
    message_date: datetime | None = None
    status: TaskStatus = "pending"
    retries: int = 0
    downloaded_bytes: int = 0
    error: str = ""
    # 内部使用：Pyrogram 消息引用（不入库，进程内传递）
    _message_ref: object = field(default=None, repr=False, compare=False)

    @property
    def unique_key(self) -> str:
        """任务唯一标识：频道 + 消息 ID。"""
        return f"{self.channel_id}:{self.message_id}"

    @property
    def size_mb(self) -> float:
        """文件大小（MB）。"""
        return self.file_size / (1024 * 1024)

    def __str__(self) -> str:
        return (
            f"DownloadTask(msg={self.message_id}, channel={self.channel_alias}, "
            f"type={self.media_type}, file={self.file_name}, size={self.size_mb:.1f}MB)"
        )
