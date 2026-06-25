"""哈希工具：用于文件去重校验。"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


def compute_file_hash(
    file_path: Path,
    algorithm: str = "sha256",
    chunk_size: int = 8192 * 16,  # 128KB
) -> str:
    """计算文件哈希值。

    Args:
        file_path: 文件路径
        algorithm: 哈希算法（sha256 / md5）
        chunk_size: 读取块大小

    Returns:
        十六进制哈希字符串
    """
    hasher = hashlib.new(algorithm)
    with file_path.open("rb") as f:
        while chunk := f.read(chunk_size):
            hasher.update(chunk)
    return hasher.hexdigest()


def compute_bytes_hash(data: bytes, algorithm: str = "sha256") -> str:
    """计算字节串哈希值。"""
    hasher = hashlib.new(algorithm)
    hasher.update(data)
    return hasher.hexdigest()
