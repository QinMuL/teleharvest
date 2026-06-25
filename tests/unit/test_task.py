"""单元测试：DownloadTask 数据结构。"""

from __future__ import annotations

from datetime import datetime

import pytest

from teleharvest.core.task import DownloadTask


class TestDownloadTask:
    """DownloadTask 数据类测试。"""

    @pytest.fixture
    def sample_task(self) -> DownloadTask:
        """构造示例任务。"""
        return DownloadTask(
            message_id=12345,
            channel_id="@test_channel",
            channel_alias="测试频道",
            media_type="video",
            file_name="movie.mp4",
            file_size=10 * 1024 * 1024,  # 10MB
        )

    def test_unique_key(self, sample_task: DownloadTask) -> None:
        """唯一键应为频道:消息ID格式。"""
        assert sample_task.unique_key == "@test_channel:12345"

    def test_size_mb(self, sample_task: DownloadTask) -> None:
        """文件大小应正确转换为 MB。"""
        assert sample_task.size_mb == pytest.approx(10.0, rel=0.01)

    def test_default_status(self, sample_task: DownloadTask) -> None:
        """默认状态应为 pending。"""
        assert sample_task.status == "pending"
        assert sample_task.retries == 0
        assert sample_task.downloaded_bytes == 0

    def test_str_representation(self, sample_task: DownloadTask) -> None:
        """字符串表示应包含关键信息。"""
        s = str(sample_task)
        assert "12345" in s
        assert "测试频道" in s
        assert "video" in s
        assert "movie.mp4" in s

    def test_with_message_date(self) -> None:
        """应支持设置消息日期。"""
        dt = datetime(2026, 6, 24, 12, 0, 0)
        task = DownloadTask(
            message_id=1,
            channel_id=-100123,
            channel_alias="ch",
            media_type="audio",
            file_name="test.mp3",
            file_size=1024,
            message_date=dt,
        )
        assert task.message_date == dt
