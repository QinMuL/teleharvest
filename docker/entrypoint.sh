#!/bin/sh
# TeleHarvest 容器入口脚本
# 职责：
#   1. 校验必要的环境变量
#   2. 确保数据目录存在
#   3. 初始化配置文件（若不存在则从示例复制）
#   4. 启动主进程

set -e

echo "=========================================="
echo "  TeleHarvest 容器启动"
echo "  时间: $(date '+%Y-%m-%d %H:%M:%S')"
echo "=========================================="

# ===== 1. 环境变量校验 =====
if [ -z "$TELEHARVEST_API_ID" ] || [ -z "$TELEHARVEST_API_HASH" ]; then
    echo "[FATAL] 缺少必要的环境变量:"
    echo "  TELEHARVEST_API_ID  - Telegram API ID"
    echo "  TELEHARVEST_API_HASH - Telegram API Hash"
    echo ""
    echo "申请地址: https://my.telegram.org"
    echo "可通过 .env 文件或 docker-compose 环境变量注入"
    exit 1
fi

echo "[INFO] API 凭据已加载: API_ID=$TELEHARVEST_API_ID"

# ===== 2. 目录初始化 =====
DATA_DIR="${TELEHARVEST_DATA_DIR:-/home/teleharvest/data}"
CONFIG_DIR="${TELEHARVEST_CONFIG_DIR:-/home/teleharvest/config}"
EXAMPLE_DIR="/opt/teleharvest"

mkdir -p "$DATA_DIR/downloads" "$DATA_DIR/sessions" "$DATA_DIR/logs" "$CONFIG_DIR"

# 若配置文件不存在，从内置示例复制
if [ ! -f "$CONFIG_DIR/config.yaml" ]; then
    if [ -f "$EXAMPLE_DIR/config.example.yaml" ]; then
        cp "$EXAMPLE_DIR/config.example.yaml" "$CONFIG_DIR/config.yaml"
        echo "[INFO] 已从内置示例创建配置文件: $CONFIG_DIR/config.yaml"
    else
        echo "[WARN] 配置文件不存在且无内置示例: $CONFIG_DIR/config.yaml"
        echo "[WARN] 将使用环境变量和代码默认值"
    fi
fi

# ===== 3. 权限检查 =====
if [ ! -w "$DATA_DIR" ]; then
    echo "[FATAL] 数据目录不可写: $DATA_DIR"
    exit 1
fi

echo "[INFO] 数据目录: $DATA_DIR"
echo "[INFO] 配置目录: $CONFIG_DIR"
echo "[INFO] 代理状态: ${TELEHARVEST_PROXY__ENABLED:-未启用}"
echo "=========================================="

# ===== 4. 启动主进程 =====
# 使用 exec 替换当前进程，确保信号正确传递
exec "$@"
