# ===== 多阶段构建：TeleHarvest Docker 镜像 =====
# 阶段 1：依赖构建（含编译工具，生成 wheel）
# 阶段 2：运行时（仅含运行所需依赖，体积更小）

# ----------------------------------------------------------------------------
# 阶段 1：builder
# ----------------------------------------------------------------------------
FROM python:3.11-slim AS builder

# 安装编译依赖（tgcrypto / cryptography 等需要）
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    libffi-dev \
    libssl-dev \
    && rm -rf /var/lib/apt/lists/*

# 升级 pip 并安装构建工具
RUN pip install --no-cache-dir --upgrade pip setuptools wheel pip-tools

WORKDIR /build

# 先复制依赖文件，利用 Docker 缓存层
COPY pyproject.toml requirements.in ./

# 编译依赖为 wheel（隔离环境，避免污染系统 Python）
RUN pip-compile --generate-hashes --output-file=requirements.txt requirements.in \
    && pip wheel --no-cache-dir --wheel-dir=/wheels -r requirements.txt

# 复制源码并构建项目 wheel
COPY src/ ./src/
COPY README.md ./
RUN pip wheel --no-cache-dir --wheel-dir=/wheels .


# ----------------------------------------------------------------------------
# 阶段 2：runtime
# ----------------------------------------------------------------------------
FROM python:3.11-slim AS runtime

# OCI 镜像标签
LABEL org.opencontainers.image.title="TeleHarvest" \
      org.opencontainers.image.description="从 Telegram 频道和群组中监控并下载音视频资源的 Docker 化服务" \
      org.opencontainers.image.source="https://github.com/qinmul/teleharvest" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.authors="TeleHarvest Contributors"

# 安装运行时系统依赖
#   - libffi8:     Pyrogram 加密依赖
#   - libssl3:     TLS 支持
#   - ca-certificates: HTTPS 证书
#   - libmagic1:   python-magic 文件类型识别
#   - ffmpeg:      视频元数据提取（ffprobe 智能命名）
#   - tini:        正确的 init 进程（信号转发）
RUN apt-get update && apt-get install -y --no-install-recommends \
    libffi8 \
    libssl3 \
    ca-certificates \
    libmagic1 \
    ffmpeg \
    tini \
    && rm -rf /var/lib/apt/lists/* \
    && update-ca-certificates

# 创建非 root 用户
RUN groupadd -r teleharvest \
    && useradd -r -g teleharvest -m -d /home/teleharvest teleharvest

# 从 builder 阶段复制 wheel 并安装
COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir /wheels/*.whl \
    && rm -rf /wheels

# 切换到非 root 用户
USER teleharvest
WORKDIR /home/teleharvest

# 创建数据与配置目录
RUN mkdir -p data/downloads data/sessions data/logs config

# 复制配置示例文件到 /opt/teleharvest/（不会被卷挂载覆盖）
COPY --chown=teleharvest:teleharvest config/config.example.yaml /opt/teleharvest/config.example.yaml
COPY --chown=teleharvest:teleharvest config/.env.example /opt/teleharvest/.env.example

# 复制入口脚本
COPY --chown=teleharvest:teleharvest docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# 声明卷（持久化数据与会话）
VOLUME ["/home/teleharvest/data", "/home/teleharvest/config"]

# 健康检查端点
EXPOSE 18080

# 健康检查（每 30s 探测一次）
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:18080/healthz', timeout=3)" || exit 1

# 使用 tini 作为 init 进程（正确处理信号转发）
ENTRYPOINT ["/usr/bin/tini", "--", "/entrypoint.sh"]

# 默认命令
CMD ["teleharvest"]


# ----------------------------------------------------------------------------
# 阶段 3：dev（开发模式，含开发工具）
# ----------------------------------------------------------------------------
FROM runtime AS dev

# 切回 root 安装开发依赖
USER root

# 安装开发工具（ruff / mypy / pytest 等）
RUN pip install --no-cache-dir \
    ruff \
    mypy \
    pytest \
    pytest-asyncio \
    pytest-cov \
    types-pyyaml

# 切回非 root 用户
USER teleharvest

# 开发模式覆盖命令（由 docker-compose.dev.yml 指定）
CMD ["python", "-m", "teleharvest"]
