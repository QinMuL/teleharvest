"""应用生命周期管理：组件初始化顺序与优雅停止。

组件初始化顺序（依赖关系自底向上）：
    1. Logger        —— 日志系统（无依赖）
    2. Database      —— 数据持久化（无依赖）
    3. ProxyManager  —— 代理连接（无依赖）
    4. StorageManager—— 文件存储（依赖 Database）
    5. DownloadEngine—— 下载引擎（依赖 StorageManager, ProxyManager）
    6. MonitorClient —— TG 监控（依赖 ProxyManager, Scheduler）
    7. Scheduler     —— 任务调度（依赖 DownloadEngine）

停止顺序与初始化相反，确保依赖项最后释放。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from teleharvest.config.settings import AppSettings  # type: ignore[attr-defined]
    from teleharvest.core.scheduler import Scheduler
    from teleharvest.db.session import DatabaseSession
    from teleharvest.downloader.engine import DownloadEngine
    from teleharvest.monitor.client import MonitorClient
    from teleharvest.proxy.manager import ProxyManager
    from teleharvest.storage.manager import StorageManager


@dataclass
class AppContext:
    """应用上下文：持有所有运行时组件的引用。

    使用 dataclass 便于在停止阶段按顺序释放资源。
    """

    settings: AppSettings
    db: DatabaseSession | None = None
    proxy: ProxyManager | None = None
    storage: StorageManager | None = None
    downloader: DownloadEngine | None = None
    monitor: MonitorClient | None = None
    scheduler: Scheduler | None = None
    _started: bool = field(default=False, init=False)

    async def startup(self) -> None:
        """按依赖顺序启动所有组件。"""
        # TODO(P1~P3): 逐步填充各组件的初始化逻辑
        self._started = True

    async def shutdown(self) -> None:
        """按逆序停止所有组件，确保优雅关闭。"""
        if not self._started:
            return
        # 停止顺序与启动相反
        for component in (
            self.monitor,
            self.scheduler,
            self.downloader,
            self.storage,
            self.proxy,
            self.db,
        ):
            if component is not None:
                stop = getattr(component, "stop", None) or getattr(component, "close", None)
                if stop is not None:
                    await stop()
        self._started = False
