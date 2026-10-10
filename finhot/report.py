"""把处理后的条目渲染成 Markdown 日报。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .ashare import MarketSnapshot
from .calendar_watch import Watchlist
from .crosscheck import ThemeRow
from .fetchers import FetchResult
from .models import NewsItem

SECTIONS = [
    ("宏观与政策", ("政策", "宏观")),
    ("海外市场", ("海外",)),
    ("行业与产业", ("行业",)),
    ("公司动态", ("公司",)),
    ("市场与资金", ("市场",)),
]
CN_NUM = "一二三四五六七八九十十一十二"
DIR_ICON = {"利好": "🔺利好", "利空": "🔻利空", "中性": "➖中性"}


@dataclass
class ReportContext:
    date: str
    edition: str
    since: datetime
    until: datetime
    items: list[NewsItem]
    results: list[FetchResult]
    quotes: list[dict] = field(default_factory=list)
    overview: str = ""
    raw_count: int = 0
    llm_stats: dict = field(default_factory=dict)
    top_n: int = 10
    per_section: int = 8
    min_score: float = 4.0
    snapshot: MarketSnapshot | None = None
    watch: Watchlist | None = None
    themes: list[ThemeRow] = field(default_factory=list)
    edition_key: str = "pre"


def _brief(it: NewsItem, limit: int = 140) -> str:
    if it.llm.get("summary"):
        return it.llm["summary"]
    text = " ".join(it.content.split())
    head = it.title.rstrip("…")
    if text.startswith(head):
        text = text[len(head):].lstrip("，,。：:；; ")
    text = text.strip()
    if len(text) < 15 or text == it.title:
        return ""
    return text if len(text) <= limit else text[:limit] + "…"


def _entry(it: NewsItem, idx: int | None = None) -> str:
    """单条新闻的 Markdown。

    行尾的反斜杠是 CommonMark 硬换行（GitHub 与网站都按换行显示）；
    利好/利空映射放在列表项内单独的引用块里，前面空一行避免把元信息吞进引用。
    """
    title = it.llm.get("title") or it.title
    head = f"{idx}. " if idx is not None else "- "
    pad = " " * len(head)
    # 🔴：被多家信源同时标为重要（加权票数 ≥ 2）
    flag = " 🔴" if it.imp_votes >= 2 else ""
    srcs = it.source_name + (f" 等{1 + len(it.dup_sources)}家" if it.dup_sources else "")
    meta = f"{it.published:%m-%d %H:%M} · {srcs} · {it.category} · {it.score:.1f}分"
    link = f" · [原文]({it.url})" if it.url else ""
    body = [f"**{title}**{flag}"]
    brief = _brief(it)
    if brief:
        body.append(brief)
    body.append(f"<sub>{meta}{link}</sub>")
    lines = [head + "\\\n".join(body).replace("\n", "\n" + pad)]
    if it.llm.get("direction"):
        parts = [DIR_ICON.get(it.llm["direction"], it.llm["direction"])]
        if it.llm.get("sectors"):
            parts.append("板块：" + "、".join(it.llm["sectors"]))
        if it.llm.get("stocks"):
            parts.append("个股：" + "、".join(it.llm["stocks"]))
        if it.llm.get("horizon"):
            parts.append(it.llm["horizon"])
        quote = [" · ".join(parts)]
        if it.llm.get("basis"):
            quote.append(f"依据：{it.llm['basis']}")
        lines.append("")
        lines.append(pad + "> " + ("\\\n" + pad + "> ").join(quote))
    return "\n".join(lines)


def _fmt_num(v, digits: int = 2) -> str:
    if v is None or v == "-":
        return "-"
    try:
        return f"{float(v):,.{digits}f}"
    except (TypeError, ValueError):
        return str(v)


def _pct(v, signed: bool = True) -> str:
    if v is None or v == "-":
        return "-"
    v = float(v)
    arrow = "🔺" if v > 0 else "🔻" if v < 0 else ""
    return f"{arrow}{v:+.2f}%" if signed else f"{v:.2f}%"


def _yi(v) -> str:
    return "-" if v is None else f"{float(v):+,.1f}亿"


class _Sections:
    def __init__(self):
        self.n = 0

    def title(self, name: str) -> str:
        self.n += 1
        return f"## {CN_NUM[self.n - 1] if self.n <= 10 else self.n}、{name}"


def _render_quotes(ctx: ReportContext) -> list[str]:
    if not ctx.quotes:
        return ["_行情数据暂不可用_", ""]
    out = ["| 品种 | 最新 | 涨跌 | 涨跌幅 | 更新 |", "|---|---:|---:|---:|---|"]
    for q in ctx.quotes:
        digits = 4 if abs(float(q["price"])) < 20 else 2
        out.append(f"| {q['name']} | {_fmt_num(q['price'], digits)} | {_fmt_num(q['change'], digits)} "
                   f"| {_pct(q.get('pct'))} | {q['time']} |")
    return out + [""]


def _render_watch(w: Watchlist) -> list[str]:
    out: list[str] = []
    if w.data:
        out += ["**经济数据**", "", "| 时间 | 国家/地区 | 指标 | 预期 | 前值 |", "|---|---|---|---:|---:|"]
        for r in w.data[:12]:
            star = "⭐" * max(0, r["importance"] - 2)
            out.append(f"| {r['time']} | {r['country']} | {r['title']}{star} | {r['forecast']} | {r['previous']} |")
        out.append("")
    if w.events:
        out += ["**重要事件**", ""]
        for r in w.events[:10]:
            when = "" if r["time"] == "待定" else f"{r['time']} "
            out.append(f"- {when}【{r['country']}】{r['title']}")
        out.append("")
    if w.ipo_apply or w.ipo_list:
        out += ["**新股**", ""]
        for r in w.ipo_apply:
            price = f"发行价 {r['price']} 元" if r.get("price") else "发行价待定"
            out.append(f"- 申购：{r['name']}（申购代码 {r['apply_code']}，{r['market']}，{price}）{r['business']}")
        for r in w.ipo_list:
            out.append(f"- 上市：{r['name']}（{r['code']}，{r['market']}）{r['business']}")
        out.append("")
    if w.lifts:
        out += ["**限售解禁（未来 7 天，按解禁市值）**", "", "| 日期 | 股票 | 解禁市值 | 占流通股 | 类型 |",
                "|---|---|---:|---:|---|"]
        for r in w.lifts:
            out.append(f"| {r['date']} | {r['name']}（{r['code']}） | {r['cap']:,.1f}亿 | {r['ratio']:.2f}% | {r['type']} |")
        out.append("")
    return out


def _render_ashare(s: MarketSnapshot) -> list[str]:
    out: list[str] = []
    if s.indices:
        out += ["| 指数 | 收盘 | 涨跌幅 | 成交额 |", "|---|---:|---:|---:|"]
        for r in s.indices:
            out.append(f"| {r['name']} | {_fmt_num(r['price'])} | {_pct(r['pct'])} | {r['amount']:,.0f}亿 |")
        out.append("")
    b = s.breadth
    facts = []
    if s.turnover:
        facts.append(f"沪深京成交 **{s.turnover / 1e4:.2f} 万亿**")
    if b.get("up") or b.get("down"):
        facts.append(f"上涨 {b.get('up', 0)} / 下跌 {b.get('down', 0)} 家")
    if "zt" in b:
        facts.append(f"涨停 {b['zt']} / 跌停 {b.get('dt', 0)} / 炸板 {b.get('zb', 0)}（封板率 {b.get('seal_rate', 0)}%）")
    if s.hsgt:
        facts.append(f"南向净买入 {_yi(s.hsgt.get('south_net'))} · 北向成交 {s.hsgt.get('north_turnover', 0):,.0f}亿")
    if facts:
        out += ["- " + " · ".join(facts)]
    if s.ladder:
        out.append("- **连板梯队**：" + "；".join(
            f"{r['boards']}板 {'、'.join(r['stocks'][:5])}{'等' if len(r['stocks']) > 5 else ''}" for r in s.ladder))
    if s.zt_industries:
        out.append("- **涨停集中**：" + " · ".join(
            f"{r['name']} {r['count']}家（{'、'.join(r['stocks'][:3])}）" for r in s.zt_industries[:6]))
    if s.industries:
        top = s.industries[:5]
        out.append("- **领涨行业**：" + "、".join(f"{x.name} {x.pct:+.2f}%（{x.leader}）" for x in top))
        inflow = sorted(s.industries, key=lambda x: -x.inflow)[:5]
        out.append("- **主力净流入**：" + "、".join(f"{x.name} {x.inflow:+.1f}亿" for x in inflow))
        outflow = [x for x in sorted(s.industries, key=lambda x: x.inflow) if x.inflow < 0][:3]
        if outflow:
            out.append("- **主力净流出**：" + "、".join(f"{x.name} {x.inflow:+.1f}亿" for x in outflow))
    if s.concepts:
        out.append("- **领涨概念**：" + "、".join(f"{x.name} {x.pct:+.2f}%" for x in s.concepts[:6]))
    out.append("")
    if s.lhb_buy:
        out += [f"**龙虎榜净买入（{s.lhb_date}）**", "", "| 股票 | 涨跌幅 | 净买入 | 说明 |", "|---|---:|---:|---|"]
        for r in s.lhb_buy[:6]:
            out.append(f"| {r['name']}（{r['code']}） | {_pct(r['pct'])} | {r['net']:+.2f}亿 | {r['reason'] or r['rule']} |")
        if s.lhb_sell:
            out.append("")
            out.append("净卖出居前：" + "、".join(f"{r['name']} {r['net']:+.2f}亿" for r in s.lhb_sell[:5]))
        out.append("")
    return out


def _render_themes(rows: list[ThemeRow], s: MarketSnapshot) -> list[str]:
    out = [f"> 新闻热度取本期得分≥5的新闻；板块与涨停数据为 {s.trade_date or '最近交易日'} 收盘。"
           "共振=新闻多且板块走强/资金流入，资金先行=板块大涨或涨停扎堆但新闻少，新闻热·资金冷=新闻多但板块下跌、资金流出。", "",
           "| 题材 | 判断 | 新闻 | 板块中位涨幅 | 主力净流入 | 涨停 | 代表新闻 / 涨停股 |", "|---|---|---:|---:|---:|---:|---|"]
    icon = {"共振": "🔥共振", "资金先行": "💰资金先行", "新闻热·资金冷": "🧊新闻热·资金冷"}
    for r in rows:
        news = "；".join((n.llm.get("title") or n.title)[:28] for n in r.news[:2])
        if not news and r.zt_stocks:
            news = "涨停：" + "、".join(r.zt_stocks)
        board = f"{_pct(r.pct)}" + (f"<br><sub>{r.top_board} {r.top_board_pct:+.1f}%</sub>" if r.top_board else "")
        out.append(f"| {r.theme} | {icon.get(r.verdict, r.verdict)} | {r.news_count} | {board} | "
                   f"{_yi(r.inflow)} | {r.zt} | {news} |")
    return out + [""]


def render(ctx: ReportContext) -> str:
    ok = sum(1 for r in ctx.results if r.ok)
    out = [
        f"# 财经热点日报 · {ctx.date}（{ctx.edition}）",
        "",
        f"> 时间窗口 {ctx.since:%m-%d %H:%M} ~ {ctx.until:%m-%d %H:%M}（北京时间）· "
        f"采集 {ctx.raw_count} 条，去重后 {len(ctx.items)} 条 · 信源 {ok}/{len(ctx.results)} 正常",
        "",
    ]
    sec = _Sections()

    if ctx.overview:
        out += ["## 要点速览", "", ctx.overview, ""]

    shown: set[str] = set()
    top = [it for it in ctx.items if it.score >= ctx.min_score][: ctx.top_n]
    out += [sec.title("今日必读"), ""]
    for i, it in enumerate(top, 1):
        out += [_entry(it, i), ""]
        shown.add(it.uid)
    if not top:
        out += ["_暂无_", ""]

    if ctx.watch and not ctx.watch.empty:
        label = "明日关注" if ctx.edition_key == "close" else "今日关注"
        out += [sec.title(f"{label}（{ctx.watch.day[5:]}）"), ""] + _render_watch(ctx.watch)

    out += [sec.title("外盘与大宗"), ""] + _render_quotes(ctx)

    snap = ctx.snapshot
    if snap and snap.ok:
        out += [sec.title(f"A股盘面（{snap.trade_date or '最近交易日'}）"), ""] + _render_ashare(snap)
        if ctx.themes:
            out += [sec.title("新闻 × 资金"), ""] + _render_themes(ctx.themes, snap)

    for name, cats in SECTIONS:
        picked = [it for it in ctx.items
                  if it.category in cats and it.uid not in shown and it.score >= ctx.min_score][: ctx.per_section]
        if not picked:
            continue
        out += [sec.title(name), ""]
        for it in picked:
            out += [_entry(it), ""]
            shown.add(it.uid)

    # 页脚：信源状态
    out += ["---", "", "<details><summary>信源状态</summary>", "",
            "| 信源 | 状态 | 条数 | 耗时 | 备注 |", "|---|---|---:|---:|---|"]
    for r in sorted(ctx.results, key=lambda r: (not r.ok, -len(r.items))):
        status = "✅" if r.ok else ("⚠️" if r.items else "❌")
        note = r.error.replace("|", "/")[:80]
        out.append(f"| {r.name} | {status} | {len(r.items)} | {r.seconds}s | {note} |")
    extra_errors = [*(snap.errors if snap else []), *(ctx.watch.errors if ctx.watch else [])]
    for e in extra_errors:
        out.append(f"| 行情/日历 | ⚠️ | - | - | {e.replace('|', '/')[:80]} |")
    out += ["", "</details>", ""]
    if ctx.llm_stats:
        s = ctx.llm_stats
        out.append(f"*LLM：{s.get('model', '')} · 分析 {s.get('analyzed', 0)} 条 · 调用 {s.get('calls', 0)} 次 · "
                   f"tokens {s.get('prompt_tokens', 0)}+{s.get('completion_tokens', 0)}*")
    else:
        out.append("*未启用 LLM：分类与排序由规则生成。*")
    out += ["", f"*生成时间 {ctx.until:%Y-%m-%d %H:%M}。内容为公开信息整理与自动生成，不构成任何投资建议。*", ""]
    return "\n".join(out)
