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


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()
