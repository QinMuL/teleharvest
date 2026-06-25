"""TeleHarvest —— 从 Telegram 频道和群组中监控并下载音视频资源的服务。

包结构：
    - core:       核心调度与生命周期管理
    - monitor:    Telegram 消息监控
    - downloader: 媒体下载引擎
    - storage:    文件存储与去重
    - config:     配置加载与校验
    - proxy:      代理连接管理
    - db:         数据访问层
    - api:        HTTP 健康检查端点
    - utils:      通用工具函数
"""

__version__ = "0.1.0"
__all__ = ["__version__"]
