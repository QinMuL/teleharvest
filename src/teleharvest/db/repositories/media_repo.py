"""媒体仓储：已下载媒体文件的 CRUD 与去重查询。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select

from teleharvest.db.models import Channel, Media

if TYPE_CHECKING:
    from datetime import datetime

    from teleharvest.db.session import DatabaseSession


class MediaRepository:
    """媒体文件数据访问。"""

    def __init__(self, db: DatabaseSession) -> None:
        self._db = db

    async def create(self, media: Media) -> Media:
        """创建媒体记录。"""
        async with self._db.session() as session:
            session.add(media)
            await session.commit()
            await session.refresh(media)
            return media

    async def find_by_hash(self, file_hash: str) -> Media | None:
        """按哈希查询（用于去重）。

        仅匹配状态为 downloaded 的记录，避免已删除文件干扰去重判断。
        """
        async with self._db.session() as session:
            result = await session.execute(
                select(Media)
                .where(Media.file_hash == file_hash)
                .where(Media.status == "downloaded")
                .limit(1)
            )
            return result.scalar_one_or_none()

    async def find_by_message(
        self,
        channel_id: str | int,
        message_id: int,
    ) -> Media | None:
        """按 Telegram 频道 ID + 消息 ID 查询（用于消息级去重）。

        Args:
            channel_id: Telegram 频道 ID（用户名或数字 ID）
            message_id: Telegram 消息 ID

        Returns:
            匹配的媒体记录，仅当状态为 downloaded 时返回
        """
        async with self._db.session() as session:
            result = await session.execute(
                select(Media)
                .join(Channel, Media.channel_id == Channel.id)
                .where(Channel.channel_id == str(channel_id))
                .where(Media.message_id == message_id)
                .where(Media.status == "downloaded")
                .limit(1)
            )
            return result.scalar_one_or_none()

    async def find_expired(self, before_date: datetime) -> list[Media]:
        """查询早于指定日期的已下载记录（用于过期清理）。

        Args:
            before_date: 截止日期，早于此时间的记录将被返回

        Returns:
            过期媒体记录列表
        """
        async with self._db.session() as session:
            result = await session.execute(
                select(Media)
                .where(Media.created_at < before_date)
                .where(Media.status == "downloaded")
            )
            return list(result.scalars().all())

    async def find_all_active_paths(self) -> set[str]:
        """查询所有已下载状态的文件路径（用于孤儿文件检测）。

        Returns:
            数据库中记录的文件路径集合
        """
        async with self._db.session() as session:
            result = await session.execute(
                select(Media.file_path).where(Media.status == "downloaded")
            )
            return {row[0] for row in result.all()}

    async def mark_deleted(self, media_id: int) -> None:
        """标记媒体已删除。"""
        async with self._db.session() as session:
            media = await session.get(Media, media_id)
            if media:
                media.status = "deleted"
                await session.commit()

    async def find_recent(self, limit: int = 10) -> list[Media]:
        """查询最近的下载记录（按创建时间倒序）。

        Args:
            limit: 返回记录数上限

        Returns:
            最近的媒体记录列表
        """
        async with self._db.session() as session:
            result = await session.execute(
                select(Media)
                .where(Media.status == "downloaded")
                .order_by(Media.created_at.desc())
                .limit(limit)
            )
            return list(result.scalars().all())

    async def count_all(self) -> int:
        """统计已下载的媒体记录总数。"""
        from sqlalchemy import func

        async with self._db.session() as session:
            result = await session.execute(
                select(func.count())
                .select_from(Media)
                .where(Media.status == "downloaded")
            )
            return int(result.scalar() or 0)

    async def total_size(self) -> int:
        """统计已下载媒体文件总大小（字节）。"""
        from sqlalchemy import func

        async with self._db.session() as session:
            result = await session.execute(
                select(func.coalesce(func.sum(Media.file_size), 0))
                .where(Media.status == "downloaded")
            )
            return int(result.scalar() or 0)
