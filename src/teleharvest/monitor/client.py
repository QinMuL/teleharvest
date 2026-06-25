"""Pyrogram 客户端封装：连接管理、代理注入、会话持久化、频道订阅。

职责：
    1. 根据 AppSettings 创建并配置 Pyrogram Client
    2. 注入代理配置（通过 ProxyManager）
    3. 管理客户端生命周期（start/stop）
    4. 订阅配置的频道，注册消息处理器
    5. 支持历史消息回溯（history_limit > 0 时）
    6. 连接重试与自动重连（连接断开时）

错误处理策略：
    - 首次启动：指数退避重试 connect_retries 次
    - 运行中：看门狗定期检查连接状态，断开时自动重连
    - 代理故障：通知 ProxyManager 标记不可用并触发故障转移
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from loguru import logger

from teleharvest.monitor.handler import MessageHandler
from teleharvest.utils.retry import is_retryable

if TYPE_CHECKING:
    from pyrogram import Client  # type: ignore[attr-defined]

    from teleharvest.config.schema import AppSettings, ChannelConfig, MonitorSettings
    from teleharvest.core.scheduler import Scheduler
    from teleharvest.db.repositories.channel_repo import ChannelRepository
    from teleharvest.proxy.manager import ProxyManager


class MonitorClient:
    """Telegram 监控客户端，封装 Pyrogram Client。"""

    def __init__(
        self,
        settings: AppSettings,
        scheduler: Scheduler,
        proxy_manager: ProxyManager | None = None,
        channel_repo: ChannelRepository | None = None,
    ) -> None:
        self._settings = settings
        self._scheduler = scheduler
        self._proxy_manager = proxy_manager
        self._channel_repo = channel_repo
        self._client: Client | None = None
        self._started = False
        self._handlers: dict[str, MessageHandler] = {}  # channel_id -> handler
        self._watchdog_task: asyncio.Task[None] | None = None
        self._reconnecting = False

    async def start(self) -> None:
        """启动 Telegram 客户端（带重试）。

        首次使用用户账号登录时，需先运行 ``python scripts/login.py`` 生成会话文件。
        使用 Bot Token 时无需此步骤。

        Raises:
            ConnectionError: 重试耗尽后仍无法连接
        """
        monitor: MonitorSettings = self._settings.monitor

        await self._connect_with_retry()

        # 启动自动重连看门狗
        if monitor.auto_reconnect:
            self._watchdog_task = asyncio.create_task(self._reconnect_watchdog())
            logger.info("自动重连看门狗已启动: 间隔={}s", monitor.reconnect_interval)

    async def _connect_with_retry(self) -> None:
        """带指数退避重试的连接逻辑。"""
        monitor: MonitorSettings = self._settings.monitor
        max_retries = monitor.connect_retries
        base_delay = monitor.connect_retry_delay

        last_exc: Exception | None = None

        for attempt in range(max_retries + 1):
            try:
                await self._do_connect()
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                last_exc = exc

                if not is_retryable(exc) or attempt >= max_retries:
                    logger.error(
                        "Telegram 连接失败，不再重试: attempt={}/{}, error={}",
                        attempt + 1,
                        max_retries + 1,
                        exc,
                    )
                    raise

                delay = min(base_delay * (2**attempt), 60.0)
                logger.warning(
                    "Telegram 连接失败，{:.1f}s 后重试: attempt={}/{}, error={}",
                    delay,
                    attempt + 1,
                    max_retries + 1,
                    exc,
                )

                # 代理故障时通知 ProxyManager
                if self._proxy_manager is not None and is_retryable(exc):
                    self._proxy_manager.mark_unhealthy()

                await asyncio.sleep(delay)

        if last_exc:
            raise ConnectionError(f"连接重试耗尽: {last_exc}") from last_exc

    async def _do_connect(self) -> None:
        """执行实际的 Pyrogram Client 创建与连接。"""
        from pyrogram import Client  # type: ignore[attr-defined]

        monitor: MonitorSettings = self._settings.monitor

        # 构建代理参数
        proxy_arg = None
        if self._proxy_manager is not None:
            proxy_arg = self._proxy_manager.to_pyrogram_proxy()
            if proxy_arg:
                logger.info(
                    "Telegram 客户端使用代理: type={}, host={}, port={}",
                    proxy_arg[0],
                    proxy_arg[1],
                    proxy_arg[2],
                )

        # 确保会话目录存在（转为绝对路径，避免 Pyrogram 解析到包安装目录）
        session_dir = monitor.session_dir.resolve()
        session_dir.mkdir(parents=True, exist_ok=True)
        session_path = session_dir / "teleharvest"

        # 会话文件检测（用户账号模式）
        if not self._settings.bot_token:
            session_file = session_path.parent / f"{session_path.name}.session"
            if not session_file.exists():
                logger.warning("未检测到会话文件: {}", session_file)
                logger.warning("首次使用用户账号需先运行登录脚本: python scripts/login.py")
                logger.warning("或将已生成的 .session 文件复制到: {}", session_file)
                logger.warning("尝试启动（交互式终端可完成登录）...")

        self._client = Client(
            name=str(session_path),
            api_id=self._settings.api_id,
            api_hash=self._settings.api_hash,
            bot_token=self._settings.bot_token or None,  # type: ignore[arg-type]
            proxy=proxy_arg,  # type: ignore[arg-type]
            in_memory=False,
        )

        await self._client.start()
        self._started = True
        me = await self._client.get_me()
        logger.info(
            "Telegram 客户端已连接: id={}, name={}",
            me.id,
            me.first_name or me.username or "unknown",
        )

        # 清除之前项目遗留的 Bot 命令注册（覆盖所有 scope）
        if self._settings.bot_token:
            await self._purge_legacy_bot_commands()

    async def _purge_legacy_bot_commands(self) -> None:
        """清除之前项目遗留的 Bot 命令注册（覆盖所有 scope）。

        Telegram Bot 命令按 scope 维度注册，无参的 ``delete_bot_commands()``
        只能清除默认 scope，其他 scope（私聊/群组/管理员/特定 chat）的遗留命令会保留。
        此方法逐个 scope 清除，确保完全清空，包括针对 notify_chat_id 的特定 chat scope。
        """
        from pyrogram.types import (
            BotCommandScopeAllChatAdministrators,
            BotCommandScopeAllGroupChats,
            BotCommandScopeAllPrivateChats,
            BotCommandScopeChat,
            BotCommandScopeDefault,
        )

        # 构造 scope 对象：Pyrogram 类型未注解，忽略 no-untyped-call
        scopes: list[tuple[str, object]] = [
            ("default", BotCommandScopeDefault()),  # type: ignore[no-untyped-call]
            ("all_private_chats", BotCommandScopeAllPrivateChats()),  # type: ignore[no-untyped-call]
            ("all_group_chats", BotCommandScopeAllGroupChats()),  # type: ignore[no-untyped-call]
            ("all_chat_administrators", BotCommandScopeAllChatAdministrators()),  # type: ignore[no-untyped-call]
        ]

        # 针对配置的 notify_chat_id 清除特定 chat scope
        # 之前项目可能用 BotCommandScopeChat 给特定用户注册过命令
        notify_chat_id = self._settings.bot.notify_chat_id
        if notify_chat_id:
            try:
                scopes.append(
                    (
                        f"chat:{notify_chat_id}",
                        BotCommandScopeChat(chat_id=notify_chat_id),
                    )
                )
            except Exception as exc:
                logger.debug("构造 BotCommandScopeChat 失败: {}", exc)

        client = self._client
        if client is None:
            return

        cleared = 0
        for name, scope in scopes:
            try:
                ok = await client.delete_bot_commands(scope=scope)  # type: ignore[arg-type]
                if ok:
                    cleared += 1
                    logger.debug("已清除 Bot 命令 scope: {}", name)
            except Exception as exc:
                logger.debug("清除 Bot 命令 scope {} 失败: {}", name, exc)

        logger.info("已清除 Bot 命令注册: {} 个 scope", cleared)

    async def _reconnect_watchdog(self) -> None:
        """自动重连看门狗：定期检查连接状态，断开时重连。"""
        monitor: MonitorSettings = self._settings.monitor

        while self._started:
            try:
                await asyncio.sleep(monitor.reconnect_interval)

                if self._reconnecting or not self._started:
                    continue

                # 检查连接是否存活
                if not self._is_connected():
                    logger.warning("检测到 Telegram 连接断开，尝试重连...")
                    await self._reconnect()

            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.exception("重连看门狗异常: {}", exc)

    def _is_connected(self) -> bool:
        """检查 Pyrogram 客户端连接是否存活。"""
        if self._client is None or not self._started:
            return False
        try:
            # Pyrogram Client 内部维护 is_connected 状态
            return bool(getattr(self._client, "is_connected", False))
        except Exception:
            return False

    async def _reconnect(self) -> None:
        """执行重连流程。"""
        if self._reconnecting:
            return
        self._reconnecting = True

        try:
            # 停止旧客户端
            if self._client is not None:
                with _SuppressAll():
                    await self._client.stop()
                self._client = None
            self._started = False

            # 代理故障转移
            if self._proxy_manager is not None:
                self._proxy_manager.mark_unhealthy()

            # 重新连接
            await self._connect_with_retry()

            # 重新订阅频道
            if self._handlers:
                logger.info("重连后重新订阅 {} 个频道...", len(self._handlers))
                self._handlers.clear()
                await self.subscribe_channels()

            logger.info("Telegram 重连成功")
        except Exception as exc:
            logger.error("Telegram 重连失败: {}", exc)
            # 代理故障转移
            if self._proxy_manager is not None:
                await self._proxy_manager.failover()
        finally:
            self._reconnecting = False

    async def stop(self) -> None:
        """停止客户端。"""
        # 停止看门狗
        if self._watchdog_task:
            self._watchdog_task.cancel()
            with _SuppressAll():
                await self._watchdog_task
            self._watchdog_task = None

        if self._client and self._started:
            with _SuppressAll():
                await self._client.stop()
            self._started = False
            logger.info("Telegram 客户端已断开")

    @property
    def client(self) -> Client:
        """获取底层 Pyrogram Client 实例（启动后可用）。

        Raises:
            RuntimeError: 客户端未启动
        """
        if self._client is None or not self._started:
            raise RuntimeError("客户端未启动，请先调用 start()")
        return self._client

    async def register_bot_command_handler(
        self,
        handler: Any,
        chat_id: int,
        commands: list[tuple[str, str]],
    ) -> None:
        """注册 Bot 命令处理器并设置命令菜单。

        Args:
            handler: BotCommandHandler 实例（有 async handle(client, message) 方法）
            chat_id: 授权用户的 chat_id（仅响应该用户的私聊命令）
            commands: 命令菜单列表 [(command, description), ...]
        """
        if self._client is None:
            logger.error("客户端未启动，无法注册命令处理器")
            return

        from pyrogram import filters
        from pyrogram.handlers import MessageHandler as PyroHandler  # type: ignore[attr-defined]
        from pyrogram.types import BotCommand, BotCommandScopeChat

        # 注册消息处理器：仅监听授权用户的私聊命令
        command_names = [cmd for cmd, _ in commands]
        self._client.add_handler(
            PyroHandler(
                handler.handle,
                filters.private & filters.user(chat_id) & filters.command(command_names),
            ),
        )

        # 设置 Bot 命令菜单（仅对该用户可见）
        bot_commands = [
            BotCommand(command=cmd, description=desc)
            for cmd, desc in commands
        ]
        try:
            await self._client.set_bot_commands(
                bot_commands,
                scope=BotCommandScopeChat(chat_id=chat_id),
            )
            logger.info(
                "已注册 Bot 命令处理器: {} 个命令, chat_id={}",
                len(commands),
                chat_id,
            )
        except Exception as exc:
            logger.warning("设置 Bot 命令菜单失败: {}", exc)

    async def subscribe_channels(self) -> None:
        """订阅配置的频道列表。

        流程：
            1. 遍历配置中的频道，为每个启用频道创建 MessageHandler
            2. 解析频道实体（将用户名转为内部 ID）
            3. 注册 Pyrogram 消息处理器（按 chat_id 过滤）
            4. 若 history_limit > 0，回溯历史消息
        """
        channels = self._settings.monitor.channels
        enabled = [ch for ch in channels if ch.enabled]

        logger.info("开始订阅频道: 共 {} 个（启用 {} 个）", len(channels), len(enabled))

        for channel in enabled:
            await self._subscribe_one(channel)

        logger.info("频道订阅完成，共注册 {} 个处理器", len(self._handlers))

    async def _subscribe_one(self, channel: ChannelConfig) -> None:
        """订阅单个频道。

        Args:
            channel: 频道配置
        """
        # 创建消息处理器
        handler = MessageHandler(
            channel=channel,
            default_filters=self._settings.monitor.default_filters,
            scheduler=self._scheduler,
            channel_repo=self._channel_repo,
        )

        # 解析频道实体，获取 chat_id 用于注册处理器
        try:
            chat = await self._resolve_chat(channel.id)
            chat_id = chat.id
            chat_title = chat.title or chat.first_name or chat.username or str(chat_id)
            logger.info(
                "已订阅频道: alias={}, id={}, title={}",
                channel.alias,
                channel.id,
                chat_title,
            )
        except Exception as exc:
            hint = ""
            if "Peer id invalid" in str(exc) or "peer" in str(exc).lower():
                hint = "（提示：Bot 新会话无 peer 缓存，请在群组中发送任意消息后重启）"
            logger.error(
                "订阅频道失败: alias={}, id={}, error={}{}",
                channel.alias,
                channel.id,
                exc,
                hint,
            )
            return

        # 注册 Pyrogram 消息处理器（仅监听该频道的新消息）
        if self._client is None:
            logger.error("客户端未启动，无法注册处理器: {}", channel.alias)
            return

        from pyrogram import filters
        from pyrogram.handlers import MessageHandler as PyroHandler  # type: ignore[attr-defined]

        self._client.add_handler(
            PyroHandler(handler.handle, filters.chat(chat_id)),
        )
        self._handlers[str(channel.id)] = handler

        # 历史消息回溯
        if self._settings.monitor.history_limit > 0:
            await self._fetch_history(channel, chat_id, handler)

    async def _resolve_chat(self, channel_id: str | int) -> Any:
        """解析频道 ID 或用户名为 Chat 对象。

        Args:
            channel_id: 频道 ID（数字）或用户名（@username）

        Returns:
            Pyrogram Chat 对象
        """
        client = self.client  # 会抛 RuntimeError 如果未启动

        # 用户名（字符串且以 @ 开头）直接使用
        if isinstance(channel_id, str) and channel_id.startswith("@"):
            return await client.get_chat(channel_id)

        # 数字 ID：Pyrogram 需要带 -100 前缀的完整 ID
        if isinstance(channel_id, int):
            return await client.get_chat(channel_id)

        # 字符串数字：尝试转换
        try:
            numeric_id = int(channel_id)
            return await client.get_chat(numeric_id)
        except ValueError:
            # 非数字字符串，当作用户名处理
            username = channel_id if channel_id.startswith("@") else f"@{channel_id}"
            return await client.get_chat(username)

    async def _fetch_history(
        self,
        channel: ChannelConfig,
        chat_id: int,
        handler: MessageHandler,
    ) -> None:
        """回溯频道历史消息。

        Args:
            channel: 频道配置
            chat_id: 频道数字 ID
            handler: 消息处理器
        """
        client = self.client
        limit = self._settings.monitor.history_limit

        # 查询数据库中最后处理的消息 ID，从其后开始回溯
        last_id = 0
        if self._channel_repo is not None:
            last_id = await self._channel_repo.get_last_message_id(channel.id)

        logger.info(
            "回溯频道 {} 历史消息: limit={}, last_id={}",
            channel.alias,
            limit,
            last_id,
        )

        try:
            count = 0
            history = client.get_chat_history(
                chat_id,
                limit=limit,
                offset_id=last_id if last_id > 0 else 0,
            )
            if history is None:
                logger.warning("频道 {} 无法获取历史消息", channel.alias)
                return

            async for message in history:
                # 手动调用处理器（历史消息不触发 Pyrogram 回调）
                await handler.handle(client, message)
                count += 1

            if count > 0:
                logger.info(
                    "频道 {} 历史回溯完成: 处理 {} 条消息",
                    channel.alias,
                    count,
                )
        except Exception as exc:
            logger.warning(
                "频道 {} 历史回溯失败: {}",
                channel.alias,
                exc,
            )

    def get_stats(self) -> dict[str, dict[str, int]]:
        """获取所有频道的处理统计。"""
        return {channel_id: handler.stats for channel_id, handler in self._handlers.items()}


class _SuppressAll:
    """抑制所有异常的上下文管理器（用于优雅关闭时的清理）。

    比 contextlib.suppress(Exception) 更明确地表达意图。
    """

    def __enter__(self) -> _SuppressAll:
        return self

    def __exit__(self, *exc_info: object) -> bool:
        return True  # 抑制所有异常
