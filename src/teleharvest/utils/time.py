"""时间工具：统一时区处理与格式化。"""

from __future__ import annotations

from datetime import UTC, datetime

# 统一使用 UTC 时间戳存储，展示时按需转换
UTC = UTC


def now_utc() -> datetime:
    """获取当前 UTC 时间。"""
    return datetime.now(UTC)


def to_timestamp(dt: datetime) -> int:
    """datetime 转 Unix 时间戳（秒）。"""
    return int(dt.timestamp())


def from_timestamp(ts: int) -> datetime:
    """Unix 时间戳转 datetime（UTC）。"""
    return datetime.fromtimestamp(ts, UTC)


def format_datetime(dt: datetime, fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    """格式化 datetime 为字符串。"""
    return dt.strftime(fmt)
