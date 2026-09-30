"""统一的新闻条目数据结构。所有信源适配器都输出 NewsItem。"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from .timeutil import TZ


@dataclass
class NewsItem:
    source: str                 # 信源 id，如 cls / wallstreetcn
    source_name: str            # 信源显示名
    title: str                  # 标题；快讯类没有标题时取正文首句
    content: str                # 正文纯文本（快讯全文或摘要）
    url: str                    # 原文链接
    published: datetime         # 发布时间（带时区，统一转北京时间）
    source_id: str = ""         # 信源内部 id
    important: bool = False     # 信源自带的"重要/加红"标记
    tags: list[str] = field(default_factory=list)     # 信源自带的标签/主题
    stocks: list[str] = field(default_factory=list)   # 信源自带的关联个股
    lang: str = "zh"
    tier: str = "media"         # official | media
    weight: float = 1.0         # 信源权重（配置文件给出）
    imp_weight: float = 1.0     # 该信源"重要"标记的可信度（有的源标得太多，要打折）

    # ---- 后处理阶段填充 ----
    category: str = ""          # 宏观 / 政策 / 海外 / 行业 / 公司 / 市场
    score: float = 0.0          # 重要性 0-10
    dup_sources: list[str] = field(default_factory=list)  # 被合并进来的其他信源
    dup_count: int = 1          # 聚簇大小（多少条相似报道）
    imp_votes: float = 0.0      # 簇内"重要"标记的加权票数（每个信源按 imp_weight 计一次）
    imp_sources: list[str] = field(default_factory=list)  # 标记"重要"的信源 id
    llm: dict[str, Any] = field(default_factory=dict)     # LLM 输出（可选）

    @property
    def uid(self) -> str:
        raw = f"{self.source}:{self.source_id or self.url or self.title}"
        return hashlib.md5(raw.encode("utf-8")).hexdigest()[:16]

    def to_dict(self, max_content: int | None = None) -> dict[str, Any]:
        d = asdict(self)
        d["uid"] = self.uid
        d["published"] = self.published.astimezone(TZ).isoformat()
        if max_content and len(d["content"]) > max_content:
            d["content"] = d["content"][:max_content] + "…"
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "NewsItem":
        d = dict(d)
        d.pop("uid", None)
        d["published"] = datetime.fromisoformat(d["published"])
        known = cls.__dataclass_fields__.keys()
        return cls(**{k: v for k, v in d.items() if k in known})
