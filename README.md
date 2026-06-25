# TeleHarvest

> 从 Telegram 频道和群组中监控并下载音频与视频资源的 Docker 化服务

[![CI](https://github.com/qinmul/teleharvest/actions/workflows/ci.yml/badge.svg)](https://github.com/qinmul/teleharvest/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

## 简介

TeleHarvest 是一个基于 Docker 的 Telegram 媒体资源监控下载器，支持：

- 订阅多个 Telegram 频道/群组，实时监听新消息
- 按媒体类型、扩展名、大小、关键词灵活过滤
- 并发下载音视频资源，支持断点续传与失败重试
- 文件去重（SHA256 哈希校验），避免重复下载
- 多种目录组织策略（按频道/日期/类型）
- HTTP/HTTPS/SOCKS5 代理支持，含故障转移与健康检查
- 自动重连看门狗，连接断开时指数退避重试
- 过期文件清理与孤儿文件检测（APScheduler 定时任务）
- **Bot 交互**：实时下载进度推送、完成通知卡片、命令菜单、手动下载、远程控制
- Docker 一键部署，数据持久化，非 root 用户运行

### Bot 交互功能

配置 Bot Token 后，TeleHarvest 通过 Bot 私聊提供完整的交互体验：

- **实时下载进度**：下载开始/进行中/完成/失败全程推送，含进度条、速度、剩余时间
- **完成通知卡片**：下载完成后推送格式化卡片（标题、质量、大小、日期、频道、源链接、简介）
- **命令菜单**：在 Bot 私聊中点击 `/` 即可查看并使用全部命令
- **手动下载**：通过 `/dl` 命令手动指定频道消息触发下载
- **远程控制**：暂停/恢复下载引擎、查看统计信息

## 快速开始

### 1. 获取 Telegram API 凭据

访问 https://my.telegram.org ，创建应用获取 `api_id` 和 `api_hash`。

### 2. 克隆并配置

```bash
git clone https://github.com/qinmul/teleharvest.git
cd teleharvest

# 复制配置文件
cp config/config.example.yaml config/config.yaml
cp config/.env.example .env

# 编辑 .env 填入凭据
vim .env
# 编辑 config.yaml 配置订阅频道
vim config/config.yaml
```

### 3. Docker 部署（推荐）

```bash
cd docker
docker compose up -d
```

查看日志：

```bash
docker compose logs -f teleharvest
```

停止服务：

```bash
docker compose down
```

### 4. 用户账号登录（可选）

使用用户账号（非 Bot Token）时，需先生成会话文件：

```bash
# 本地执行交互式登录
pip install -e .
python scripts/login.py

# 生成的会话文件位于 data/sessions/teleharvest.session
# Docker 启动时会自动挂载使用
```

使用 Bot Token 时无需此步骤，直接在 `.env` 中配置 `TELEHARVEST_BOT_TOKEN` 即可。

### 4.1 Bot 交互配置（可选）

启用 Bot 交互功能需配置以下项：

```bash
# .env 文件
TELEHARVEST_BOT_TOKEN=123456:ABC-DEF...           # 从 @BotFather 获取
TELEHARVEST_BOT__NOTIFY_CHAT_ID=5406565010          # 你的 Telegram user_id（不是 Bot ID）
```

> **获取你的 user_id**：在 Telegram 中搜索 `@userinfobot` 发送任意消息即可获取。
>
> **注意**：`notify_chat_id` 必须是你的用户 ID（数字），Bot 无法主动给自己发消息。
> 配置后需先在 Bot 私聊中发送 `/start`，Bot 才能向你推送通知。

### 5. 本地开发

```bash
bash scripts/setup_dev.sh
source .venv/bin/activate  # Windows: .venv\Scripts\activate
python -m teleharvest
```

## 配置说明

配置优先级（从高到低）：

1. 环境变量（前缀 `TELEHARVEST_`，嵌套用 `__` 分隔）
2. `.env` 文件
3. `config/config.yaml`
4. 代码默认值

### 核心配置项

| 配置项 | 环境变量 | 默认值 | 说明 |
|--------|----------|--------|------|
| API ID | `TELEHARVEST_API_ID` | — | Telegram API ID（必填） |
| API Hash | `TELEHARVEST_API_HASH` | — | Telegram API Hash（必填） |
| Bot Token | `TELEHARVEST_BOT_TOKEN` | — | Bot Token（启用 Bot 交互需配置） |
| 通知目标 | `TELEHARVEST_BOT__NOTIFY_CHAT_ID` | 0 | 接收通知的 user_id（0=禁用推送） |
| 进度间隔 | `TELEHARVEST_BOT__PROGRESS_INTERVAL` | 2.0 | 进度消息更新间隔（秒） |
| 进度消息 | `TELEHARVEST_BOT__ENABLE_PROGRESS_MESSAGE` | true | 是否推送实时下载进度 |
| 完成通知 | `TELEHARVEST_BOT__NOTIFY_ON_COMPLETE` | true | 下载完成时推送通知卡片 |
| 错误通知 | `TELEHARVEST_BOT__NOTIFY_ON_ERROR` | true | 下载失败时推送错误通知 |
| 下载并发 | `TELEHARVEST_DOWNLOADER__MAX_CONCURRENCY` | 3 | 最大并发下载数 |
| 存储目录 | `TELEHARVEST_STORAGE__ROOT_DIR` | data/downloads | 下载文件根目录 |
| 代理启用 | `TELEHARVEST_PROXY__ENABLED` | false | 是否启用代理 |
| 日志级别 | `TELEHARVEST_LOGGING__LEVEL` | INFO | TRACE/DEBUG/INFO/WARNING/ERROR |

详细配置项见 [config/config.example.yaml](config/config.example.yaml)。

## Bot 命令

配置 Bot Token 和 `notify_chat_id` 后，在 Bot 私聊中发送命令即可操作。点击对话框输入框旁的 `/` 按钮可查看命令菜单。

### 基础命令

| 命令 | 说明 |
|------|------|
| `/start` | 开始使用，显示欢迎信息 |
| `/help` | 查看所有可用命令 |
| `/status` | 查看运行状态（频道数、活跃下载、存储信息） |
| `/history [N]` | 查看最近 N 条下载记录（默认 10，最大 50） |

### 手动下载

```
/dl <channel_id> <message_id>
```

手动下载指定频道的指定消息，支持数字 ID 和 @username 两种格式：

```
/dl -1001234567890 123       # 按超级群组 ID 下载
/dl @channel_username 456    # 按公开用户名下载
```

### 远程控制

| 命令 | 说明 |
|------|------|
| `/pause` | 暂停下载引擎（进行中的不中断，拒绝新任务入队执行） |
| `/resume` | 恢复下载引擎，新任务可正常执行 |
| `/stats` | 查看下载统计（引擎状态、活跃/队列/累计完成失败、下载总量） |

> **暂停行为**：`/pause` 后正在进行中的下载会继续完成，但队列中的新任务会被拒绝（返回 `engine_paused`），需 `/resume` 后才能继续。

## Docker 部署详解

### 生产模式

```bash
cd docker
docker compose up -d
```

特性：
- 多阶段构建，最终镜像仅含运行时依赖
- 非 root 用户运行，安全加固
- tini 作为 init 进程，正确处理信号转发
- 健康检查端点 `http://localhost:18080/healthz`
- 数据卷持久化（data/ 和 config/）
- 日志轮转（50MB × 5 份）
- 资源限制（1GB 内存 / 1 CPU）

### 开发模式

```bash
cd docker
docker compose -f docker-compose.yml -f docker-compose.dev.yml up
```

特性：
- 源代码热重载（挂载 src/ 目录）
- 调试日志级别（DEBUG）
- SQL 回显
- 含 ruff/mypy/pytest 开发工具
- 无资源限制

### 自定义构建

```bash
# 从项目根目录构建
docker build -f docker/Dockerfile -t teleharvest:custom .

# 指定构建阶段
docker build -f docker/Dockerfile --target dev -t teleharvest:dev .
```

## 项目结构

```
teleharvest/
├── src/teleharvest/          # 源代码
│   ├── core/                 # 核心调度与生命周期
│   │   ├── scheduler.py      # 下载任务调度器
│   │   ├── task.py           # DownloadTask 数据模型
│   │   └── lifecycle.py      # 生命周期管理
│   ├── monitor/              # Telegram 消息监控
│   │   ├── client.py         # Pyrogram 客户端（连接/重连/看门狗）
│   │   ├── handler.py        # 消息处理器
│   │   └── filters.py        # 媒体过滤规则
│   ├── downloader/           # 媒体下载引擎
│   │   ├── engine.py         # 9 步下载流程
│   │   ├── progress.py       # 下载进度追踪
│   │   └── resume.py         # 断点续传
│   ├── storage/              # 文件存储与去重
│   │   ├── manager.py        # 存储路径管理
│   │   ├── dedup.py          # SHA256 去重
│   │   └── cleaner.py       # 过期清理与孤儿检测
│   ├── proxy/                # 代理连接管理
│   │   └── manager.py        # 故障转移与健康检查
│   ├── config/               # 配置管理
│   │   ├── schema.py         # Pydantic 配置模型
│   │   └── settings.py       # 配置加载入口
│   ├── db/                   # 数据访问层
│   │   ├── models.py         # SQLAlchemy 模型
│   │   ├── session.py        # 异步会话管理
│   │   └── repositories/     # 仓储模式
│   ├── bot/                  # Bot 交互模块
│   │   ├── notifier.py       # 实时进度与通知推送
│   │   ├── commands.py       # Bot 命令处理器
│   │   └── card.py           # 下载完成通知卡片格式化
│   ├── api/                  # HTTP 健康检查端点
│   ├── utils/                # 工具函数
│   │   ├── retry.py          # 错误分类与重试装饰器
│   │   ├── logger.py         # loguru 日志配置
│   │   ├── hash.py           # 哈希计算
│   │   └── time.py           # 时间工具
│   └── main.py               # 应用主入口
├── tests/                    # 测试（117 个单元测试）
├── docker/                   # Docker 相关文件
├── config/                   # 配置文件
├── scripts/                  # 辅助脚本
└── .github/workflows/        # CI/CD 工作流
```

## 开发

```bash
# 安装开发依赖
pip install -e ".[dev]"

# 运行测试
pytest

# 代码检查
ruff check .
ruff format --check .

# 类型检查
mypy src/teleharvest

# 预提交钩子
pre-commit run --all-files
```

## 技术栈

| 类别 | 选型 |
|------|------|
| 语言 | Python 3.11+ |
| Telegram 客户端 | Pyrogram v2 + TgCrypto |
| 数据库 | SQLite + SQLAlchemy 2.0 (async) |
| 日志 | loguru |
| 配置 | pydantic-settings + YAML |
| 任务调度 | APScheduler |
| 容器化 | Docker + Docker Compose (多阶段构建) |
| 代码质量 | Ruff + mypy (strict) + pytest |
| CI/CD | GitHub Actions |

## 许可证

[MIT](LICENSE)
