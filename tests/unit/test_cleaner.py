"""单元测试：文件清理器。

使用临时目录和 mock MediaRepository 测试过期清理与孤儿检测逻辑。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from teleharvest.config.schema import StorageSettings
from teleharvest.storage.cleaner import Cleaner

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def storage_settings(tmp_path: Path) -> StorageSettings:
    """存储配置（使用临时目录，启用清理）。"""
    return StorageSettings(
        root_dir=tmp_path / "downloads",
        retention_days=7,
        cleanup_orphans=True,
    )


@pytest.fixture
def mock_media_repo() -> MagicMock:
    """模拟媒体仓储。"""
    repo = MagicMock()
    repo.find_expired = AsyncMock(return_value=[])
    repo.find_all_active_paths = AsyncMock(return_value=set())
    repo.mark_deleted = AsyncMock()
    return repo


@pytest.fixture
def cleaner(
    storage_settings: StorageSettings,
    mock_media_repo: MagicMock,
) -> Cleaner:
    """文件清理器实例。"""
    return Cleaner(settings=storage_settings, media_repo=mock_media_repo)


def _make_media(
    media_id: int,
    file_path: str,
    created_at: datetime,
) -> MagicMock:
    """构造模拟 Media 记录。"""
    media = MagicMock()
    media.id = media_id
    media.file_path = file_path
    media.created_at = created_at
    media.status = "downloaded"
    return media


class TestCleanupExpired:
    """过期清理测试。"""

    async def test_skip_when_retention_zero(
        self,
        tmp_path: Path,
        mock_media_repo: MagicMock,
    ) -> None:
        """retention_days=0 时应跳过清理。"""
        settings = StorageSettings(root_dir=tmp_path / "downloads", retention_days=0)
        c = Cleaner(settings=settings, media_repo=mock_media_repo)
        count = await c.cleanup_expired()
        assert count == 0
        mock_media_repo.find_expired.assert_not_awaited()

    async def test_cleanup_db_records(
        self,
        cleaner: Cleaner,
        storage_settings: StorageSettings,
        mock_media_repo: MagicMock,
        tmp_path: Path,
    ) -> None:
        """DB 驱动清理：删除过期文件并标记记录。"""
        # 创建过期文件
        root = storage_settings.root_dir
        old_file = root / "old_video.mp4"
        old_file.parent.mkdir(parents=True, exist_ok=True)
        old_file.write_bytes(b"old content")

        old_date = datetime.now() - timedelta(days=10)
        expired_media = _make_media(1, str(old_file), old_date)
        mock_media_repo.find_expired = AsyncMock(return_value=[expired_media])

        count = await cleaner.cleanup_expired()

        assert count >= 1
        assert not old_file.exists()
        mock_media_repo.mark_deleted.assert_awaited_once_with(1)

    async def test_cleanup_by_mtime_fallback(
        self,
        cleaner: Cleaner,
        storage_settings: StorageSettings,
        mock_media_repo: MagicMock,
        tmp_path: Path,
    ) -> None:
        """文件系统兜底清理：无 DB 记录的过期文件。"""
        root = storage_settings.root_dir
        old_file = root / "orphan_old.mp4"
        old_file.parent.mkdir(parents=True, exist_ok=True)
        old_file.write_bytes(b"orphan")

        # 设置文件 mtime 为 10 天前
        import os

        old_time = (datetime.now() - timedelta(days=10)).timestamp()
        os.utime(old_file, (old_time, old_time))

        # DB 中无记录
        mock_media_repo.find_expired = AsyncMock(return_value=[])

        count = await cleaner.cleanup_expired()

        assert count >= 1
        assert not old_file.exists()


class TestCleanupOrphans:
    """孤儿文件清理测试。"""

    async def test_no_orphans_when_db_empty_and_fs_empty(
        self,
        cleaner: Cleaner,
        mock_media_repo: MagicMock,
    ) -> None:
        """空目录无孤儿文件。"""
        mock_media_repo.find_all_active_paths = AsyncMock(return_value=set())
        count = await cleaner.cleanup_orphans()
        assert count == 0

    async def test_delete_orphan_files(
        self,
        cleaner: Cleaner,
        storage_settings: StorageSettings,
        mock_media_repo: MagicMock,
    ) -> None:
        """应删除 DB 中无记录的文件。"""
        root = storage_settings.root_dir

        # DB 中记录的文件（应保留）
        tracked_file = root / "tracked.mp4"
        tracked_file.parent.mkdir(parents=True, exist_ok=True)
        tracked_file.write_bytes(b"tracked")

        # 孤儿文件（应删除）
        orphan_file = root / "orphan.mp4"
        orphan_file.write_bytes(b"orphan")

        mock_media_repo.find_all_active_paths = AsyncMock(return_value={str(tracked_file)})

        count = await cleaner.cleanup_orphans()

        assert count == 1
        assert tracked_file.exists()
        assert not orphan_file.exists()

    async def test_skip_when_no_repo(self, tmp_path: Path) -> None:
        """无 DB 仓储时应跳过孤儿清理。"""
        settings = StorageSettings(root_dir=tmp_path / "downloads")
        c = Cleaner(settings=settings, media_repo=None)
        count = await c.cleanup_orphans()
        assert count == 0


class TestCleanupAll:
    """完整清理流程测试。"""

    async def test_cleanup_all_returns_stats(
        self,
        cleaner: Cleaner,
        storage_settings: StorageSettings,
        mock_media_repo: MagicMock,
    ) -> None:
        """cleanup_all 应返回过期与孤儿统计。"""
        root = storage_settings.root_dir
        orphan = root / "orphan.mp4"
        orphan.parent.mkdir(parents=True, exist_ok=True)
        orphan.write_bytes(b"x")

        mock_media_repo.find_expired = AsyncMock(return_value=[])
        mock_media_repo.find_all_active_paths = AsyncMock(return_value=set())

        stats = await cleaner.cleanup_all()

        assert "expired" in stats
        assert "orphans" in stats
        assert stats["orphans"] >= 1


class TestResolvePath:
    """路径解析测试。"""

    def test_absolute_path_exists(
        self,
        cleaner: Cleaner,
        storage_settings: StorageSettings,
        tmp_path: Path,
    ) -> None:
        """绝对路径且文件存在时应返回该路径。"""
        file_path = tmp_path / "abs_file.mp4"
        file_path.write_bytes(b"x")
        result = cleaner._resolve_path(str(file_path))
        assert result == file_path

    def test_absolute_path_not_exists(self, cleaner: Cleaner) -> None:
        """绝对路径但文件不存在时应返回 None。"""
        result = cleaner._resolve_path("/nonexistent/file.mp4")
        assert result is None

    def test_relative_path(
        self,
        cleaner: Cleaner,
        storage_settings: StorageSettings,
    ) -> None:
        """相对路径应基于 root_dir 解析。"""
        root = storage_settings.root_dir
        rel_file = root / "subdir" / "rel.mp4"
        rel_file.parent.mkdir(parents=True, exist_ok=True)
        rel_file.write_bytes(b"x")

        result = cleaner._resolve_path("subdir/rel.mp4")
        assert result == rel_file
