"""单元测试：下载引擎核心逻辑。

使用 mock 模拟 Pyrogram Message 和文件系统操作，
验证 DownloadEngine 的去重、断点续传、重试等逻辑。
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from teleharvest.config.schema import DownloaderSettings
from teleharvest.core.task import DownloadTask
from teleharvest.downloader.engine import DownloadEngine, DownloadResult
from teleharvest.storage.dedup import DedupChecker
from teleharvest.storage.manager import StorageManager

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def downloader_settings() -> DownloaderSettings:
    """下载配置（快速重试用于测试）。"""
    return DownloaderSettings(
        max_concurrency=2,
        timeout=60,
        max_retries=2,
        retry_base_delay=0.1,  # 测试中快速重试
        enable_resume=True,
    )


@pytest.fixture
def storage_manager(tmp_path: Path) -> StorageManager:
    """存储管理器（使用临时目录）。"""
    from teleharvest.config.schema import StorageSettings

    return StorageManager(StorageSettings(root_dir=tmp_path / "downloads"))


@pytest.fixture
def download_engine(
    downloader_settings: DownloaderSettings,
    storage_manager: StorageManager,
) -> DownloadEngine:
    """下载引擎实例。"""
    return DownloadEngine(
        settings=downloader_settings,
        storage_manager=storage_manager,
        dedup_checker=None,
        media_repo=None,
    )


@pytest.fixture
def sample_task(tmp_path: Path) -> DownloadTask:
    """示例下载任务（带 mock 消息引用）。"""
    message = MagicMock()
    message.download = AsyncMock(return_value=str(tmp_path / "test.mp4"))
    return DownloadTask(
        message_id=100,
        channel_id="@test",
        channel_alias="测试频道",
        media_type="video",
        file_name="test.mp4",
        file_size=1024,
        _message_ref=message,
    )


class TestDownloadResult:
    """DownloadResult 数据类测试。"""

    def test_success_result(self) -> None:
        """成功结果字符串表示。"""
        r = DownloadResult(success=True, file_path="/tmp/a.mp4", file_size=1024)
        assert "ok" in str(r)
        assert "1024" in str(r)

    def test_failure_result(self) -> None:
        """失败结果字符串表示。"""
        r = DownloadResult(success=False, error="timeout")
        assert "failed" in str(r)
        assert "timeout" in str(r)


class TestDownloadEngine:
    """DownloadEngine 下载逻辑测试。"""

    @pytest.mark.asyncio
    async def test_download_no_message_ref(
        self,
        download_engine: DownloadEngine,
    ) -> None:
        """无消息引用的任务应返回失败。"""
        task = DownloadTask(
            message_id=1,
            channel_id="@test",
            channel_alias="测试",
            media_type="video",
            file_name="test.mp4",
            file_size=100,
            _message_ref=None,
        )
        result = await download_engine.download(task)
        assert result.success is False
        assert "消息引用" in result.error

    @pytest.mark.asyncio
    async def test_download_success(
        self,
        download_engine: DownloadEngine,
        storage_manager: StorageManager,
        sample_task: DownloadTask,
    ) -> None:
        """成功下载应返回正确结果。"""
        # mock download 在调用时创建文件
        dest_path = storage_manager.build_path(
            channel_alias=sample_task.channel_alias,
            media_type=sample_task.media_type,
            original_name=sample_task.file_name,
            message_id=sample_task.message_id,
        )

        async def mock_download(*args, **kwargs):
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            dest_path.write_bytes(b"x" * 1024)
            return str(dest_path)

        sample_task._message_ref.download = AsyncMock(side_effect=mock_download)

        result = await download_engine.download(sample_task)

        assert result.success is True
        assert result.file_size == 1024
        # 验证 Pyrogram download 被调用
        sample_task._message_ref.download.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_download_already_downloaded_message(
        self,
        downloader_settings: DownloaderSettings,
        storage_manager: StorageManager,
        sample_task: DownloadTask,
    ) -> None:
        """消息级去重：已下载过的消息应跳过。"""
        dedup = MagicMock(spec=DedupChecker)
        dedup.is_message_downloaded = AsyncMock(return_value=True)

        engine = DownloadEngine(
            settings=downloader_settings,
            storage_manager=storage_manager,
            dedup_checker=dedup,
            media_repo=None,
        )

        result = await engine.download(sample_task)

        assert result.success is True
        assert result.error == "already_downloaded"
        # 不应调用实际下载
        sample_task._message_ref.download.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_download_file_already_complete(
        self,
        download_engine: DownloadEngine,
        storage_manager: StorageManager,
        sample_task: DownloadTask,
    ) -> None:
        """断点续传：文件已完整存在时应跳过下载。"""
        # 预先创建完整文件
        dest_path = storage_manager.build_path(
            channel_alias=sample_task.channel_alias,
            media_type=sample_task.media_type,
            original_name=sample_task.file_name,
            message_id=sample_task.message_id,
        )
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        dest_path.write_bytes(b"x" * sample_task.file_size)

        result = await download_engine.download(sample_task)

        assert result.success is True
        assert result.file_size == sample_task.file_size
        # 不应调用 Pyrogram 下载
        sample_task._message_ref.download.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_download_retry_on_failure(
        self,
        downloader_settings: DownloaderSettings,
        storage_manager: StorageManager,
        sample_task: DownloadTask,
    ) -> None:
        """下载失败应重试，最终耗尽返回失败。"""
        # mock download 总是抛出异常
        sample_task._message_ref.download = AsyncMock(side_effect=ConnectionError("network error"))

        engine = DownloadEngine(
            settings=downloader_settings,
            storage_manager=storage_manager,
        )

        result = await engine.download(sample_task)

        assert result.success is False
        assert "network error" in result.error
        # 应重试 max_retries + 1 次
        assert sample_task._message_ref.download.await_count == downloader_settings.max_retries + 1

    @pytest.mark.asyncio
    async def test_download_flood_wait_handling(
        self,
        downloader_settings: DownloaderSettings,
        storage_manager: StorageManager,
        sample_task: DownloadTask,
    ) -> None:
        """FloodWait 异常应等待后重试。"""
        # 创建带 value 属性的异常（模拟 Pyrogram FloodWait）
        flood_exc = ConnectionError("flood")
        flood_exc.value = 1  # type: ignore[attr-defined]

        call_count = 0

        async def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise flood_exc
            # 第二次成功
            return str(args[0]) if args else ""

        sample_task._message_ref.download = AsyncMock(side_effect=side_effect)

        # 预创建文件使第二次调用后能获取文件大小
        dest_path = storage_manager.build_path(
            channel_alias=sample_task.channel_alias,
            media_type=sample_task.media_type,
            original_name=sample_task.file_name,
            message_id=sample_task.message_id,
        )
        dest_path.parent.mkdir(parents=True, exist_ok=True)

        async def mock_download_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise flood_exc
            dest_path.write_bytes(b"x" * 512)
            return str(dest_path)

        sample_task._message_ref.download = AsyncMock(side_effect=mock_download_side_effect)

        engine = DownloadEngine(
            settings=downloader_settings,
            storage_manager=storage_manager,
        )

        result = await engine.download(sample_task)

        assert result.success is True
        assert call_count == 2  # 第一次 FloodWait，第二次成功
