"""单元测试：配置加载与校验。"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from teleharvest.config.schema import (
    AppSettings,
    ChannelConfig,
    MediaFilter,
    ProxyConfig,
    ProxyType,
)


class TestMediaFilter:
    """MediaFilter 规则测试。"""

    def test_default_filter_accepts_all(self) -> None:
        """默认过滤器应接受所有类型。"""
        f = MediaFilter()
        assert f.types == []
        assert f.extensions == []

    def test_extensions_normalized(self) -> None:
        """扩展名应被标准化为小写且不含前导点。"""
        f = MediaFilter(extensions=["MP4", ".mkv", ".Mp3"])
        assert f.extensions == ["mp4", "mkv", "mp3"]


class TestChannelConfig:
    """ChannelConfig 测试。"""

    def test_alias_defaults_to_id(self) -> None:
        """未提供 alias 时应默认使用 id。"""
        ch = ChannelConfig(id="@test_channel")
        assert ch.alias == "@test_channel"

    def test_custom_alias_preserved(self) -> None:
        """自定义 alias 应被保留。"""
        ch = ChannelConfig(id="@test_channel", alias="测试频道")
        assert ch.alias == "测试频道"


class TestProxyConfig:
    """ProxyConfig 测试。"""

    def test_default_disabled(self) -> None:
        """默认应禁用代理。"""
        p = ProxyConfig()
        assert p.enabled is False
        assert p.type == ProxyType.SOCKS5

    def test_fallback_no_nested(self) -> None:
        """fallback 不允许嵌套 fallback。"""
        inner = ProxyConfig(enabled=True, host="inner", port=1080)
        nested = ProxyConfig(enabled=True, host="nested", port=1080, fallback=[inner])
        with pytest.raises(ValueError, match="嵌套"):
            ProxyConfig(
                enabled=True,
                host="primary",
                port=1080,
                fallback=[nested],
            )


class TestAppSettings:
    """AppSettings 根配置测试。"""

    def test_missing_credentials_raises(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """缺少 Telegram 凭据应抛出校验错误。"""
        # 清除环境变量 + 切换到无 .env 的临时目录，避免外部干扰
        monkeypatch.delenv("TELEHARVEST_API_ID", raising=False)
        monkeypatch.delenv("TELEHARVEST_API_HASH", raising=False)
        monkeypatch.delenv("TELEHARVEST_BOT_TOKEN", raising=False)
        monkeypatch.chdir(tmp_path)
        with pytest.raises(ValueError, match="API"):
            AppSettings(api_id=0, api_hash="")

    def test_valid_credentials(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """提供有效凭据应成功创建。"""
        monkeypatch.delenv("TELEHARVEST_API_ID", raising=False)
        monkeypatch.delenv("TELEHARVEST_API_HASH", raising=False)
        monkeypatch.delenv("TELEHARVEST_BOT_TOKEN", raising=False)
        monkeypatch.chdir(tmp_path)
        settings = AppSettings(api_id=12345, api_hash="a" * 32)
        assert settings.api_id == 12345
        assert settings.monitor.history_limit == 0
        assert settings.downloader.max_concurrency == 3
