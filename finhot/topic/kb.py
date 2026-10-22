"""知识层：topics/<名称>.yaml，人工维护（或 `topic draft` 让模型起草后人工校对），程序只读。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ..config import ROOT

TOPICS_DIR = ROOT / "topics"
REQUIRED = ("name", "slug", "one_liner", "chain", "wencai", "local")


@dataclass
class Stock:
    name: str
    code: str = ""
    tier: str = "相关"      # 核心 / 相关（知识层的判断，问财主营数据会再核对）
    note: str = ""
    segment: str = ""
    stage: str = ""


@dataclass
class Topic:
    path: Path
    data: dict[str, Any] = field(default_factory=dict)

    @property
    def title(self) -> str:
        return str(self.data["name"])

    @property
    def slug(self) -> str:
        return str(self.data["slug"])

    def get(self, key: str, default: Any = None) -> Any:
        v = self.data.get(key)
        return default if v is None else v

    def stocks(self) -> list[Stock]:
        """产业链里列出的全部个股（同一只股票在多个环节出现时各算一次）。"""
        out = []
        for st in self.data.get("chain") or []:
            for seg in st.get("segments") or []:
                for s in seg.get("stocks") or []:
                    out.append(Stock(name=str(s.get("name", "")), code=str(s.get("code") or ""),
                                     tier=str(s.get("tier") or "相关"), note=str(s.get("note") or ""),
                                     segment=str(seg.get("name", "")), stage=str(st.get("stage", ""))))
        return out

    def segments(self) -> list[dict]:
        return [dict(seg, stage=st.get("stage", "")) for st in self.data.get("chain") or []
                for seg in st.get("segments") or []]

    def pattern(self, key: str) -> re.Pattern | None:
        s = (self.data.get("local") or {}).get(key)
        return re.compile(s, re.I) if s else None


def validate(data: dict) -> list[str]:
    errs = [f"缺少字段 {k}" for k in REQUIRED if not data.get(k)]
    for k in ("universe", "index", "search"):
        if not (data.get("wencai") or {}).get(k):
            errs.append(f"wencai.{k} 为空")
    if not (data.get("local") or {}).get("keywords"):
        errs.append("local.keywords 为空")
    for key in ("keywords", "exclude", "boards"):
        s = (data.get("local") or {}).get(key)
        if s:
            try:
                re.compile(s)
            except re.error as e:
                errs.append(f"local.{key} 不是合法正则：{e}")
    return errs


def load(path: Path) -> Topic:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    errs = validate(data)
    if errs:
        raise ValueError(f"{path.name}：" + "；".join(errs))
    return Topic(path, data)


def all_topics(topics_dir: Path = TOPICS_DIR) -> list[Topic]:
    return [load(p) for p in sorted(topics_dir.glob("*.yaml"))]


def find(name: str, topics_dir: Path = TOPICS_DIR) -> Topic | None:
    """按名称、slug 或别名找题材。"""
    key = name.strip().lower()
    for t in all_topics(topics_dir):
        names = [t.title, t.data.get("slug", ""), *(t.data.get("aliases") or [])]
        if key in (str(n).lower() for n in names):
            return t
    return None
