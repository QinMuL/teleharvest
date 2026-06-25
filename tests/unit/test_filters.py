"""单元测试：消息过滤规则。"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from teleharvest.config.schema import MediaFilter
from teleharvest.monitor.filters import MessageFilter


class TestMessageFilter:
    """MessageFilter 过滤逻辑测试。"""

    @pytest.fixture
    def video_message(self) -> MagicMock:
        """构造一个含视频的消息 mock。"""
        msg = MagicMock()
        msg.video.file_name = "test.mp4"
        msg.video.file_size = 10 * 1024 * 1024  # 10MB
        msg.audio = None
        msg.document = None
        msg.photo = None
        msg.caption = ""
        msg.text = ""
        msg.id = 1
        return msg

    def test_no_media_rejected(self) -> None:
        """无媒体内容的消息应被拒绝。"""
        msg = MagicMock()
        msg.video = None
        msg.audio = None
        msg.document = None
        msg.photo = None

        f = MessageFilter(MediaFilter())
        assert f.should_download(msg) is False

    def test_type_whitelist(self, video_message: MagicMock) -> None:
        """媒体类型白名单应生效。"""
        f = MessageFilter(MediaFilter(types=["audio"]))
        assert f.should_download(video_message) is False

        f = MessageFilter(MediaFilter(types=["video"]))
        assert f.should_download(video_message) is True

    def test_extension_whitelist(self, video_message: MagicMock) -> None:
        """扩展名白名单应生效。"""
        f = MessageFilter(MediaFilter(extensions=["mp3"]))
        assert f.should_download(video_message) is False

        f = MessageFilter(MediaFilter(extensions=["mp4"]))
        assert f.should_download(video_message) is True

    def test_size_range(self, video_message: MagicMock) -> None:
        """文件大小范围应生效。"""
        # 10MB 文件
        f = MessageFilter(MediaFilter(min_size_mb=20))
        assert f.should_download(video_message) is False

        f = MessageFilter(MediaFilter(max_size_mb=5))
        assert f.should_download(video_message) is False

        f = MessageFilter(MediaFilter(min_size_mb=5, max_size_mb=20))
        assert f.should_download(video_message) is True

    def test_keyword_whitelist(self, video_message: MagicMock) -> None:
        """关键词白名单应生效。"""
        video_message.caption = "精彩电影推荐"

        f = MessageFilter(MediaFilter(keywords=["电影"]))
        assert f.should_download(video_message) is True

        f = MessageFilter(MediaFilter(keywords=["音乐"]))
        assert f.should_download(video_message) is False

    def test_keyword_blacklist(self, video_message: MagicMock) -> None:
        """关键词黑名单应生效。"""
        video_message.caption = "广告推广内容"

        f = MessageFilter(MediaFilter(exclude_keywords=["广告"]))
        assert f.should_download(video_message) is False
