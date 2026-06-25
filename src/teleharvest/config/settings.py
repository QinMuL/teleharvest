"""配置加载入口：合并 YAML 文件、环境变量、默认值。

加载流程：
    1. 读取 config.yaml（若存在）作为基础配置
    2. 环境变量 / .env 文件覆盖（由 pydantic-settings 自动处理）
    3. pydantic 校验类型与约束
    4. 失败则抛出明确异常，由 main.py fail-fast
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from teleharvest.config.schema import AppSettings

# 默认配置文件路径（相对工作目录）
DEFAULT_CONFIG_PATH = Path("config/config.yaml")


def load_yaml_config(path: Path | None = None) -> dict[str, Any]:
    """加载 YAML 配置文件为字典。

    Args:
        path: 配置文件路径，为 None 时使用默认路径

    Returns:
        配置字典；文件不存在时返回空字典
    """
    config_path = path or DEFAULT_CONFIG_PATH
    if not config_path.exists():
        return {}
    with config_path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"配置文件 {config_path} 顶层必须是字典/映射结构")
    return data


def load_settings(config_path: Path | None = None) -> AppSettings:
    """加载并校验应用配置。

    优先级：环境变量 > .env > config.yaml > 默认值

    Args:
        config_path: 配置文件路径，为 None 时使用 config/config.yaml

    Returns:
        已校验的 AppSettings 实例

    Raises:
        ValidationError: 配置校验失败
        ValueError: 配置文件格式错误
    """
    yaml_data = load_yaml_config(config_path)
    try:
        # pydantic-settings 会自动合并 yaml_data（作为 init 参数）与环境变量
        # 环境变量优先级更高，会覆盖 yaml 中的同名配置
        return AppSettings(**yaml_data)
    except ValidationError as exc:
        # 重新抛出，附带更友好的错误信息
        errors = []
        for err in exc.errors():
            loc = ".".join(str(x) for x in err["loc"])
            errors.append(f"  - {loc}: {err['msg']}")
        raise ValueError(f"配置校验失败，共 {len(errors)} 处错误：\n" + "\n".join(errors)) from exc
