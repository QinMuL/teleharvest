"""单元测试：媒体仓储查询逻辑。

使用内存 SQLite 数据库测试真实的 SQLAlchemy 查询，
重点验证 find_by_message 的 channel join 和 status 过滤。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from teleharvest.config.schema import DatabaseSettings
from teleharvest.db.models import Channel, Media
from teleharvest.db.repositories.media_repo import MediaRepository
from teleharvest.db.session import DatabaseSession

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
async def db(tmp_path: Path) -> DatabaseSession:
    """内存 SQLite 数据库会话。"""
    db_path = tmp_path / "test_media.db"
    session = DatabaseSession(
        DatabaseSettings(
            url=f"sqlite+aiosqlite:///{db_path}",
        )
    )
    await session.start()
    yield session
    await session.stop()


@pytest.fixture
async def repo_with_data(db: DatabaseSession) -> tuple[MediaRepository, dict]:
    """带测试数据的仓储。

    创建 2 个频道、3 条媒体记录（含不同状态）。
    """
    repo = MediaRepository(db)

    # 创建频道
    async with db.session() as session:
        ch1 = Channel(channel_id="@channel_a", alias="频道A", enabled=True)
        ch2 = Channel(channel_id="@channel_b", alias="频道B", enabled=True)
        session.add_all([ch1, ch2])
        await session.commit()
        await session.refresh(ch1)
        await session.refresh(ch2)

        # 创建媒体记录
        old_date = datetime.now() - timedelta(days=30)
        recent_date = datetime.now() - timedelta(days=1)

        m1 = Media(
            channel_id=ch1.id,
            message_id=100,
            media_type="video",
            original_name="video_a.mp4",
            file_path="/data/video_a.mp4",
            file_size=1024,
            file_hash="hash_aaa",
            status="downloaded",
            created_at=recent_date,
        )
        m2 = Media(
            channel_id=ch2.id,
            message_id=100,  # 相同 message_id，不同频道
            media_type="audio",
            original_name="audio_b.mp3",
            file_path="/data/audio_b.mp3",
            file_size=512,
            file_hash="hash_bbb",
            status="downloaded",
            created_at=old_date,
        )
        m3 = Media(
            channel_id=ch1.id,
            message_id=200,
            media_type="video",
            original_name="video_deleted.mp4",
            file_path="/data/video_deleted.mp4",
            file_size=2048,
            file_hash="hash_ccc",
            status="deleted",  # 已删除
            created_at=old_date,
        )
        session.add_all([m1, m2, m3])
        await session.commit()

        return repo, {
            "ch1_id": ch1.id,
            "ch2_id": ch2.id,
            "m1_id": m1.id,
            "m2_id": m2.id,
            "m3_id": m3.id,
        }


class TestFindByMessage:
    """find_by_message 测试：验证 channel join 与 status 过滤。"""

    async def test_find_by_channel_and_message(
        self,
        repo_with_data: tuple[MediaRepository, dict],
    ) -> None:
        """应通过 Telegram 频道 ID + 消息 ID 精确查找。"""
        repo, data = repo_with_data
        result = await repo.find_by_message("@channel_a", 100)
        assert result is not None
        assert result.id == data["m1_id"]
        assert result.original_name == "video_a.mp4"

    async def test_same_message_id_different_channel(
        self,
        repo_with_data: tuple[MediaRepository, dict],
    ) -> None:
        """相同 message_id 不同频道应返回不同记录。"""
        repo, _ = repo_with_data
        result_a = await repo.find_by_message("@channel_a", 100)
        result_b = await repo.find_by_message("@channel_b", 100)
        assert result_a is not None
        assert result_b is not None
        assert result_a.id != result_b.id
        assert result_a.original_name == "video_a.mp4"
        assert result_b.original_name == "audio_b.mp3"

    async def test_skip_deleted_status(
        self,
        repo_with_data: tuple[MediaRepository, dict],
    ) -> None:
        """应跳过 status=deleted 的记录。"""
        repo, _ = repo_with_data
        result = await repo.find_by_message("@channel_a", 200)
        assert result is None  # m3 状态为 deleted

    async def test_not_found(self, repo_with_data: tuple[MediaRepository, dict]) -> None:
        """不存在的消息应返回 None。"""
        repo, _ = repo_with_data
        result = await repo.find_by_message("@channel_a", 999)
        assert result is None


class TestFindByHash:
    """find_by_hash 测试。"""

    async def test_find_existing_hash(
        self,
        repo_with_data: tuple[MediaRepository, dict],
    ) -> None:
        """应找到匹配哈希的已下载记录。"""
        repo, _ = repo_with_data
        result = await repo.find_by_hash("hash_aaa")
        assert result is not None
        assert result.original_name == "video_a.mp4"

    async def test_skip_deleted_hash(
        self,
        repo_with_data: tuple[MediaRepository, dict],
    ) -> None:
        """应跳过已删除记录的哈希。"""
        repo, _ = repo_with_data
        result = await repo.find_by_hash("hash_ccc")
        assert result is None  # m3 状态为 deleted

    async def test_not_found_hash(
        self,
        repo_with_data: tuple[MediaRepository, dict],
    ) -> None:
        """不存在的哈希应返回 None。"""
        repo, _ = repo_with_data
        result = await repo.find_by_hash("nonexistent")
        assert result is None


class TestFindExpired:
    """find_expired 测试。"""

    async def test_find_expired_records(
        self,
        repo_with_data: tuple[MediaRepository, dict],
    ) -> None:
        """应返回早于截止日期的已下载记录。"""
        repo, _ = repo_with_data
        cutoff = datetime.now() - timedelta(days=7)
        results = await repo.find_expired(cutoff)

        # m2 (old_date, downloaded) 应在结果中
        # m3 (old_date, deleted) 不应在结果中
        assert data_m2_in_results(results, repo_with_data)

    async def test_no_expired_when_cutoff_recent(
        self,
        repo_with_data: tuple[MediaRepository, dict],
    ) -> None:
        """截止日期较近时不应返回记录。"""
        repo, _ = repo_with_data
        cutoff = datetime.now() - timedelta(days=60)
        results = await repo.find_expired(cutoff)
        assert len(results) == 0


def data_m2_in_results(results: list, repo_with_data: tuple[MediaRepository, dict]) -> bool:
    """检查 m2 是否在结果中。"""
    _, data = repo_with_data
    return any(r.id == data["m2_id"] for r in results)


class TestFindAllActivePaths:
    """find_all_active_paths 测试。"""

    async def test_returns_only_downloaded_paths(
        self,
        repo_with_data: tuple[MediaRepository, dict],
    ) -> None:
        """应只返回 status=downloaded 的文件路径。"""
        repo, _ = repo_with_data
        paths = await repo.find_all_active_paths()

        assert "/data/video_a.mp4" in paths
        assert "/data/audio_b.mp3" in paths
        assert "/data/video_deleted.mp4" not in paths  # deleted 不包含


class TestMarkDeleted:
    """mark_deleted 测试。"""

    async def test_mark_as_deleted(
        self,
        repo_with_data: tuple[MediaRepository, dict],
    ) -> None:
        """标记删除后应无法通过 find_by_hash 查到。"""
        repo, data = repo_with_data

        # 确认标记前可查到
        before = await repo.find_by_hash("hash_aaa")
        assert before is not None

        # 标记删除
        await repo.mark_deleted(data["m1_id"])

        # 标记后查不到
        after = await repo.find_by_hash("hash_aaa")
        assert after is None
