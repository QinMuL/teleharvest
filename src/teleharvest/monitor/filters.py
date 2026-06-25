"""消息过滤规则引擎：按媒体类型、扩展名、大小、关键词过滤。

过滤逻辑：
    1. 媒体类型白名单检查
    2. 文件扩展名白名单检查
    3. 文件大小范围检查
    4. 关键词白名单检查（匹配消息文本或文件名）
    5. 关键词黑名单检查（任一命中即拒绝）

所有规则为 AND 关系，任一环节不通过即拒绝。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pyrogram.types import Message

    from teleharvest.config.schema import MediaFilter


class MessageFilter:
    """消息过滤器，封装 MediaFilter 规则的判定逻辑。"""

    def __init__(self, rules: MediaFilter) -> None:
        self._rules = rules

    def should_download(self, message: Message) -> bool:
        """判断消息是否应被下载。

        Args:
            message: Pyrogram 消息对象

        Returns:
            True 表示通过过滤，应下载；False 表示应跳过
        """
        # 1. 提取媒体信息
        media_info = self.extract_media_info(message)
        if media_info is None:
            return False  # 无媒体内容

        media_type, file_name, file_size = media_info

        # 2. 媒体类型白名单
        if self._rules.types and media_type not in self._rules.types:
            return False

        # 3. 扩展名白名单
        if self._rules.extensions:
            ext = self._get_extension(file_name)
            if ext not in self._rules.extensions:
                return False

        # 4. 文件大小范围
        size_mb = file_size / (1024 * 1024) if file_size else 0
        if self._rules.min_size_mb and size_mb < self._rules.min_size_mb:
            return False
        if self._rules.max_size_mb and size_mb > self._rules.max_size_mb:
            return False

        # 5. 关键词匹配（消息文本 + 文件名）
        text = self._get_match_text(message, file_name)
        if self._rules.exclude_keywords:
            for kw in self._rules.exclude_keywords:
                if kw.lower() in text.lower():
                    return False
        if self._rules.keywords:
            text_lower = text.lower()
            if not any(kw.lower() in text_lower for kw in self._rules.keywords):
                return False

        return True

    def extract_media_info(self, message: Message) -> tuple[str, str, int] | None:
        """从消息中提取媒体类型、文件名、文件大小。

        支持的媒体类型：
            - video:    视频文件
            - audio:    音频文件
            - document: 文档文件（含其他类型）
            - photo:    照片

        Args:
            message: Pyrogram 消息对象

        Returns:
            (media_type, file_name, file_size) 或 None（无媒体）
        """
        if message.video:
            return (
                "video",
                message.video.file_name or f"video_{message.id}.mp4",
                message.video.file_size or 0,
            )
        if message.audio:
            return (
                "audio",
                message.audio.file_name or f"audio_{message.id}.mp3",
                message.audio.file_size or 0,
            )
        if message.document:
            return (
                "document",
                message.document.file_name or f"document_{message.id}",
                message.document.file_size or 0,
            )
        if message.photo:
            # Pyrogram Photo 对象无 file_name，取最大尺寸的 file_size
            # Photo 对象本身有 file_size 属性表示最大尺寸
            return (
                "photo",
                f"photo_{message.id}.jpg",
                message.photo.file_size or 0,
            )
        return None

    @staticmethod
    def _get_extension(file_name: str) -> str:
        """提取文件扩展名（小写，不含点）。"""
        if not file_name or "." not in file_name:
            return ""
        return file_name.rsplit(".", 1)[-1].lower()

    @staticmethod
    def _get_match_text(message: Message, file_name: str) -> str:
        """获取用于关键词匹配的文本（消息正文 + 文件名）。"""
        parts: list[str] = []
        if message.caption:
            parts.append(message.caption)
        if message.text:
            parts.append(message.text)
        if file_name:
            parts.append(file_name)
        return " ".join(parts)
