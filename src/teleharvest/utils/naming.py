"""智能文件名生成：从消息 caption、视频元数据中提取标题。

命名优先级（组合策略）：
    1. 消息 caption 第一行有效文本
    2. ffprobe 读取视频/音频元数据中的 title 标签
    3. 原始文件名（若非默认占位名）
    4. 生成默认名：{media_type}_{message_id}_{date}.{ext}
"""

from __future__ import annotations

import re
import subprocess
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from datetime import datetime
    from pathlib import Path


def extract_title_from_caption(caption: str) -> str:
    """从消息 caption 中提取标题（第一行有效文本）。

    Args:
        caption: 消息附带文本

    Returns:
        提取的标题，无有效内容时返回空字符串
    """
    if not caption:
        return ""

    for line in caption.strip().splitlines():
        line = line.strip()
        if line:
            return line
    return ""


def extract_title_from_metadata(file_path: Path) -> str:
    """用 ffprobe 读取视频/音频文件的 title 元数据。

    Args:
        file_path: 媒体文件路径

    Returns:
        title 元数据值，无则返回空字符串
    """
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "quiet",
                "-show_entries",
                "format_tags=title",
                "-of",
                "default=nw=1:nk=1",
                str(file_path),
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        title = result.stdout.strip()
        if title and title != "N/A":
            return title
    except FileNotFoundError:
        logger.debug("ffprobe 未安装，跳过元数据标题提取")
    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.debug("ffprobe 读取元数据失败: {}", exc)
    return ""


def sanitize_filename(name: str) -> str:
    """清理文件名中的非法字符。

    Args:
        name: 原始名称

    Returns:
        安全的文件名（不含路径分隔符、控制字符等）
    """
    # Windows/Linux 非法字符替换为下划线
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    # 去除首尾空格和点
    name = name.strip(". ")
    # 限制长度（留出扩展名空间）
    if len(name) > 200:
        name = name[:200]
    return name


def build_smart_filename(
    original_name: str,
    title: str,
    media_type: str,
    message_id: int,
    timestamp: datetime | None = None,
) -> str:
    """生成智能文件名。

    优先级：title > original_name > 生成默认名

    Args:
        original_name: 原始文件名
        title: 从 caption 或元数据提取的标题
        media_type: 媒体类型（video/audio/document/photo）
        message_id: 消息 ID
        timestamp: 消息时间

    Returns:
        智能文件名（含扩展名）
    """
    # 获取原始扩展名
    ext = ""
    if "." in original_name:
        ext = original_name.rsplit(".", 1)[-1].lower()
    elif media_type == "video":
        ext = "mp4"
    elif media_type == "audio":
        ext = "mp3"
    elif media_type == "photo":
        ext = "jpg"

    # 清理标题
    clean_title = sanitize_filename(title)

    if clean_title:
        return f"{clean_title}.{ext}" if ext else clean_title

    # 原始文件名有效（非占位名）时保留
    default_names = {
        f"video_{message_id}.mp4",
        f"audio_{message_id}.mp3",
        f"document_{message_id}",
        f"photo_{message_id}.jpg",
    }
    if original_name and original_name not in default_names:
        return original_name

    # 生成默认文件名
    if timestamp:
        date_str = timestamp.strftime("%Y%m%d")
        return f"{media_type}_{message_id}_{date_str}.{ext}"
    return f"{media_type}_{message_id}.{ext}"
