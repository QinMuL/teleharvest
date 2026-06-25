"""下载完成通知卡片格式化。

基于消息 caption 和文件元数据构造卡片，不调用 TMDB API。

卡片格式遵循用户偏好：
    - 每类信息独占一行，格式 ``icon 标签：内容``
    - 源链接全文显示在 ``🔗 源链接：`` 标签下新行
    - 简介截断到 150 字符放在最后
    - 无有效内容的信息行自动省略
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime

    from teleharvest.core.task import DownloadTask


def build_complete_card(task: DownloadTask, file_path: str, file_size: int) -> str:
    """构造下载完成通知卡片文本。

    Args:
        task: 下载任务（含 caption、频道、消息 ID 等元数据）
        file_path: 已下载文件路径
        file_size: 文件大小（字节）

    Returns:
        格式化后的卡片文本
    """
    from pathlib import Path

    from teleharvest.utils.naming import extract_title_from_caption

    file_name = Path(file_path).name

    # 1. 标题：caption 第一行 > 文件名
    title = extract_title_from_caption(task.caption) or file_name

    # 2. 媒体类型标签
    type_label = _media_type_label(task.media_type)

    # 3. 质量：从文件名推断
    quality = _infer_quality(task.file_name)

    # 4. 大小
    size_str = _format_bytes(file_size)

    # 5. 日期
    date_str = _format_date(task.message_date)

    # 6. 源链接
    source_url = _build_source_url(task.channel_id, task.message_id)

    # 7. 简介：caption 去掉标题行后的内容，截断 150 字符
    synopsis = _extract_synopsis(task.caption, title)

    # 组装卡片
    lines: list[str] = ["✅ 下载完成", ""]

    # 标题行
    icon = _title_icon(task.media_type)
    lines.append(f"{icon} {type_label}：{title}")

    # 质量行（有则显示）
    if quality:
        lines.append(f"💿 质量：{quality}")

    # 大小行
    lines.append(f"📦 大小：{size_str}")

    # 日期行（有则显示）
    if date_str:
        lines.append(f"📅 日期：{date_str}")

    # 频道行
    lines.append(f"📌 频道：{task.channel_alias}")

    # 源链接（全文显示在新行）
    lines.append("")
    lines.append("🔗 源链接：")
    lines.append(source_url)

    # 简介（截断 150 字符，放在最后）
    if synopsis:
        lines.append("")
        lines.append("📝 简介：")
        lines.append(synopsis)

    return "\n".join(lines)


def _media_type_label(media_type: str) -> str:
    """媒体类型对应的中文标签。"""
    labels = {
        "video": "视频",
        "audio": "音频",
        "document": "文档",
        "photo": "图片",
    }
    return labels.get(media_type, "资源")


def _title_icon(media_type: str) -> str:
    """标题行图标（按媒体类型）。"""
    icons = {
        "video": "🎞️",
        "audio": "🎵",
        "document": "📄",
        "photo": "🖼️",
    }
    return icons.get(media_type, "📦")


def _infer_quality(file_name: str) -> str:
    """从文件名推断视频质量标记。

    识别常见分辨率和来源标记，如 2160p/1080p/720p、HDR、WEB-DL、BluRay 等。
    """
    if not file_name:
        return ""
    name = file_name.lower()

    # 分辨率
    resolution = ""
    for res in ["2160p", "1080p", "720p", "480p", "360p"]:
        if res in name:
            resolution = "4K" if res == "2160p" else res
            break

    # 来源标记
    source = ""
    if "web-dl" in name or "webdl" in name or "webrip" in name:
        source = "WEB-DL"
    elif "bluray" in name or "bdrip" in name or "brrip" in name:
        source = "BluRay"
    elif "hdtv" in name:
        source = "HDTV"
    elif "remux" in name:
        source = "REMUX"

    # HDR 标记
    hdr = "HDR" if "hdr" in name else ""

    parts = [p for p in [resolution, source, hdr] if p]
    return " ".join(parts)


def _build_source_url(channel_id: str | int, message_id: int) -> str:
    """构造 Telegram 消息源链接。

    - 数字 ID（超级群组/频道，以 -100 开头）：``https://t.me/c/{positive_id}/{msg_id}``
    - @username：``https://t.me/{username}/{msg_id}``
    """
    cid_str = str(channel_id)

    # 数字 ID：去掉 -100 前缀
    if cid_str.lstrip("-").isdigit():
        # 超级群组/频道 ID 形如 -1001234567890，链接中用 1234567890
        if cid_str.startswith("-100"):
            positive_id = cid_str[4:]
            return f"https://t.me/c/{positive_id}/{message_id}"
        # 其他数字 ID（罕见，直接用绝对值）
        return f"https://t.me/c/{cid_str.lstrip('-')}/{message_id}"

    # @username 形式
    username = cid_str.lstrip("@")
    return f"https://t.me/{username}/{message_id}"


def _extract_synopsis(caption: str, title: str) -> str:
    """从 caption 提取简介（去掉首行标题后的内容，截断到 150 字符）。

    Args:
        caption: 消息附带文本
        title: 已提取的标题（用于从 caption 中去除首行）

    Returns:
        简介文本（截断到 150 字符），无有效内容时返回空字符串
    """
    if not caption:
        return ""
    lines = caption.strip().splitlines()
    # 去掉首行（通常与 title 重复）
    if lines and title and lines[0].strip() == title:
        lines = lines[1:]
    synopsis = " ".join(line.strip() for line in lines if line.strip())
    if len(synopsis) > 150:
        return synopsis[:150] + "..."
    return synopsis


def _format_date(dt: datetime | None) -> str:
    """格式化日期为 YYYY-MM-DD。"""
    if dt is None:
        return ""
    try:
        return dt.strftime("%Y-%m-%d")
    except (AttributeError, ValueError):
        return ""


def _format_bytes(num: float) -> str:
    """字节数格式化为人类可读字符串。"""
    if num <= 0:
        return "0 B"
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if abs(num) < 1024.0:
            return f"{num:.1f} {unit}"
        num /= 1024.0
    return f"{num:.1f} PB"
