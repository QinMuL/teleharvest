"""pytest 公共夹具。

为单元测试与集成测试提供通用 fixture。
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from teleharvest.config.schema import (
    AppSettings,
    DatabaseSettings,
    DownloaderSettings,
    LoggingSettings,
    MonitorSettings,
    ProxyConfig,
    StorageSettings,
)

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path

# ===== 事件循环 =====


@pytest.fixture(scope="session")
def event_loop() -> Generator[asyncio.AbstractEventLoop, None, None]:
    """会话级事件循环，避免每个测试创建新循环。"""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


# ===== 配置夹具 =====


@pytest.fixture
def tmp_data_dir(tmp_path: Path) -> Path:
    """临时数据目录。"""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    return data_dir


@pytest.fixture
def test_settings(tmp_data_dir: Path) -> AppSettings:
    """测试用应用配置（使用临时目录，跳过凭据校验）。"""
    return AppSettings(
        api_id=12345,
        api_hash="0" * 32,  # 32 位十六进制
        bot_token="",
        monitor=MonitorSettings(
            session_dir=tmp_data_dir / "sessions",
        ),
        downloader=DownloaderSettings(max_concurrency=2),
        storage=StorageSettings(root_dir=tmp_data_dir / "downloads"),
        proxy=ProxyConfig(enabled=False),
        logging=LoggingSettings(
            level="DEBUG",
            dir=tmp_data_dir / "logs",
            console=False,  # 测试时关闭控制台输出
        ),
        database=DatabaseSettings(
            url=f"sqlite+aiosqlite:///{tmp_data_dir / 'test.db'}",
        ),
    )


# ===== 标记注册 =====


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """自动为测试添加标记（基于目录路径）。"""
    for item in items:
        # 根据路径自动添加标记
        if "tests/unit/" in str(item.fspath):
            item.add_marker(pytest.mark.unit)
        elif "tests/integration/" in str(item.fspath):
            item.add_marker(pytest.mark.integration)
        elif "tests/e2e/" in str(item.fspath):
            item.add_marker(pytest.mark.e2e)
