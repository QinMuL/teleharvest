"""数据库会话管理：异步引擎与表初始化。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from teleharvest.db.models import Base

if TYPE_CHECKING:
    from teleharvest.config.schema import DatabaseSettings


class DatabaseSession:
    """异步数据库会话管理器。"""

    def __init__(self, settings: DatabaseSettings) -> None:
        self._settings = settings
        self._engine: AsyncEngine | None = None
        self._session_maker: async_sessionmaker[AsyncSession] | None = None

    async def start(self) -> None:
        """初始化引擎并创建表。"""
        self._engine = create_async_engine(
            self._settings.url,
            echo=self._settings.echo,
            pool_size=self._settings.pool_size,
            pool_timeout=self._settings.pool_timeout,
        )
        self._session_maker = async_sessionmaker(
            self._engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

        # 自动建表
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        logger.info("数据库已初始化: {}", self._settings.url)

    async def stop(self) -> None:
        """关闭引擎。"""
        if self._engine:
            await self._engine.dispose()
            logger.info("数据库连接已关闭")

    @property
    def session_maker(self) -> async_sessionmaker[AsyncSession]:
        """获取会话工厂。"""
        if self._session_maker is None:
            raise RuntimeError("数据库未初始化，请先调用 start()")
        return self._session_maker

    def session(self) -> AsyncSession:
        """创建新会话。调用方负责关闭。"""
        return self.session_maker()
