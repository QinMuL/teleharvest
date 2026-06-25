"""ORM 数据模型定义。

表结构：
    - channels:  订阅的频道信息
    - media:     已下载的媒体文件记录（含哈希用于去重）
    - tasks:     下载任务状态记录
    - logs:      关键操作日志（可选）
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 - SQLAlchemy Mapped 运行时类型解析需要

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from teleharvest.utils.time import now_utc


class Base(DeclarativeBase):
    """SQLAlchemy 声明式基类。"""

    pass


class Channel(Base):
    """订阅频道表。"""

    __tablename__ = "channels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Telegram 频道 ID（可能为负数）或用户名
    channel_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    alias: Mapped[str] = mapped_column(String(128), default="")
    enabled: Mapped[bool] = mapped_column(default=True)
    # 最后监听的消息 ID（用于断点续传）
    last_message_id: Mapped[int] = mapped_column(BigInteger, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc, onupdate=now_utc)

    media: Mapped[list[Media]] = relationship(back_populates="channel")


class Media(Base):
    """媒体文件记录表（用于去重与查询）。"""

    __tablename__ = "media"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id"), index=True)
    message_id: Mapped[int] = mapped_column(BigInteger, index=True)
    # 媒体类型：audio / video / document / photo
    media_type: Mapped[str] = mapped_column(String(32))
    # 原始文件名
    original_name: Mapped[str] = mapped_column(String(512), default="")
    # 本地存储路径（相对 root_dir）
    file_path: Mapped[str] = mapped_column(String(1024))
    # 文件大小（字节）
    file_size: Mapped[int] = mapped_column(BigInteger, default=0)
    # 文件哈希（用于去重）
    file_hash: Mapped[str] = mapped_column(String(64), index=True, default="")
    # 状态：downloaded / failed / deleted
    status: Mapped[str] = mapped_column(String(16), default="downloaded")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)

    channel: Mapped[Channel] = relationship(back_populates="media")

    @property
    def unique_key(self) -> tuple[int, int]:
        """频道 + 消息 ID 唯一键。"""
        return (self.channel_id, self.message_id)


class Task(Base):
    """下载任务记录表。"""

    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id"), index=True)
    message_id: Mapped[int] = mapped_column(BigInteger, index=True)
    # 状态：pending / running / done / failed / cancelled
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    # 错误信息（失败时记录）
    error: Mapped[str] = mapped_column(Text, default="")
    # 重试次数
    retries: Mapped[int] = mapped_column(Integer, default=0)
    # 已下载字节数（断点续传用）
    downloaded_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    # 关联的媒体记录 ID（完成后填充）
    media_id: Mapped[int | None] = mapped_column(ForeignKey("media.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
