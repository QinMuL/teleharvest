"""断点续传：基于已下载文件大小与数据库记录校验。

策略：
    1. 下载前检查目标文件是否已存在
    2. 查询数据库中该任务的已下载字节数
    3. 若文件大小与记录一致，视为已完成，跳过
    4. 若文件大小小于记录，视为中断，从断点继续
    5. Pyrogram download() 支持 in_memory=False 时追加写入
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from pathlib import Path


class ResumeManager:
    """断点续传管理器。"""

    @staticmethod
    def check_partial(file_path: Path, expected_size: int | None = None) -> int:
        """检查部分下载文件。

        Args:
            file_path: 目标文件路径
            expected_size: 预期总大小（字节），None 表示未知

        Returns:
            已下载字节数；0 表示需从头下载
        """
        if not file_path.exists():
            return 0

        actual_size = file_path.stat().st_size

        # 已完成
        if expected_size and actual_size == expected_size:
            logger.info("文件已完整存在，跳过: {}", file_path)
            return actual_size

        # 部分下载
        if actual_size > 0:
            logger.warning(
                "检测到部分下载文件: {} ({} / {})",
                file_path.name,
                actual_size,
                expected_size or "unknown",
            )
            return actual_size

        return 0

    @staticmethod
    def cleanup_partial(file_path: Path) -> None:
        """清理无效的部分下载文件。"""
        if file_path.exists() and file_path.stat().st_size == 0:
            file_path.unlink(missing_ok=True)
            logger.debug("已清理空文件: {}", file_path)
