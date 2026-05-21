"""内置数据目录定位。

开发态数据位于仓库根目录的 ``data/``；打包安装后，Hatchling shared-data
会把它们安装到 ``sys.prefix/chinalaw/data``。这里集中处理两种路径。
"""

from __future__ import annotations

import sys
from pathlib import Path


def builtin_data_dir(name: str) -> Path:
    repo_dir = Path(__file__).resolve().parents[2] / "data" / name
    install_dir = Path(sys.prefix) / "chinalaw" / "data" / name
    base_install_dir = Path(sys.base_prefix) / "chinalaw" / "data" / name

    for candidate in (repo_dir, install_dir, base_install_dir):
        if candidate.exists():
            return candidate
    return repo_dir


def builtin_data_file(name: str) -> Path:
    repo_file = Path(__file__).resolve().parents[2] / "data" / name
    install_file = Path(sys.prefix) / "chinalaw" / "data" / name
    base_install_file = Path(sys.base_prefix) / "chinalaw" / "data" / name

    for candidate in (repo_file, install_file, base_install_file):
        if candidate.exists():
            return candidate
    return repo_file
