"""单元测试：代理管理器与错误处理工具。

测试故障转移、健康检查、统计指标、重试装饰器、错误分类。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from teleharvest.config.schema import ProxyConfig, ProxyType
from teleharvest.proxy.manager import ProxyManager, _proxy_key
from teleharvest.utils.retry import (
    ErrorCategory,
    categorize_error,
    extract_flood_wait,
    is_retryable,
    retry_async,
)


@pytest.fixture
def primary_proxy() -> ProxyConfig:
    """主代理配置。"""
    return ProxyConfig(
        enabled=True,
        type=ProxyType.SOCKS5,
        host="127.0.0.1",
        port=1080,
        health_check_interval=300,
        health_check_timeout=5,
    )


@pytest.fixture
def fallback_proxy() -> ProxyConfig:
    """备用代理配置。"""
    return ProxyConfig(
        enabled=True,
        type=ProxyType.HTTP,
        host="192.168.1.1",
        port=8080,
    )


@pytest.fixture
def proxy_manager(primary_proxy: ProxyConfig, fallback_proxy: ProxyConfig) -> ProxyManager:
    """带 fallback 的代理管理器。"""
    primary_proxy.fallback = [fallback_proxy]
    return ProxyManager(primary_proxy)


class TestProxyKey:
    """_proxy_key 测试。"""

    def test_generates_host_port_key(self, primary_proxy: ProxyConfig) -> None:
        """应生成 host:port 格式的键。"""
        key = _proxy_key(primary_proxy)
        assert key == "127.0.0.1:1080"

    def test_different_proxies_different_keys(
        self,
        primary_proxy: ProxyConfig,
        fallback_proxy: ProxyConfig,
    ) -> None:
        """不同代理应有不同键。"""
        assert _proxy_key(primary_proxy) != _proxy_key(fallback_proxy)


class TestProxyManagerInit:
    """ProxyManager 初始化测试。"""

    def test_disabled_proxy(self) -> None:
        """禁用的代理应初始化为直连模式。"""
        config = ProxyConfig(enabled=False)
        mgr = ProxyManager(config)
        assert mgr.is_direct_mode is True
        assert mgr.to_pyrogram_proxy() is None

    def test_stats_initial_values(self, proxy_manager: ProxyManager) -> None:
        """统计指标初始值应为 0。"""
        stats = proxy_manager.stats
        assert stats["failover_count"] == 0
        assert stats["health_check_failures"] == 0
        assert stats["health_check_successes"] == 0
        assert stats["direct_mode_duration"] == 0


class TestToPyrogramProxy:
    """to_pyrogram_proxy 测试。"""

    async def test_returns_dict_when_enabled(
        self,
        proxy_manager: ProxyManager,
        primary_proxy: ProxyConfig,
    ) -> None:
        """启用代理时应返回 dict。"""
        proxy_manager._current = primary_proxy
        result = proxy_manager.to_pyrogram_proxy()
        assert result is not None
        assert result["scheme"] == "socks5"
        assert result["hostname"] == "127.0.0.1"
        assert result["port"] == 1080

    async def test_returns_none_when_direct(self, proxy_manager: ProxyManager) -> None:
        """直连模式时应返回 None。"""
        proxy_manager._current = None
        assert proxy_manager.to_pyrogram_proxy() is None


class TestFailover:
    """故障转移测试。"""

    async def test_failover_to_fallback(
        self,
        proxy_manager: ProxyManager,
        fallback_proxy: ProxyConfig,
    ) -> None:
        """主代理不可用时应切换到备用代理。"""
        proxy_manager._current = proxy_manager._primary

        # mock 健康检查：主代理失败，备用代理成功
        async def mock_check(proxy: ProxyConfig) -> bool:
            return proxy.host == fallback_proxy.host

        with patch.object(proxy_manager, "_check_proxy", side_effect=mock_check):
            result = await proxy_manager.failover()

        assert result is not None
        assert result.host == fallback_proxy.host
        assert proxy_manager._stats["failover_count"] == 1

    async def test_failover_to_direct_when_all_fail(
        self,
        proxy_manager: ProxyManager,
    ) -> None:
        """所有代理不可用时应降级为直连。"""
        proxy_manager._current = proxy_manager._primary

        with patch.object(proxy_manager, "_check_proxy", return_value=False):
            result = await proxy_manager.failover()

        assert result is None
        assert proxy_manager.is_direct_mode is True
        assert proxy_manager._stats["failover_count"] == 1
        assert proxy_manager._stats["direct_mode_since"] > 0

    async def test_failover_skips_unhealthy(
        self,
        proxy_manager: ProxyManager,
        fallback_proxy: ProxyConfig,
    ) -> None:
        """应跳过已标记为不可用的备用代理。"""
        proxy_manager._current = proxy_manager._primary
        proxy_manager._unhealthy.add(_proxy_key(fallback_proxy))

        with patch.object(proxy_manager, "_check_proxy", return_value=False):
            result = await proxy_manager.failover()

        assert result is None
        assert proxy_manager.is_direct_mode is True


class TestMarkUnhealthy:
    """mark_unhealthy / mark_healthy 测试。"""

    def test_mark_current_unhealthy(
        self,
        proxy_manager: ProxyManager,
        primary_proxy: ProxyConfig,
    ) -> None:
        """应标记当前代理为不可用。"""
        proxy_manager._current = primary_proxy
        proxy_manager.mark_unhealthy()
        assert _proxy_key(primary_proxy) in proxy_manager._unhealthy

    def test_mark_healthy_removes_from_unhealthy(
        self,
        proxy_manager: ProxyManager,
        primary_proxy: ProxyConfig,
    ) -> None:
        """标记可用后应从不可用集合中移除。"""
        proxy_manager._current = primary_proxy
        proxy_manager.mark_unhealthy()
        assert _proxy_key(primary_proxy) in proxy_manager._unhealthy

        proxy_manager.mark_healthy()
        assert _proxy_key(primary_proxy) not in proxy_manager._unhealthy

    def test_mark_unhealthy_no_op_when_direct(self, proxy_manager: ProxyManager) -> None:
        """直连模式下标记不可用应无操作。"""
        proxy_manager._current = None
        proxy_manager.mark_unhealthy()
        assert len(proxy_manager._unhealthy) == 0


class TestCheckProxy:
    """_check_proxy 测试。"""

    async def test_check_success(
        self,
        proxy_manager: ProxyManager,
        primary_proxy: ProxyConfig,
    ) -> None:
        """TCP 连接成功时应返回 True。"""
        mock_writer = MagicMock()
        mock_writer.close = MagicMock()
        mock_writer.wait_closed = AsyncMock()

        with (
            patch("asyncio.open_connection"),
            patch("asyncio.wait_for", return_value=(MagicMock(), mock_writer)),
        ):
            result = await proxy_manager._check_proxy(primary_proxy)

        assert result is True
        assert proxy_manager._stats["health_check_successes"] == 1

    async def test_check_timeout(
        self,
        proxy_manager: ProxyManager,
        primary_proxy: ProxyConfig,
    ) -> None:
        """超时应返回 False。"""
        import asyncio as _asyncio

        with patch("asyncio.wait_for", side_effect=_asyncio.TimeoutError):
            result = await proxy_manager._check_proxy(primary_proxy)

        assert result is False
        assert proxy_manager._stats["health_check_failures"] == 1

    async def test_check_connection_error(
        self,
        proxy_manager: ProxyManager,
        primary_proxy: ProxyConfig,
    ) -> None:
        """连接错误应返回 False。"""
        with patch("asyncio.wait_for", side_effect=ConnectionError("refused")):
            result = await proxy_manager._check_proxy(primary_proxy)

        assert result is False
        assert proxy_manager._stats["health_check_failures"] == 1


# ===== 错误处理工具测试 =====


class TestCategorizeError:
    """categorize_error 测试。"""

    def test_network_error(self) -> None:
        """网络错误分类。"""
        assert categorize_error(ConnectionError("refused")) == ErrorCategory.NETWORK
        assert categorize_error(OSError("network")) == ErrorCategory.NETWORK

    def test_timeout_error(self) -> None:
        """超时错误分类。"""
        assert categorize_error(TimeoutError()) == ErrorCategory.TIMEOUT

    def test_rate_limit_error(self) -> None:
        """限流错误分类。"""
        exc = ConnectionError("FloodWait: too many requests")
        assert categorize_error(exc) == ErrorCategory.RATE_LIMIT

    def test_auth_error(self) -> None:
        """认证错误分类。"""
        exc = RuntimeError("AuthKeyError: invalid key")
        assert categorize_error(exc) == ErrorCategory.AUTH

    def test_not_found_error(self) -> None:
        """未找到错误分类。"""
        exc = RuntimeError("ChatNotFound")
        assert categorize_error(exc) == ErrorCategory.NOT_FOUND

    def test_unknown_error(self) -> None:
        """未知错误分类。"""
        exc = ValueError("something weird")
        assert categorize_error(exc) == ErrorCategory.UNKNOWN


class TestIsRetryable:
    """is_retryable 测试。"""

    def test_network_retryable(self) -> None:
        """网络错误可重试。"""
        assert is_retryable(ConnectionError("refused")) is True

    def test_timeout_retryable(self) -> None:
        """超时可重试。"""
        assert is_retryable(TimeoutError()) is True

    def test_rate_limit_retryable(self) -> None:
        """限流可重试。"""
        exc = ConnectionError("FloodWait")
        assert is_retryable(exc) is True

    def test_auth_not_retryable(self) -> None:
        """认证错误不可重试。"""
        exc = RuntimeError("AuthKeyError")
        assert is_retryable(exc) is False

    def test_not_found_not_retryable(self) -> None:
        """未找到不可重试。"""
        exc = RuntimeError("ChatNotFound")
        assert is_retryable(exc) is False


class TestExtractFloodWait:
    """extract_flood_wait 测试。"""

    def test_extract_seconds(self) -> None:
        """应从异常 value 属性提取等待秒数。"""
        exc = ConnectionError("flood")
        exc.value = 30  # type: ignore[attr-defined]
        assert extract_flood_wait(exc) == 30

    def test_no_value_attribute(self) -> None:
        """无 value 属性时应返回 None。"""
        exc = ConnectionError("no value")
        assert extract_flood_wait(exc) is None

    def test_non_numeric_value(self) -> None:
        """value 非数字时应返回 None。"""
        exc = ConnectionError("flood")
        exc.value = "abc"  # type: ignore[attr-defined]
        assert extract_flood_wait(exc) is None


class TestRetryAsync:
    """retry_async 装饰器测试。"""

    async def test_success_first_try(self) -> None:
        """首次成功不应重试。"""
        call_count = 0

        @retry_async(max_retries=3, base_delay=0.01)
        async def func() -> str:
            nonlocal call_count
            call_count += 1
            return "ok"

        result = await func()
        assert result == "ok"
        assert call_count == 1

    async def test_retry_on_network_error(self) -> None:
        """网络错误应重试。"""
        call_count = 0

        @retry_async(max_retries=3, base_delay=0.01)
        async def func() -> str:
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError("fail")
            return "ok"

        result = await func()
        assert result == "ok"
        assert call_count == 3

    async def test_no_retry_on_auth_error(self) -> None:
        """认证错误不应重试。"""
        call_count = 0

        @retry_async(max_retries=3, base_delay=0.01)
        async def func() -> str:
            nonlocal call_count
            call_count += 1
            raise RuntimeError("AuthKeyError: invalid")

        with pytest.raises(RuntimeError, match="AuthKeyError"):
            await func()
        assert call_count == 1

    async def test_max_retries_exhausted(self) -> None:
        """重试耗尽应抛出异常。"""
        call_count = 0

        @retry_async(max_retries=2, base_delay=0.01)
        async def func() -> str:
            nonlocal call_count
            call_count += 1
            raise ConnectionError("always fail")

        with pytest.raises(ConnectionError, match="always fail"):
            await func()
        assert call_count == 3  # 1 initial + 2 retries

    async def test_cancelled_not_retried(self) -> None:
        """CancelledError 不应被重试拦截。"""
        import asyncio as _asyncio

        @retry_async(max_retries=3, base_delay=0.01)
        async def func() -> str:
            raise _asyncio.CancelledError()

        with pytest.raises(_asyncio.CancelledError):
            await func()

    async def test_specific_exception_filter(self) -> None:
        """指定异常类型过滤。"""
        call_count = 0

        @retry_async(
            max_retries=3,
            base_delay=0.01,
            retryable_exceptions=(ValueError,),
        )
        async def func() -> str:
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise ValueError("retryable")
            return "ok"

        result = await func()
        assert result == "ok"
        assert call_count == 2
