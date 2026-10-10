"""新闻 × 资金 交叉验证。

把新闻题材热度（多少条高分新闻、分数之和）和板块表现（涨幅、主力净流入、涨停家数）放到同一张表里：
- 共振：新闻多 + 板块涨/涨停多 + 资金净流入 → 题材有持续性的可能更大
- 资金先行：板块大涨/涨停扎堆，但几乎没有新闻 → 留意是否有未被报道的催化
- 新闻热·资金冷：新闻很多但板块下跌、资金流出 → 利好可能已被消化或不被认可
注意：盘前版里的板块数据是上一交易日收盘数据，新闻多为隔夜消息，表示"昨日资金 + 隔夜消息"。
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from statistics import median

from .ashare import MarketSnapshot
from .models import NewsItem
from .themes import board_themes, item_themes


@dataclass
class ThemeRow:
    theme: str
    news_count: int = 0
    heat: float = 0.0
    news: list[NewsItem] = field(default_factory=list)
    pct: float | None = None
    inflow: float | None = None
    zt: int = 0
    top_board: str = ""
    top_board_pct: float | None = None
    zt_stocks: list[str] = field(default_factory=list)
    verdict: str = ""


def cross_check(items: list[NewsItem], snap: MarketSnapshot, min_score: float = 5.0,
                top_items: int = 300, limit: int = 10) -> list[ThemeRow]:
    rows: dict[str, ThemeRow] = {}

    def row(t: str) -> ThemeRow:
        return rows.setdefault(t, ThemeRow(t))

    for it in items[:top_items]:
        if it.score < min_score:
            continue
        for t in item_themes(it):
            r = row(t)
            r.news_count += 1
            r.heat += it.score
            if len(r.news) < 3:
                r.news.append(it)

    board_map = defaultdict(list)
    for b in [*snap.industries, *snap.concepts]:
        for t in board_themes(b.name):
            board_map[t].append(b)
    for t, boards in board_map.items():
        r = row(t)
        r.pct = round(median(b.pct for b in boards), 2)
        ind = [b for b in boards if b.kind == "industry"]
        src = ind or sorted(boards, key=lambda b: -abs(b.inflow))[:3]
        r.inflow = round(sum(b.inflow for b in src), 1)
        best = max(boards, key=lambda b: b.pct)
        r.top_board, r.top_board_pct = best.name, best.pct

    for s in snap.zt_stocks:
        for t in board_themes(s["industry"]):
            r = row(t)
            r.zt += 1
            if len(r.zt_stocks) < 4:
                r.zt_stocks.append(s["name"])

    for r in rows.values():
        pct = r.pct if r.pct is not None else 0.0
        inflow = r.inflow if r.inflow is not None else 0.0
        if r.news_count >= 2 and (pct >= 1 or r.zt >= 3) and inflow >= 0:
            r.verdict = "共振"
        elif r.news_count <= 1 and (pct >= 2 or r.zt >= 3):
            r.verdict = "资金先行"
        elif r.news_count >= 3 and pct < 0 and inflow < 0:
            r.verdict = "新闻热·资金冷"

    order = {"共振": 0, "资金先行": 1, "新闻热·资金冷": 2}
    picked = [r for r in rows.values() if r.verdict and r.pct is not None]
    picked.sort(key=lambda r: (order[r.verdict], -(r.heat + r.zt * 3 + max(r.pct or 0, 0) * 2)))
    return picked[:limit]
