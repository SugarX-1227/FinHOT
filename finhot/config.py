from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
SOURCES_FILE = ROOT / "sources" / "sources.yaml"
PROMPTS_DIR = ROOT / "prompts"
DATA_DIR = ROOT / "data"
REPORTS_DIR = ROOT / "reports"


def load_sources(path: Path = SOURCES_FILE) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    cfg.setdefault("sources", [])
    cfg.setdefault("markets", [])
    return cfg


def public_data_dir() -> Path:
    """公开的 data/（日报存下的快讯、公告、回测）。服务器上推送进程的 /app/data 是它自己的数据卷，
    仓库里的 data/ 以只读方式挂在 FINHOT_PUBLIC_DATA_DIR（见 docker-compose.yml）。"""
    return Path(env("FINHOT_PUBLIC_DATA_DIR") or DATA_DIR)


def env(name: str, default: str = "") -> str:
    """读取环境变量；未设置或为空字符串都返回默认值（GitHub Actions 中未配置的变量会是空串）。"""
    return os.environ.get(name, "").strip() or default
