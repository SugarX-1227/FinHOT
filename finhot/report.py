"""把处理后的条目渲染成 Markdown 日报。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .fetchers import FetchResult
from .models import NewsItem

SECTIONS = [
    ("宏观与政策", ("政策", "宏观")),
    ("海外市场", ("海外",)),
    ("行业与产业", ("行业",)),
    ("公司动态", ("公司",)),
    ("市场与资金", ("市场",)),
]
CN_NUM = "一二三四五六七八九十"
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


def _brief(it: NewsItem, limit: int = 140) -> str:
    if it.llm.get("summary"):
        return it.llm["summary"]
    text = it.content
    if text.startswith(it.title.rstrip("…")):
        text = text[len(it.title.rstrip("…")):].lstrip("，,。：: ")
    text = text.strip()
    if not text or text == it.title:
        return ""
    return text if len(text) <= limit else text[:limit] + "…"


def _entry(it: NewsItem, idx: int | None = None) -> str:
    title = it.llm.get("title") or it.title
    head = f"{idx}. " if idx is not None else "- "
    flag = " 🔴" if it.important else ""
    lines = [f"{head}**{title}**{flag}"]
    brief = _brief(it)
    if brief:
        lines.append(f"   {brief}")
    if it.llm.get("direction"):
        parts = [DIR_ICON.get(it.llm["direction"], it.llm["direction"])]
        if it.llm.get("sectors"):
            parts.append("板块：" + "、".join(it.llm["sectors"]))
        if it.llm.get("stocks"):
            parts.append("个股：" + "、".join(it.llm["stocks"]))
        if it.llm.get("horizon"):
            parts.append(it.llm["horizon"])
        lines.append("   > " + " · ".join(parts))
        if it.llm.get("basis"):
            lines.append(f"   > 依据：{it.llm['basis']}")
    srcs = it.source_name + (f" 等{1 + len(it.dup_sources)}家" if it.dup_sources else "")
    meta = f"{it.published:%m-%d %H:%M} · {srcs} · {it.category} · {it.score:.1f}分"
    link = f" · [原文]({it.url})" if it.url else ""
    lines.append(f"   <sub>{meta}{link}</sub>")
    return "\n".join(lines)


def _fmt_num(v, digits: int = 2) -> str:
    if v is None or v == "-":
        return "-"
    try:
        return f"{float(v):,.{digits}f}"
    except (TypeError, ValueError):
        return str(v)


def render(ctx: ReportContext) -> str:
    ok = sum(1 for r in ctx.results if r.ok)
    out = [
        f"# 财经热点日报 · {ctx.date}（{ctx.edition}）",
        "",
        f"> 时间窗口 {ctx.since:%m-%d %H:%M} ~ {ctx.until:%m-%d %H:%M}（北京时间）· "
        f"采集 {ctx.raw_count} 条，去重后 {len(ctx.items)} 条 · 信源 {ok}/{len(ctx.results)} 正常",
        "",
    ]
    sec = 0

    if ctx.overview:
        out += ["## 盘前要点", "", ctx.overview, ""]

    sec += 1
    out += [f"## {CN_NUM[sec - 1]}、外盘与大宗", ""]
    if ctx.quotes:
        out += ["| 品种 | 最新 | 涨跌 | 涨跌幅 | 更新 |", "|---|---:|---:|---:|---|"]
        for q in ctx.quotes:
            pct = q.get("pct")
            arrow = "" if pct in (None, "-") else ("🔺" if float(pct) > 0 else "🔻" if float(pct) < 0 else "")
            out.append(f"| {q['name']} | {_fmt_num(q['price'], 4 if abs(float(q['price'])) < 20 else 2)} "
                       f"| {_fmt_num(q['change'], 4 if abs(float(q['price'])) < 20 else 2)} "
                       f"| {arrow}{_fmt_num(pct)}% | {q['time']} |")
    else:
        out.append("_行情数据暂不可用_")
    out.append("")

    shown: set[str] = set()
    top = [it for it in ctx.items if it.score >= ctx.min_score][: ctx.top_n]
    sec += 1
    out += [f"## {CN_NUM[sec - 1]}、今日必读", ""]
    for i, it in enumerate(top, 1):
        out += [_entry(it, i), ""]
        shown.add(it.uid)
    if not top:
        out += ["_暂无_", ""]

    for name, cats in SECTIONS:
        picked = [it for it in ctx.items
                  if it.category in cats and it.uid not in shown and it.score >= ctx.min_score][: ctx.per_section]
        if not picked:
            continue
        sec += 1
        out += [f"## {CN_NUM[sec - 1]}、{name}", ""]
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
    out += ["", "</details>", ""]
    if ctx.llm_stats:
        s = ctx.llm_stats
        out.append(f"*LLM：{s.get('model', '')} · 分析 {s.get('analyzed', 0)} 条 · 调用 {s.get('calls', 0)} 次 · "
                   f"tokens {s.get('prompt_tokens', 0)}+{s.get('completion_tokens', 0)}*")
    else:
        out.append("*未启用 LLM：分类与排序由规则生成。*")
    out += ["", f"*生成时间 {ctx.until:%Y-%m-%d %H:%M}。内容为公开信息整理与自动生成，不构成任何投资建议。*", ""]
    return "\n".join(out)
