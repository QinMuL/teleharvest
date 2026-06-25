"""代理管理器：统一代理连接管理、故障转移、健康检查。

支持的代理类型：
    - SOCKS5（推荐，支持 UDP）
    - SOCKS4
    - HTTP
    - HTTPS

故障转移策略：
    1. 优先使用主代理
    2. 主代理不可用时，按顺序尝试 fallback 列表
    3. 全部失败则降级为直连，并记录 WARNING
    4. 定期健康检查，恢复后自动切回代理

统计指标：
    - failover_count: 故障转移次数
    - health_check_failures: 健康检查失败次数
    - direct_mode_duration: 直连模式持续时间
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from teleharvest.config.schema import ProxyConfig


def _proxy_key(proxy: ProxyConfig) -> str:
    """生成代理的唯一标识键（host:port）。

    使用 host:port 而非 id()，确保跨 Python 会话稳定。
    """
    return f"{proxy.host}:{proxy.port}"


class ProxyManager:
    """统一代理连接管理器。"""

    def __init__(self, config: ProxyConfig) -> None:
        self._primary = config
        self._fallbacks = config.fallback
        self._current: ProxyConfig | None = None
        self._unhealthy: set[str] = set()  # 不可用代理的 host:port
        self._health_task: asyncio.Task[None] | None = None

        # 统计指标
        self._stats = {
            "failover_count": 0,
            "health_check_failures": 0,
            "health_check_successes": 0,
            "direct_mode_since": 0.0,  # 直连模式开始时间戳
        }

    async def start(self) -> None:
        """启动代理管理器，初始化当前代理并开启健康检查。"""
        if not self._primary.enabled:
            logger.info("代理未启用，使用直连")
            return

        self._current = self._primary
        logger.info(
            "代理已启用: type={}, host={}, port={}",
            self._primary.type.value,
            self._primary.host,
            self._primary.port,
        )

        # 启动周期性健康检查
        self._health_task = asyncio.create_task(self._health_check_loop())

    async def stop(self) -> None:
        """停止代理管理器。"""
        if self._health_task:
            self._health_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._health_task
            self._health_task = None
        logger.info("代理管理器已停止: stats={}", self._stats)

    def to_pyrogram_proxy(self) -> dict[str, str | int | None] | None:
        """转换为 Pyrogram Client 接受的 proxy dict 格式。

        Pyrogram 2.0.106 的 TCP.__init__ 期望 dict，key 为：
        scheme / hostname / port / username / password

        Returns:
            代理 dict，或 None（直连）
        """
        if not self._current or not self._current.enabled:
            return None

        proxy = self._current
        return {
            "scheme": proxy.type.value,
            "hostname": proxy.host,
            "port": proxy.port,
            "username": proxy.username or None,
            "password": proxy.password or None,
        }

    async def failover(self) -> ProxyConfig | None:
        """故障转移：切换到下一个可用代理。

        Returns:
            新的可用代理配置，或 None（全部不可用，降级直连）
        """
        logger.warning("主代理不可用，尝试故障转移")
        self._stats["failover_count"] += 1

        # 标记当前代理为不可用
        if self._current is not None:
            self._unhealthy.add(_proxy_key(self._current))

        # 尝试 fallback 列表
        for proxy in self._fallbacks:
            if _proxy_key(proxy) in self._unhealthy:
                continue
            if await self._check_proxy(proxy):
                self._current = proxy
                logger.warning(
                    "已切换到备用代理: type={}, host={}, port={}",
                    proxy.type.value,
                    proxy.host,
                    proxy.port,
                )
                return proxy

        # 全部失败，降级直连
        self._current = None
        self._stats["direct_mode_since"] = time.time()
        logger.error("所有代理均不可用，降级为直连（可能无法访问 Telegram）")
        return None

    def mark_unhealthy(self, proxy: ProxyConfig | None = None) -> None:
        """手动标记代理为不可用（外部调用，如连接失败时）。

        Args:
            proxy: 要标记的代理，None 表示当前代理
        """
        target = proxy or self._current
        if target is not None:
            self._unhealthy.add(_proxy_key(target))
            logger.debug("代理已标记为不可用: {}", _proxy_key(target))

    def mark_healthy(self, proxy: ProxyConfig | None = None) -> None:
        """手动标记代理为可用（外部调用，如连接恢复时）。

        Args:
            proxy: 要标记的代理，None 表示当前代理
        """
        target = proxy or self._current
        if target is not None:
            self._unhealthy.discard(_proxy_key(target))
            logger.debug("代理已标记为可用: {}", _proxy_key(target))

    @property
    def is_direct_mode(self) -> bool:
        """当前是否为直连模式（无代理）。"""
        return self._current is None

    @property
    def stats(self) -> dict[str, int | float]:
        """获取统计指标。"""
        stats = dict(self._stats)
        # 计算直连模式持续时间
        if stats["direct_mode_since"] > 0:
            stats["direct_mode_duration"] = int(time.time() - stats["direct_mode_since"])
        else:
            stats["direct_mode_duration"] = 0
        return stats

    async def _health_check_loop(self) -> None:
        """周期性健康检查循环。"""
        while True:
            await asyncio.sleep(self._primary.health_check_interval)
            await self._check_current()

    async def _check_current(self) -> None:
        """检查当前代理是否健康。"""
        if not self._current:
            # 当前为直连，尝试恢复主代理
            if await self._check_proxy(self._primary):
                self._current = self._primary
                self._stats["direct_mode_since"] = 0.0
                self._unhealthy.discard(_proxy_key(self._primary))
                logger.info("主代理已恢复，切回: {}", self._primary.host)
            return

        if not await self._check_proxy(self._current):
            logger.warning("当前代理健康检查失败: {}", self._current.host)
            await self.failover()

    async def _check_proxy(self, proxy: ProxyConfig) -> bool:
        """TCP 探测代理目标是否可达。

        Args:
            proxy: 代理配置

        Returns:
            True 表示代理可用
        """
        try:
            # 通过代理连接 Telegram DC IP 验证可用性
            # 这里简化为 TCP 连接代理服务器本身
            future = asyncio.open_connection(proxy.host, proxy.port)
            _reader, writer = await asyncio.wait_for(
                future, timeout=self._primary.health_check_timeout
            )
            writer.close()
            await writer.wait_closed()
            self._stats["health_check_successes"] += 1
            return True
        except (TimeoutError, OSError) as exc:
            self._stats["health_check_failures"] += 1
            logger.debug("代理健康检查失败: {}:{} ({})", proxy.host, proxy.port, exc)
            return False
