"""存储管理器：文件命名、目录组织、磁盘空间监控。

目录组织方式（由 StorageSettings.structure 决定）：
    - by_channel: {root}/{channel_alias}/{date}/{filename}
    - by_date:     {root}/{date}/{channel_alias}/{filename}
    - by_type:     {root}/{media_type}/{channel_alias}/{filename}
    - flat:        {root}/{filename}
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from pathlib import Path

    from teleharvest.config.schema import StorageSettings


class StorageManager:
    """文件存储管理器。"""

    def __init__(self, settings: StorageSettings) -> None:
        self._settings = settings
        self._root = settings.root_dir
        self._root.mkdir(parents=True, exist_ok=True)

    def build_path(
        self,
        channel_alias: str,
        media_type: str,
        original_name: str,
        message_id: int,
        timestamp: datetime | None = None,
    ) -> Path:
        """根据组织策略构建目标文件路径。

        Args:
            channel_alias: 频道别名
            media_type: 媒体类型（audio/video/document/photo）
            original_name: 原始文件名
            message_id: 消息 ID（用于命名去重）
            timestamp: 消息时间，默认当前

        Returns:
            完整目标文件路径
        """
        ts = timestamp or datetime.now()
        date_str = ts.strftime("%Y-%m-%d")

        # 应用命名模板
        filename = self._settings.filename_template.format(
            date=date_str,
            channel=channel_alias,
            type=media_type,
            original_name=original_name or f"msg_{message_id}",
            id=message_id,
        )

        # 按组织策略构建子目录
        structure = self._settings.structure
        if structure == "by_channel":
            path = self._root / channel_alias / date_str / filename
        elif structure == "by_date":
            path = self._root / date_str / channel_alias / filename
        elif structure == "by_type":
            path = self._root / media_type / channel_alias / filename
        else:  # flat
            path = self._root / filename

        # 确保父目录存在
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def check_free_space(self) -> float:
        """检查磁盘可用空间（GB）。

        Returns:
            可用空间（GB）
        """
        usage = self._get_disk_usage()
        free_gb = usage["free"] / (1024**3)
        threshold = self._settings.min_free_space_gb

        if free_gb < threshold:
            logger.warning(
                "磁盘空间不足: 可用 {:.2f} GB < 阈值 {:.2f} GB",
                free_gb,
                threshold,
            )
        return free_gb

    def _get_disk_usage(self) -> dict[str, int]:
        """获取磁盘使用情况。"""
        import shutil

        total, used, free = shutil.disk_usage(self._root)
        return {"total": total, "used": used, "free": free}

    async def stop(self) -> None:
        """存储管理器无需特殊停止逻辑。"""
        pass
