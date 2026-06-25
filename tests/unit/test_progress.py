"""单元测试：ProgressTracker 进度追踪器。

验证 async callback 触发、异常吞没、百分比日志间隔等行为。
"""

from __future__ import annotations

from teleharvest.downloader.progress import ProgressTracker, _format_bytes


class TestFormatBytes:
    """_format_bytes 函数测试。"""

    def test_bytes(self) -> None:
        assert _format_bytes(0) == "0.0 B"
        assert _format_bytes(1023) == "1023.0 B"

    def test_kb(self) -> None:
        assert _format_bytes(1024) == "1.0 KB"
        assert _format_bytes(1536) == "1.5 KB"

    def test_mb(self) -> None:
        assert _format_bytes(1024 * 1024) == "1.0 MB"

    def test_gb(self) -> None:
        assert _format_bytes(1024**3) == "1.0 GB"


class TestProgressTracker:
    """ProgressTracker 测试。"""

    async def test_callback_invoked(self) -> None:
        """async callback 被正确调用。"""
        calls: list[tuple[int, int]] = []

        async def cb(current: int, total: int) -> None:
            calls.append((current, total))

        tracker = ProgressTracker(callback=cb)
        await tracker.on_progress(100, 1000)
        assert calls == [(100, 1000)]

    async def test_callback_exception_swallowed(self) -> None:
        """callback 抛异常时被吞掉，不影响 on_progress。"""
        call_count = 0

        async def cb(current: int, total: int) -> None:
            nonlocal call_count
            call_count += 1
            raise RuntimeError("test error")

        tracker = ProgressTracker(callback=cb)
        # 不抛异常
        await tracker.on_progress(100, 1000)
        await tracker.on_progress(200, 1000)
        assert call_count == 2

    async def test_no_callback(self) -> None:
        """无 callback 时不报错。"""
        tracker = ProgressTracker()
        await tracker.on_progress(100, 1000)

    async def test_total_zero(self) -> None:
        """total=0 时不报错（避免除零）。"""
        tracker = ProgressTracker()
        await tracker.on_progress(100, 0)

    async def test_multiple_callbacks(self) -> None:
        """多次进度更新都被转发到 callback。"""
        calls: list[tuple[int, int]] = []

        async def cb(current: int, total: int) -> None:
            calls.append((current, total))

        tracker = ProgressTracker(callback=cb, log_interval_percent=10)
        await tracker.on_progress(50, 1000)
        await tracker.on_progress(500, 1000)
        await tracker.on_progress(1000, 1000)
        assert len(calls) == 3
        assert calls[-1] == (1000, 1000)
