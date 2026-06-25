"""配置数据模型定义。

使用 pydantic v2 定义所有配置项的 schema，提供：
    - 类型校验
    - 默认值
    - 环境变量绑定
    - 配置文件加载
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict


class ProxyType(StrEnum):
    """支持的代理协议类型。"""

    SOCKS5 = "socks5"
    SOCKS4 = "socks4"
    HTTP = "http"
    HTTPS = "https"


class MediaFilter(BaseModel):
    """媒体过滤规则。"""

    # 媒体类型白名单，空表示全部接受
    types: list[Literal["audio", "video", "document", "photo"]] = Field(default_factory=list)
    # 文件扩展名白名单（小写，不含点），空表示全部接受
    extensions: list[str] = Field(default_factory=list)
    # 最小文件大小（MB），0 表示不限
    min_size_mb: float = 0.0
    # 最大文件大小（MB），0 表示不限
    max_size_mb: float = 0.0
    # 关键词白名单（匹配消息文本或文件名，任一命中即通过）
    keywords: list[str] = Field(default_factory=list)
    # 关键词黑名单（任一命中即拒绝）
    exclude_keywords: list[str] = Field(default_factory=list)

    @field_validator("extensions")
    @classmethod
    def normalize_extensions(cls, v: list[str]) -> list[str]:
        """统一扩展名为小写且不含前导点。"""
        return [ext.lower().lstrip(".") for ext in v]


class ChannelConfig(BaseModel):
    """单个频道/群组的订阅配置。"""

    # 频道 ID 或用户名（@username）
    id: str | int
    # 自定义别名（用于存储目录命名）
    alias: str = ""
    # 是否启用
    enabled: bool = True
    # 该频道专属过滤规则（为空则使用全局规则）
    filters: MediaFilter | None = None
    # 该频道专属存储子目录（为空则使用 alias 或 id）
    storage_dir: str = ""

    @model_validator(mode="after")
    def ensure_alias(self) -> ChannelConfig:
        """确保 alias 非空，默认使用 id。"""
        if not self.alias:
            self.alias = str(self.id)
        return self


class MonitorSettings(BaseModel):
    """监控模块配置。"""

    # 订阅的频道列表
    channels: list[ChannelConfig] = Field(default_factory=list)
    # 全局过滤规则（频道未配置专属规则时使用）
    default_filters: MediaFilter = Field(default_factory=MediaFilter)
    # 历史消息回溯条数（0 表示仅监听新消息）
    history_limit: int = Field(default=0, ge=0)
    # 会话文件路径（Pyrogram session）
    session_dir: Path = Field(default=Path("data/sessions"))
    # 连接重试次数（首次启动时）
    connect_retries: int = Field(default=3, ge=0, le=10)
    # 连接重试间隔基数（秒，指数退避）
    connect_retry_delay: float = Field(default=5.0, ge=0.1)
    # 是否启用自动重连（连接断开时）
    auto_reconnect: bool = True
    # 重连检查间隔（秒）
    reconnect_interval: int = Field(default=60, ge=10)


class DownloaderSettings(BaseModel):
    """下载模块配置。"""

    # 最大并发下载数
    max_concurrency: int = Field(default=3, ge=1, le=20)
    # 单文件下载超时（秒）
    timeout: int = Field(default=3600, ge=60)
    # 下载重试次数
    max_retries: int = Field(default=3, ge=0, le=10)
    # 重试间隔基数（秒，指数退避）
    retry_base_delay: float = Field(default=5.0, ge=0.1)
    # 分块大小（MB，用于大文件分块下载进度追踪）
    chunk_size_mb: int = Field(default=10, ge=1)
    # 是否启用断点续传
    enable_resume: bool = True


class StorageSettings(BaseModel):
    """存储模块配置。"""

    # 存储根目录
    root_dir: Path = Field(default=Path("data/downloads"))
    # 目录组织方式：by_channel（按频道）/ by_date（按日期）/ by_type（按类型）/ flat（平铺）
    structure: Literal["by_channel", "by_date", "by_type", "flat"] = "by_channel"
    # 文件命名模板（支持变量：{date} {channel} {type} {original_name} {id}）
    filename_template: str = "{original_name}"
    # 是否启用去重
    enable_dedup: bool = True
    # 去重算法
    dedup_algorithm: Literal["sha256", "md5"] = "sha256"
    # 过期清理天数（0 表示不清理）
    retention_days: int = Field(default=0, ge=0)
    # 磁盘空间阈值（GB，低于此值暂停下载并告警）
    min_free_space_gb: float = Field(default=5.0, ge=0)
    # 定时清理 Cron 表达式（默认每天凌晨 3 点）
    cleanup_cron: str = "0 3 * * *"
    # 是否启用孤儿文件清理
    cleanup_orphans: bool = True


class ProxyConfig(BaseModel):
    """代理配置。"""

    enabled: bool = False
    type: ProxyType = ProxyType.SOCKS5
    host: str = "127.0.0.1"
    port: int = Field(default=1080, ge=1, le=65535)
    username: str = ""
    password: str = ""
    # 备用代理列表（主代理故障时切换）
    fallback: list[ProxyConfig] = Field(default_factory=list)
    # 健康检查配置
    health_check_interval: int = Field(default=300, ge=30)
    health_check_timeout: int = Field(default=10, ge=1)
    health_check_target: str = "149.154.167.51"  # Telegram DC2 IP

    @field_validator("fallback")
    @classmethod
    def fallback_no_nested(cls, v: list[ProxyConfig]) -> list[ProxyConfig]:
        """禁止 fallback 嵌套 fallback，避免无限递归。"""
        for item in v:
            if item.fallback:
                raise ValueError("备用代理不支持嵌套备用代理")
        return v


class LoggingSettings(BaseModel):
    """日志配置。"""

    level: Literal["TRACE", "DEBUG", "INFO", "SUCCESS", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    # 日志目录
    dir: Path = Field(default=Path("data/logs"))
    # 日志文件名
    filename: str = "teleharvest.log"
    # 单文件最大大小（MB）
    rotation_mb: int = Field(default=50, ge=1)
    # 保留备份数
    retention: int = Field(default=7, ge=0)
    # 是否压缩归档
    compression: bool = True
    # 控制台输出
    console: bool = True
    # 控制台彩色
    colorize: bool = True


class DatabaseSettings(BaseModel):
    """数据库配置。"""

    # SQLite 数据库文件路径
    url: str = "sqlite+aiosqlite:///data/teleharvest.db"
    # 连接池大小（SQLite 通常为 1）
    pool_size: int = Field(default=5, ge=1)
    # 连接超时（秒）
    pool_timeout: int = Field(default=30, ge=5)
    # 是否打印 SQL（调试用）
    echo: bool = False


class BotSettings(BaseModel):
    """Bot 交互模块配置。

    控制 Bot 通知推送、实时进度上报等行为。
    所有功能依赖 ``AppSettings.bot_token`` 已配置。
    """

    # 推送目标聊天 ID（0 表示不推送；用户私聊 ID 或频道 ID）
    notify_chat_id: int = Field(default=0, description="Bot 推送目标 chat_id")
    # 进度消息编辑最小间隔（秒），避免频繁调用 Telegram API 触发限流
    progress_interval: float = Field(default=2.0, ge=0.5, le=60.0)
    # 进度文本刷新的百分比步长（达到此步长时强制刷新一次）
    progress_percent_step: int = Field(default=5, ge=1, le=50)
    # 下载完成时是否推送通知卡片
    notify_on_complete: bool = True
    # 下载失败时是否推送错误通知
    notify_on_error: bool = True
    # 是否在下载开始时创建实时进度消息
    enable_progress_message: bool = True


class AppSettings(BaseSettings):
    """应用全局配置根模型。

    配置优先级（从高到低）：
        1. 环境变量（前缀 TELEHARVEST_，支持嵌套，如 TELEHARVEST_PROXY__HOST）
        2. .env 文件
        3. config.yaml 配置文件
        4. 代码默认值
    """

    model_config = SettingsConfigDict(
        env_prefix="TELEHARVEST_",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """自定义配置源优先级：环境变量 > .env > YAML(init) > 默认值。

        默认顺序中 init_settings 优先级最高，会导致 YAML 的相对路径
        覆盖 docker-compose 环境变量中的绝对路径。此处调整顺序修复该问题。
        """
        return (env_settings, dotenv_settings, init_settings, file_secret_settings)

    # Telegram API 凭据（敏感，必须通过环境变量注入）
    api_id: int = Field(default=0, description="Telegram API ID")
    api_hash: str = Field(default="", description="Telegram API Hash")
    bot_token: str = Field(default="", description="Bot Token（可选，用户账号登录时留空）")

    # 各模块配置
    monitor: MonitorSettings = Field(default_factory=MonitorSettings)
    downloader: DownloaderSettings = Field(default_factory=DownloaderSettings)
    storage: StorageSettings = Field(default_factory=StorageSettings)
    proxy: ProxyConfig = Field(default_factory=ProxyConfig)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    bot: BotSettings = Field(default_factory=BotSettings)

    @model_validator(mode="after")
    def validate_credentials(self) -> AppSettings:
        """校验 Telegram 凭据必须提供。"""
        if not self.api_id or not self.api_hash:
            raise ValueError(
                "必须提供 Telegram API 凭据：设置 TELEHARVEST_API_ID 和 TELEHARVEST_API_HASH "
                "环境变量，或在 .env 文件中配置。申请地址：https://my.telegram.org"
            )
        return self
