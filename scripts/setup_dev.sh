#!/usr/bin/env bash
# TeleHarvest 开发环境初始化脚本
# 使用方式：bash scripts/setup_dev.sh

set -e

echo "=========================================="
echo "  TeleHarvest 开发环境初始化"
echo "=========================================="

# 1. 检查 Python 版本
if ! command -v python3.11 &> /dev/null; then
    echo "[WARN] 未找到 python3.11，尝试使用 python3"
    PYTHON=python3
else
    PYTHON=python3.11
fi

echo "[1/5] Python: $($PYTHON --version)"

# 2. 创建虚拟环境
echo "[2/5] 创建虚拟环境..."
if [ ! -d ".venv" ]; then
    $PYTHON -m venv .venv
    echo "  虚拟环境已创建: .venv"
else
    echo "  虚拟环境已存在，跳过"
fi

# 激活虚拟环境
source .venv/bin/activate 2>/dev/null || source .venv/Scripts/activate 2>/dev/null

# 3. 升级 pip
echo "[3/5] 升级 pip..."
python -m pip install --upgrade pip setuptools wheel

# 4. 安装项目（开发模式）
echo "[4/5] 安装项目依赖（含开发工具）..."
pip install -e ".[dev]"

# 5. 安装 pre-commit 钩子
echo "[5/5] 安装 pre-commit 钩子..."
pre-commit install

# 6. 创建配置文件
if [ ! -f "config/config.yaml" ]; then
    cp config/config.example.yaml config/config.yaml
    echo "[INFO] 已从示例创建配置文件: config/config.yaml"
fi

if [ ! -f ".env" ]; then
    cp config/.env.example .env
    echo "[INFO] 已从示例创建环境变量文件: .env"
    echo "[WARN] 请编辑 .env 填入 Telegram API 凭据"
fi

echo "=========================================="
echo "  初始化完成！"
echo ""
echo "  下一步："
echo "  1. 编辑 .env 填入 TELEHARVEST_API_ID 和 TELEHARVEST_API_HASH"
echo "  2. 编辑 config/config.yaml 配置订阅频道"
echo "  3. 运行: python -m teleharvest"
echo "=========================================="
