"""去重校验：基于文件哈希避免重复下载。

流程：
    1. 下载前：查询数据库中是否已存在相同哈希或相同 (channel, message_id)
    2. 下载后：计算文件哈希并入库
    3. 若发现重复：跳过或覆盖（由配置决定）
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from teleharvest.utils.hash import compute_file_hash

if TYPE_CHECKING:
    from pathlib import Path

    from teleharvest.db.repositories.media_repo import MediaRepository


class DedupChecker:
    """文件去重检查器。"""

    def __init__(
        self,
        algorithm: str = "sha256",
        media_repo: MediaRepository | None = None,
    ) -> None:
        self._algorithm = algorithm
        self._repo = media_repo

    def compute_hash(self, file_path: Path) -> str:
        """计算文件哈希。"""
        return compute_file_hash(file_path, self._algorithm)

    async def is_duplicate(self, file_hash: str) -> bool:
        """检查哈希是否已存在于数据库。

        Args:
            file_hash: 文件哈希值

        Returns:
            True 表示重复
        """
        if self._repo is None:
            return False
        existing = await self._repo.find_by_hash(file_hash)
        if existing:
            logger.info("检测到重复文件: hash={}, 原始路径={}", file_hash[:16], existing.file_path)
            return True
        return False

    async def is_message_downloaded(self, channel_id: str | int, message_id: int) -> bool:
        """检查某条消息是否已下载过。

        Args:
            channel_id: 频道 ID
            message_id: 消息 ID

        Returns:
            True 表示已下载过
        """
        if self._repo is None:
            return False
        existing = await self._repo.find_by_message(channel_id, message_id)
        return existing is not None
