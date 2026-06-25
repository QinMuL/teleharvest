"""错误处理工具：重试装饰器、错误分类、退避策略。

提供通用的异步重试机制，支持：
    - 指数退避（base_delay * 2^attempt）
    - 最大重试次数限制
    - 可重试异常类型过滤
    - FloodWait 特殊处理（遵守 Telegram 要求的等待时间）
"""

from __future__ import annotations

import asyncio
import functools
from typing import TYPE_CHECKING, Any

from loguru import logger

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


class ErrorCategory:
    """错误分类常量。"""

    NETWORK = "network"  # 网络连接错误（可重试）
    TIMEOUT = "timeout"  # 超时（可重试）
    RATE_LIMIT = "rate_limit"  # Telegram 限流（等待后可重试）
    AUTH = "auth"  # 认证错误（不可重试）
    NOT_FOUND = "not_found"  # 资源不存在（不可重试）
    UNKNOWN = "unknown"  # 未知错误


def categorize_error(exc: Exception) -> str:
    """将异常分类，用于决定是否重试。

    Args:
        exc: 捕获的异常

    Returns:
        错误分类字符串（见 ErrorCategory）
    """
    exc_name = type(exc).__name__
    exc_str = str(exc)
    exc_str_lower = exc_str.lower()

    # Telegram 限流
    if "Flood" in exc_name or "flood" in exc_str_lower:
        return ErrorCategory.RATE_LIMIT

    # 超时
    if isinstance(exc, asyncio.TimeoutError | TimeoutError):
        return ErrorCategory.TIMEOUT

    # 网络错误
    if isinstance(exc, ConnectionError | OSError):
        return ErrorCategory.NETWORK

    # 认证错误（异常类名或错误消息中包含关键字）
    if (
        "Auth" in exc_name
        or "Unauthorized" in exc_name
        or "Key" in exc_name
        or "auth" in exc_str_lower
        or "unauthorized" in exc_str_lower
        or "authkey" in exc_str_lower
    ):
        return ErrorCategory.AUTH

    # 未找到（异常类名或错误消息中包含关键字）
    if "NotFound" in exc_name or "not found" in exc_str_lower or "notfound" in exc_str_lower:
        return ErrorCategory.NOT_FOUND

    return ErrorCategory.UNKNOWN


def is_retryable(exc: Exception) -> bool:
    """判断异常是否可重试。

    可重试：网络错误、超时、限流
    不可重试：认证错误、未找到、未知错误
    """
    category = categorize_error(exc)
    return category in (ErrorCategory.NETWORK, ErrorCategory.TIMEOUT, ErrorCategory.RATE_LIMIT)


def extract_flood_wait(exc: Exception) -> int | None:
    """从异常中提取 FloodWait 等待时间（秒）。

    Pyrogram 的 FloodWait 异常包含 value 属性表示等待秒数。
    """
    if hasattr(exc, "value"):
        try:
            return int(exc.value)
        except (TypeError, ValueError):
            pass
    return None


def retry_async(
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    retryable_exceptions: tuple[type[Exception], ...] | None = None,
) -> Callable[[Callable[..., Awaitable[Any]]], Callable[..., Awaitable[Any]]]:
    """异步重试装饰器（指数退避）。

    Args:
        max_retries: 最大重试次数（不含首次调用）
        base_delay: 首次重试延迟（秒）
        max_delay: 最大重试延迟上限（秒）
        retryable_exceptions: 可重试的异常类型，None 表示用 is_retryable 判断

    Returns:
        装饰后的异步函数

    示例::

        @retry_async(max_retries=3, base_delay=2.0)
        async def fetch_data():
            ...
    """

    def decorator(
        func: Callable[..., Awaitable[Any]],
    ) -> Callable[..., Awaitable[Any]]:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            last_exc: Exception | None = None

            for attempt in range(max_retries + 1):
                try:
                    return await func(*args, **kwargs)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    last_exc = exc

                    # 判断是否可重试
                    if retryable_exceptions is not None:
                        should_retry = isinstance(exc, retryable_exceptions)
                    else:
                        should_retry = is_retryable(exc)

                    if not should_retry or attempt >= max_retries:
                        logger.error(
                            "{} 失败，不再重试: attempt={}, error={}",
                            func.__name__,
                            attempt + 1,
                            exc,
                        )
                        raise

                    # 计算延迟
                    flood_wait = extract_flood_wait(exc)
                    if flood_wait:
                        delay = float(flood_wait)
                    else:
                        delay = min(base_delay * (2**attempt), max_delay)

                    logger.warning(
                        "{} 失败，{}s 后重试: attempt={}/{}, error={}",
                        func.__name__,
                        f"{delay:.1f}",
                        attempt + 1,
                        max_retries + 1,
                        exc,
                    )
                    await asyncio.sleep(delay)

            # 理论上不会执行到这里
            if last_exc:
                raise last_exc
            raise RuntimeError("retry loop exhausted without exception")

        return wrapper

    return decorator
