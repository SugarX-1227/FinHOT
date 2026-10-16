"""上市公司公告：取（巨潮为主、东方财富备用，两边的并在一起去重）、按类型分类、按白名单过滤、落盘。

全市场的公告是公开数据，按“类型白名单”过滤后存在 data/<日期>/announcements*.jsonl（不按任何人的自选过滤，
所以可以入库）：涨停归因、个人简报、按公告类型的事件研究都从这里取。按自选股过滤只在服务器上做。

公告不进模型：标题已经说清楚是什么事，类型表（sources/announcements.yaml）给出重要性。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path

import yaml

from .collect import collect
from .config import DATA_DIR, ROOT, load_sources
from .fetchers import FetchResult
from .models import NewsItem
from .textutil import normalize

log = logging.getLogger(__name__)
TYPES_FILE = ROOT / "sources" / "announcements.yaml"


@dataclass(frozen=True)
class AnnType:
    name: str
    importance: float = 2.0
    push: bool = False
    catalyst: str = ""      # "催化" / "提示" / ""
    whitelist: bool = False


OTHER = AnnType("其他")


@lru_cache(maxsize=1)
def _types() -> tuple[re.Pattern | None, list[tuple[re.Pattern, AnnType]]]:
    try:
        cfg = yaml.safe_load(TYPES_FILE.read_text(encoding="utf-8")) or {}
    except OSError:
        return None, []
    out = []
    for t in cfg.get("types") or []:
        cat = t.get("catalyst")
        out.append((re.compile(t["pattern"]), AnnType(
            t["name"], float(t.get("importance", 2)), bool(t.get("push")),
            "催化" if cat is True else "提示" if cat == "notice" else "", bool(t.get("whitelist")))))
    exclude = re.compile(cfg["exclude"]) if cfg.get("exclude") else None
    return exclude, out


def classify_type(title: str) -> AnnType:
    exclude, types = _types()
    if exclude and exclude.search(title):
        return OTHER
    return next((t for pat, t in types if pat.search(title)), OTHER)


def type_of(it: NewsItem) -> AnnType:
    name = it.tags[0] if it.tags else ""
    return next((t for _, t in _types()[1] if t.name == name), OTHER)


def is_announcement(it: NewsItem) -> bool:
    """公告源产出的条目：带代码、官方信源、第一个标签是公告类型。"""
    return bool(it.codes) and it.tier == "official" and bool(it.tags) and it.tags[0] in _type_names()


@lru_cache(maxsize=1)
def _type_names() -> frozenset[str]:
    return frozenset([OTHER.name, *(t.name for _, t in _types()[1])])


def announcement_sources(cfg: dict | None = None) -> list[dict]:
    cfg = cfg or load_sources()
    return [s for s in cfg.get("announcements") or [] if s.get("enabled", True)]


def merge_sources(results: list[FetchResult]) -> list[NewsItem]:
    """几个公告源的并在一起：同一家公司、标题一样的只留一条（巨潮优先，排在前面的源优先）。"""
    seen: set[tuple[str, str]] = set()
    out: list[NewsItem] = []
    for r in results:
        for it in r.items:
            key = (it.codes[0] if it.codes else "", normalize(it.content or it.title))
            if key not in seen:
                seen.add(key)
                out.append(it)
    out.sort(key=lambda it: it.published, reverse=True)
    return out


def fetch(since: datetime, codes: dict[str, str] | None = None, sources: list[dict] | None = None,
          whitelist_only: bool = True, max_pages: int | None = None) -> tuple[list[NewsItem], list[FetchResult]]:
    """取 since 之后的公告。codes（{代码: 巨潮 orgId}）给定时只查这些公司（空字典就什么都不查），None 是全市场。
    返回（公告, 各源抓取结果）。"""
    if codes is not None and not codes:
        return [], []
    srcs = [dict(s, options={**(s.get("options") or {}), **({"codes": codes} if codes is not None else {})})
            for s in (sources if sources is not None else announcement_sources())]
    results = collect(srcs, since, max_pages=max_pages)
    items = merge_sources(results)
    if codes is not None:   # 备用源按代码批量查时可能多给，只留要的
        items = [it for it in items if it.codes and it.codes[0] in codes]
    if whitelist_only:
        items = [it for it in items if type_of(it).whitelist]
    return items, results


def save(items: list[NewsItem], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(it.to_dict(), ensure_ascii=False) + "\n" for it in items), encoding="utf-8")


def load_recent(until: datetime, days: int = 2, data_dir: Path = DATA_DIR) -> list[NewsItem]:
    """data/ 里最近几天存下的公告（同一条在几期里重复的只留一条）。"""
    out: dict[str, NewsItem] = {}
    for d in range(days + 1):
        day = (until - timedelta(days=d)).strftime("%Y-%m-%d")
        for path in sorted((data_dir / day).glob("announcements*.jsonl")):
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    it = NewsItem.from_dict(json.loads(line))
                    out.setdefault(it.uid, it)
    return sorted(out.values(), key=lambda it: it.published, reverse=True)
