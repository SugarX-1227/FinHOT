"""信源适配器注册表与通用翻页逻辑。

新增一个信源类型：
1. 在 fetchers/ 下写一个函数 `fetch_xxx(ctx) -> Iterable[NewsItem]`（按时间倒序逐页产出）；
2. 用 `@register("xxx")` 注册；
3. 在 sources/sources.yaml 里添加 `type: xxx` 的配置。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Iterable

from ..http import Session
from ..models import NewsItem

log = logging.getLogger(__name__)

FetchFn = Callable[["FetchContext"], Iterable[list[NewsItem]]]
REGISTRY: dict[str, FetchFn] = {}


def register(name: str):
    def deco(fn: FetchFn) -> FetchFn:
        REGISTRY[name] = fn
        return fn
    return deco


@dataclass
class FetchContext:
    source: dict[str, Any]      # sources.yaml 中的单个信源配置
    session: Session
    since: datetime             # 只要这个时间之后的条目
    max_pages: int = 20
    page_delay: float = 0.6     # 翻页间隔，礼貌抓取

    @property
    def options(self) -> dict[str, Any]:
        return self.source.get("options") or {}

    def make(self, **kw) -> NewsItem:
        """用信源配置补齐公共字段后构造 NewsItem。"""
        kw.setdefault("source", self.source["id"])
        kw.setdefault("source_name", self.source["name"])
        kw.setdefault("lang", self.source.get("lang", "zh"))
        kw.setdefault("tier", self.source.get("tier", "media"))
        kw.setdefault("weight", float(self.source.get("weight", 1.0)))
        return NewsItem(**kw)


@dataclass
class FetchResult:
    source_id: str
    name: str
    items: list[NewsItem] = field(default_factory=list)
    ok: bool = True
    error: str = ""
    pages: int = 0
    seconds: float = 0.0


def run_fetch(ctx: FetchContext) -> FetchResult:
    """执行单个信源的抓取；任何异常都被隔离在本信源内。"""
    src = ctx.source
    res = FetchResult(src["id"], src["name"])
    fn = REGISTRY.get(src["type"])
    t0 = time.time()
    if fn is None:
        res.ok, res.error = False, f"未知信源类型: {src['type']}"
        return res
    seen: set[str] = set()
    try:
        for page in fn(ctx):
            res.pages += 1
            fresh = [it for it in page if it.published >= ctx.since and it.uid not in seen]
            for it in fresh:
                seen.add(it.uid)
            res.items.extend(fresh)
            # 本页最后一条（最旧）已早于 since → 翻到头了。用最后一条而非最小值，避免置顶旧闻导致提前停止
            if not page or page[-1].published < ctx.since or res.pages >= ctx.max_pages:
                break
            time.sleep(ctx.page_delay)
    except Exception as e:  # noqa: BLE001 —— 单源失败不影响整体
        res.ok = False
        res.error = f"{type(e).__name__}: {e}"[:300]
        log.warning("信源 %s 抓取失败: %s", src["id"], res.error)
    res.seconds = round(time.time() - t0, 2)
    # 翻页过程中抓到了部分数据，也算部分成功
    if not res.ok and res.items:
        res.error = "部分成功 · " + res.error
    return res
