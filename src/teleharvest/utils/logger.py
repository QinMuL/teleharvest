"""日志系统配置：基于 loguru 的统一日志方案。

特性：
    - 控制台彩色输出（可关闭）
    - 文件轮转（按大小，可配置保留数与压缩）
    - 敏感信息脱敏（API Hash、Token、密码）
    - 模块级 logger 获取
"""

from __future__ import annotations

import re
import sys
from typing import TYPE_CHECKING, Any

from loguru import logger

if TYPE_CHECKING:
    from teleharvest.config.schema import LoggingSettings

# 需要脱敏的敏感字段名模式
_SENSITIVE_PATTERNS = [
    re.compile(r"(api_hash\s*[:=]\s*)['\"]?([a-f0-9]{32})['\"]?", re.IGNORECASE),
    re.compile(r"(bot_token\s*[:=]\s*)['\"]?(\d+:[A-Za-z0-9_-]{35})['\"]?", re.IGNORECASE),
    re.compile(r"(password\s*[:=]\s*)['\"]?(\S+)['\"]?", re.IGNORECASE),
    re.compile(r"(api_id\s*[:=]\s*)(\d+)", re.IGNORECASE),
]


def _redact_sensitive(message: str) -> str:
    """对日志消息中的敏感信息进行脱敏。"""
    redacted = message
    for pattern in _SENSITIVE_PATTERNS:
        redacted = pattern.sub(lambda m: f"{m.group(1)}***", redacted)
    return redacted


def _redact_patcher(record: dict[str, Any]) -> None:
    """loguru patcher：在记录前脱敏。"""
    record["message"] = _redact_sensitive(record["message"])


def setup_logging(settings: LoggingSettings) -> None:
    """初始化全局日志系统。

    Args:
        settings: 日志配置
    """
    # 移除默认 handler
    logger.remove()

    # 注册脱敏 patcher
    logger.configure(patcher=_redact_patcher)  # type: ignore[arg-type]

    # 日志格式
    fmt = (
        "{time:YYYY-MM-DD HH:mm:ss.SSS} | "
        "<level>{level: <8}</level> | "
        "{module}:{function}:{line} | "
        "{message}"
    )

    # 控制台输出
    if settings.console:
        logger.add(
            sys.stderr,
            level=settings.level,
            format=fmt,
            colorize=settings.colorize,
            backtrace=True,
            diagnose=True,
        )

    # 文件输出（轮转 + 保留 + 压缩）
    log_dir = settings.dir
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / settings.filename

    logger.add(
        str(log_file),
        level=settings.level,
        format=fmt,
        rotation=f"{settings.rotation_mb} MB",
        retention=settings.retention,
        compression="zip" if settings.compression else None,
        backtrace=True,
        diagnose=False,  # 文件中不输出变量值，避免泄露
        enqueue=True,  # 异步写入，线程安全
    )

    logger.info("日志系统已初始化: level={}, file={}", settings.level, log_file)


def get_logger(name: str | None = None) -> Any:
    """获取模块级 logger。

    Args:
        name: 模块名（通常传 __name__）

    Returns:
        绑定了模块名的 logger 实例
    """
    if name:
        return logger.bind(module=name)
    return logger
