"""频道仓储：订阅频道的 CRUD 与配置同步。

职责：
    1. 将配置文件中的频道列表同步到数据库
    2. 记录每个频道最后处理的消息 ID（断点续传）
    3. 查询频道信息
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from teleharvest.db.models import Channel
from teleharvest.utils.time import now_utc

if TYPE_CHECKING:
    from teleharvest.config.schema import ChannelConfig
    from teleharvest.db.session import DatabaseSession


class ChannelRepository:
    """频道数据访问与配置同步。"""

    def __init__(self, db: DatabaseSession) -> None:
        self._db = db

    async def sync_from_config(self, channels: list[ChannelConfig]) -> dict[str, int]:
        """将配置文件中的频道列表同步到数据库。

        策略：upsert（存在则更新 enabled/alias，不存在则插入）。
        数据库中存在但配置中已删除的频道不会被删除（保留历史记录），
        但会被标记为 disabled。

        Args:
            channels: 配置文件中的频道列表

        Returns:
            统计字典：{"inserted": n, "updated": n, "disabled": n}
        """
        config_ids = {str(ch.id) for ch in channels}
        inserted = 0
        updated = 0

        for ch in channels:
            channel_id = str(ch.id)
            # SQLite upsert
            stmt = sqlite_insert(Channel).values(
                channel_id=channel_id,
                alias=ch.alias,
                enabled=ch.enabled,
                updated_at=now_utc(),
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=[Channel.channel_id],
                set_={
                    "alias": stmt.excluded.alias,
                    "enabled": stmt.excluded.enabled,
                    "updated_at": now_utc(),
                },
            )
            async with self._db.session() as session:
                result = await session.execute(stmt)
                await session.commit()
                # SQLite: rowcount 判断是否插入（1=insert, 0=update/no-op）
                if getattr(result, "rowcount", 0) > 0:
                    inserted += 1
                else:
                    updated += 1

        # 标记配置中已移除的频道为 disabled
        disabled = await self._disable_removed_channels(config_ids)

        return {"inserted": inserted, "updated": updated, "disabled": disabled}

    async def _disable_removed_channels(self, active_ids: set[str]) -> int:
        """将配置中已移除的频道标记为禁用。

        Args:
            active_ids: 当前配置中仍存在的频道 ID 集合

        Returns:
            被禁用的频道数量
        """
        async with self._db.session() as session:
            # 查询所有当前 enabled 的频道
            result = await session.execute(select(Channel).where(Channel.enabled.is_(True)))
            db_channels = list(result.scalars().all())

            to_disable = [ch for ch in db_channels if ch.channel_id not in active_ids]
            for ch in to_disable:
                ch.enabled = False
                ch.updated_at = now_utc()

            if to_disable:
                await session.commit()

            return len(to_disable)

    async def get_by_channel_id(self, channel_id: str | int) -> Channel | None:
        """按 Telegram 频道 ID 查询。"""
        async with self._db.session() as session:
            result = await session.execute(
                select(Channel).where(Channel.channel_id == str(channel_id))
            )
            return result.scalar_one_or_none()

    async def get_enabled(self) -> list[Channel]:
        """获取所有启用的频道。"""
        async with self._db.session() as session:
            result = await session.execute(select(Channel).where(Channel.enabled.is_(True)))
            return list(result.scalars().all())

    async def update_last_message_id(
        self,
        channel_id: str | int,
        message_id: int,
    ) -> None:
        """更新频道最后处理的消息 ID（用于断点续传）。"""
        async with self._db.session() as session:
            await session.execute(
                update(Channel)
                .where(Channel.channel_id == str(channel_id))
                .values(last_message_id=message_id, updated_at=now_utc())
            )
            await session.commit()

    async def get_last_message_id(self, channel_id: str | int) -> int:
        """获取频道最后处理的消息 ID。

        Returns:
            最后处理的消息 ID，无记录则返回 0
        """
        channel = await self.get_by_channel_id(channel_id)
        return channel.last_message_id if channel else 0
