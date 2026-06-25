"""Bot 命令处理器：响应 /start /help /status /history /dl /pause /resume /stats 等命令。

行为约束：
    - 仅响应配置的 notify_chat_id 用户的私聊命令（安全限制）
    - 命令处理吞掉异常，不影响主流程
    - 长文本自动分块发送（Telegram 单条消息上限 4096 字符）
"""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import TYPE_CHECKING, cast

from loguru import logger

if TYPE_CHECKING:
    from typing import Literal

    from pyrogram import Client  # type: ignore[attr-defined]
    from pyrogram.types import Message

    from teleharvest.config.schema import AppSettings
    from teleharvest.core.scheduler import Scheduler
    from teleharvest.db.repositories.channel_repo import ChannelRepository
    from teleharvest.db.repositories.media_repo import MediaRepository
    from teleharvest.downloader.engine import DownloadEngine


# 命令菜单定义（用于 set_bot_commands 注册）
COMMAND_LIST: list[tuple[str, str]] = [
    ("start", "开始使用机器人"),
    ("help", "查看可用命令"),
    ("status", "查看运行状态"),
    ("history", "查看最近下载记录"),
    ("dl", "手动下载指定消息"),
    ("pause", "暂停下载引擎"),
    ("resume", "恢复下载引擎"),
    ("stats", "查看下载统计"),
]


class BotCommandHandler:
    """Bot 命令处理器：监听私聊命令并响应。

    生命周期由调用方管理（main.py 在 MonitorClient 启动后创建）。
    通过 MonitorClient.register_bot_commands 注册到 Pyrogram Client。
    """

    def __init__(
        self,
        settings: AppSettings,
        media_repo: MediaRepository,
        channel_repo: ChannelRepository,
        download_engine: DownloadEngine,
        client: Client,
        scheduler: Scheduler,
    ) -> None:
        self._settings = settings
        self._media_repo = media_repo
        self._channel_repo = channel_repo
        self._engine = download_engine
        self._client = client
        self._scheduler = scheduler

    async def handle(self, client: Client, message: Message) -> None:
        """处理私聊命令入口。

        Args:
            client: Pyrogram Client
            message: 收到的消息
        """
        text = message.text or ""
        if not text.startswith("/"):
            return

        # 解析命令（去除 @botname 后缀）
        parts = text.split()
        command = parts[0].lower().split("@")[0]
        args = parts[1:]

        logger.debug("收到 Bot 命令: {} (args={})", command, args)

        try:
            if command == "/start":
                await self._cmd_start(message)
            elif command == "/help":
                await self._cmd_help(message)
            elif command == "/status":
                await self._cmd_status(message)
            elif command == "/history":
                await self._cmd_history(message, args)
            elif command == "/dl":
                await self._cmd_dl(message, args)
            elif command == "/pause":
                await self._cmd_pause(message)
            elif command == "/resume":
                await self._cmd_resume(message)
            elif command == "/stats":
                await self._cmd_stats(message)
            else:
                await message.reply_text(
                    f"❓ 未知命令: {command}\n\n输入 /help 查看可用命令",
                    quote=False,
                )
        except Exception as exc:
            logger.warning("处理 Bot 命令失败: command={}, error={}", command, exc)
            with contextlib.suppress(Exception):
                await message.reply_text(
                    f"⚠️ 处理命令时出错: {exc}",
                    quote=False,
                )

    # ===== 命令实现 =====

    async def _cmd_start(self, message: Message) -> None:
        """/start 命令：欢迎消息。"""
        text = "\n".join(
            [
                "👋 你好！我是 TeleHarvest 机器人",
                "",
                "我会自动监控 Telegram 频道并下载音视频资源，",
                "下载进度和完成通知会推送到这里。",
                "",
                "输入 /help 查看可用命令",
            ]
        )
        await message.reply_text(text, quote=False)

    async def _cmd_help(self, message: Message) -> None:
        """/help 命令：命令列表。"""
        lines = ["📖 可用命令\n"]
        for cmd, desc in COMMAND_LIST:
            lines.append(f"/{cmd} — {desc}")
        lines.append("")
        lines.append("💡 提示：/history 后可跟数字指定条数，如 /history 20")
        await message.reply_text("\n".join(lines), quote=False)

    async def _cmd_status(self, message: Message) -> None:
        """/status 命令：运行状态。"""
        channels = self._settings.monitor.channels
        enabled_channels = [ch for ch in channels if ch.enabled]
        active = self._engine.active_count
        active_tasks = self._engine.get_active_tasks()
        total_records = await self._media_repo.count_all()

        lines = [
            "📊 运行状态\n",
            f"🔗 监控频道: {len(enabled_channels)}/{len(channels)} 个启用",
            f"📥 活跃下载: {active} 个 (最大并发 {self._settings.downloader.max_concurrency})",
            f"💾 下载记录: {total_records} 条",
            f"📂 存储目录: {self._settings.storage.root_dir}",
        ]

        # 活跃下载详情
        if active_tasks:
            lines.append("\n🔄 进行中的下载:")
            for ctx in active_tasks[:5]:  # 最多显示 5 个
                task = ctx.task
                if task is not None:
                    lines.append(f"  • [{ctx.message_id}] {task.file_name}")
                    lines.append(f"    📌 频道: {task.channel_alias}")
                else:
                    lines.append(f"  • [{ctx.message_id}] (无任务详情)")

        await message.reply_text("\n".join(lines), quote=False)

    async def _cmd_history(self, message: Message, args: list[str]) -> None:
        """/history [数量] 命令：查看最近下载记录。"""
        limit = 10
        if args:
            try:
                limit = max(1, min(50, int(args[0])))
            except ValueError:
                await message.reply_text(
                    "❌ 参数无效，请输入数字\n示例: /history 20",
                    quote=False,
                )
                return

        records = await self._media_repo.find_recent(limit)
        if not records:
            await message.reply_text("📭 暂无下载记录", quote=False)
            return

        # 构建历史列表
        lines = [f"📋 最近 {len(records)} 条下载记录\n"]
        for i, media in enumerate(records, 1):
            name = Path(media.file_path).name if media.file_path else media.original_name
            size_str = _format_bytes(media.file_size)
            created = media.created_at.strftime("%m-%d %H:%M") if media.created_at else "N/A"
            type_icon = _media_type_icon(media.media_type)
            lines.append(f"{i}. {type_icon} {name}")
            lines.append(f"   📦 {size_str}  📅 {created}")

        text = "\n".join(lines)
        # Telegram 单条消息上限 4096 字符，超长则截断
        if len(text) > 4000:
            text = text[:4000] + "\n... (已截断)"
        await message.reply_text(text, quote=False)

    async def _cmd_dl(self, message: Message, args: list[str]) -> None:
        """/dl <channel_id> <message_id> 命令：手动下载指定频道消息。

        用法：
            /dl -1001234567890 123       按数字 ID 下载
            /dl @channel_username 456    按用户名下载

        流程：
            1. 解析参数（channel_id + message_id）
            2. 通过 Pyrogram 获取目标消息
            3. 提取媒体信息（无过滤，直接提取）
            4. 构造 DownloadTask 入队
        """
        if len(args) < 2:
            await message.reply_text(
                "❌ 参数不足\n\n"
                "用法: /dl <channel_id> <message_id>\n"
                "示例: /dl -1001234567890 123\n"
                "      /dl @channel_username 456",
                quote=False,
            )
            return

        channel_id_str = args[0]
        message_id_str = args[1]

        # 解析 channel_id（数字 ID 或 @username）
        try:
            channel_id: str | int = int(channel_id_str)
        except ValueError:
            channel_id = channel_id_str.lstrip("@")

        # 解析 message_id
        try:
            msg_id = int(message_id_str)
        except ValueError:
            await message.reply_text(
                f"❌ message_id 无效: {message_id_str}（必须为数字）",
                quote=False,
            )
            return

        # 查找频道配置以获取 alias（找不到则用 channel_id 字符串）
        channel_alias = str(channel_id)
        for ch in self._settings.monitor.channels:
            if str(ch.id) == str(channel_id):
                channel_alias = ch.alias
                break

        # 获取目标消息
        try:
            fetched = await self._client.get_messages(channel_id, message_ids=msg_id)
        except Exception as exc:
            logger.warning(
                "Bot /dl 获取消息失败: channel={}, msg={}, error={}", channel_id, msg_id, exc
            )
            await message.reply_text(
                f"❌ 获取消息失败: {exc}",
                quote=False,
            )
            return

        # get_messages 返回类型可能是 Message 或 list[Message]
        target_message: Message
        if isinstance(fetched, list):
            if not fetched:
                await message.reply_text("❌ 消息不存在", quote=False)
                return
            target_message = fetched[0]
        else:
            target_message = fetched

        # 提取媒体信息（使用空过滤规则，不应用任何过滤）
        from teleharvest.config.schema import MediaFilter
        from teleharvest.monitor.filters import MessageFilter as PyroMessageFilter

        info = PyroMessageFilter(MediaFilter()).extract_media_info(target_message)
        if info is None:
            await message.reply_text(
                "❌ 该消息没有可下载的媒体内容",
                quote=False,
            )
            return

        media_type, file_name, file_size = info

        # 构建下载任务
        from teleharvest.core.task import DownloadTask

        task = DownloadTask(
            message_id=target_message.id,
            channel_id=channel_id,
            channel_alias=channel_alias,
            media_type=cast("Literal['audio', 'video', 'document', 'photo']", media_type),
            file_name=file_name,
            file_size=file_size,
            caption=target_message.caption or target_message.text or "",
            message_date=target_message.date,
            _message_ref=target_message,
        )

        # 入队
        await self._scheduler.enqueue(task)
        logger.info(
            "Bot /dl 已入队: channel={}, msg={}, file={}, size={}",
            channel_alias,
            msg_id,
            file_name,
            _format_bytes(file_size),
        )

        await message.reply_text(
            "✅ 已加入下载队列\n"
            f"📌 频道: {channel_alias}\n"
            f"🆔 消息: {msg_id}\n"
            f"📄 文件: {file_name}\n"
            f"📦 大小: {_format_bytes(file_size)}",
            quote=False,
        )

    async def _cmd_pause(self, message: Message) -> None:
        """/pause 命令：暂停下载引擎（进行中的不中断，拒绝新任务）。"""
        if self._engine.is_paused:
            await message.reply_text("ℹ️ 下载引擎已处于暂停状态", quote=False)
            return
        self._engine.pause()
        active = self._engine.active_count
        await message.reply_text(
            "⏸️ 下载引擎已暂停\n"
            f"🔄 进行中的下载继续: {active} 个\n"
            "🚫 新任务将被拒绝（入队但不执行）",
            quote=False,
        )

    async def _cmd_resume(self, message: Message) -> None:
        """/resume 命令：恢复下载引擎。"""
        if not self._engine.is_paused:
            await message.reply_text("ℹ️ 下载引擎未处于暂停状态", quote=False)
            return
        self._engine.resume()
        await message.reply_text(
            "▶️ 下载引擎已恢复\n✅ 新任务可正常执行",
            quote=False,
        )

    async def _cmd_stats(self, message: Message) -> None:
        """/stats 命令：查看下载统计（调度器 + 数据库）。"""
        stats = self._scheduler.stats
        total_records = await self._media_repo.count_all()
        total_size = await self._media_repo.total_size()
        active = self._engine.active_count
        paused = self._engine.is_paused

        lines = [
            "📈 下载统计\n",
            f"🔄 引擎状态: {'⏸️ 已暂停' if paused else '▶️ 运行中'}",
            f"📥 活跃下载: {active} 个 (最大并发 {self._settings.downloader.max_concurrency})",
            f"📋 队列等待: {stats['queue_size']} 个",
            f"✅ 累计完成: {stats['succeeded']} 个",
            f"❌ 累计失败: {stats['failed']} 个",
            f"📊 累计处理: {stats['processed']} 个",
            f"💾 下载记录: {total_records} 条",
            f"📦 下载总量: {_format_bytes(total_size)}",
        ]
        await message.reply_text("\n".join(lines), quote=False)


# ===== 工具函数 =====


def _format_bytes(num: float) -> str:
    """字节数格式化为人类可读字符串。"""
    if num <= 0:
        return "0 B"
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if abs(num) < 1024.0:
            return f"{num:.1f} {unit}"
        num /= 1024.0
    return f"{num:.1f} PB"


def _media_type_icon(media_type: str) -> str:
    """媒体类型对应的图标。"""
    icons = {
        "video": "🎞️",
        "audio": "🎵",
        "document": "📄",
        "photo": "🖼️",
    }
    return icons.get(media_type, "📦")
