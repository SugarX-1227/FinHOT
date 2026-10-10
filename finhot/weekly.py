"""周报：汇总一周的日报数据（data/<date>/items*.jsonl、meta*.json）。

内容：本周指数表现、本周十大新闻、题材热度榜（含较上周变化）、热门公司、
新闻×资金周度回顾、每日分类分布、下周关注；配置了 LLM 时另有"本周回顾/下周关注"要点。
"""

from __future__ import annotations

import json
import logging
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date as Date
from datetime import datetime, timedelta
from pathlib import Path

from . import llm as llm_mod
from .calendar_watch import fetch_calendar
from .config import DATA_DIR, REPORTS_DIR
from .dedup import dedup
from .http import Session
from .models import NewsItem
from .report import _entry
from .themes import item_themes, looks_like_company
from .timeutil import TZ, now

log = logging.getLogger(__name__)

WEEK_INDICES = [
    ("1.000001", "上证指数"), ("0.399001", "深证成指"), ("0.399006", "创业板指"), ("1.000688", "科创50"),
    ("1.000300", "沪深300"), ("1.000852", "中证1000"), ("100.HSI", "恒生指数"), ("100.NDX", "纳斯达克"),
    ("100.SPX", "标普500"),
]
SPARK = "▁▂▃▄▅▆▇█"


@dataclass
class WeekData:
    year: int
    week: int
    start: Date
    end: Date
    days: list[Date] = field(default_factory=list)
    items: list[NewsItem] = field(default_factory=list)       # 去重后
    per_day: dict[Date, list[NewsItem]] = field(default_factory=dict)
    metas: list[dict] = field(default_factory=list)
    reports: int = 0

    @property
    def label(self) -> str:
        return f"{self.year}-W{self.week:02d}"


def week_bounds(d: Date) -> tuple[Date, Date]:
    start = d - timedelta(days=d.weekday())
    return start, start + timedelta(days=6)


def load_week(d: Date, data_dir: Path = DATA_DIR) -> WeekData:
    start, end = week_bounds(d)
    y, w, _ = start.isocalendar()
    wd = WeekData(y, w, start, end)
    raw: list[NewsItem] = []
    for i in range(7):
        day = start + timedelta(days=i)
        ddir = data_dir / day.isoformat()
        if not ddir.is_dir():
            continue
        day_items: list[NewsItem] = []
        for f in sorted(ddir.glob("items*.jsonl")):
            wd.reports += 1
            for line in f.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    try:
                        day_items.append(NewsItem.from_dict(json.loads(line)))
                    except (ValueError, TypeError, KeyError):
                        continue
        for f in sorted(ddir.glob("meta*.json")):
            try:
                wd.metas.append(json.loads(f.read_text(encoding="utf-8")))
            except ValueError:
                pass
        if day_items:
            wd.days.append(day)
            raw += day_items
    # 同分时多家信源报道的排前面
    wd.items = sorted(dedup(raw), key=lambda x: (x.score, len(x.dup_sources), x.published), reverse=True)
    for it in wd.items:
        wd.per_day.setdefault(it.published.astimezone(TZ).date(), []).append(it)
    return wd


def theme_heat(items: list[NewsItem], min_score: float = 5.0) -> dict[str, dict]:
    out: dict[str, dict] = defaultdict(lambda: {"count": 0, "heat": 0.0, "top": None})
    for it in items:
        if it.score < min_score:
            continue
        for t in item_themes(it):
            r = out[t]
            r["count"] += 1
            r["heat"] += it.score
            if r["top"] is None or it.score > r["top"].score:
                r["top"] = it
    return out


def spark(values: list[float]) -> str:
    hi = max(values) if values else 0
    if hi <= 0:
        return "·" * len(values)
    return "".join(SPARK[min(len(SPARK) - 1, int(v / hi * (len(SPARK) - 1)))] if v else "·" for v in values)


def hot_stocks(items: list[NewsItem], top: int = 15) -> list[tuple[str, int, NewsItem]]:
    cnt: Counter = Counter()
    best: dict[str, NewsItem] = {}
    for it in items:
        if it.score < 4:
            continue
        names = set(it.stocks) | set((it.llm or {}).get("stocks") or [])
        for n in names:
            if not looks_like_company(n):
                continue
            cnt[n] += 1
            if n not in best or it.score > best[n].score:
                best[n] = it
    return [(n, c, best[n]) for n, c in cnt.most_common(top) if c >= 2]


def capital_review(metas: list[dict]) -> list[dict]:
    """把每期日报的"新闻×资金"结论按题材汇总。"""
    agg: dict[str, Counter] = defaultdict(Counter)
    for m in metas:
        for r in m.get("themes") or []:
            agg[r["theme"]][r["verdict"]] += 1
    rows = [{"theme": t, **c, "total": sum(c.values())} for t, c in agg.items()]
    rows.sort(key=lambda r: (-r.get("共振", 0), -r["total"]))
    return rows[:10]


def indices_from_metas(wd: WeekData) -> list[dict]:
    """用每期日报 meta 里存的 A 股指数日涨跌幅复利出周涨跌幅（周 K 接口不可用时的兜底）。"""
    by_day: dict[str, dict] = {}
    for m in wd.metas:
        a = m.get("ashare") or {}
        td = a.get("trade_date") or ""
        if td and wd.start.isoformat() <= td <= wd.end.isoformat():
            by_day[td] = {r["name"]: r for r in a.get("indices") or []}
    if not by_day:
        return []
    names = [n for _, n in WEEK_INDICES if any(n in d for d in by_day.values())]
    out = []
    for n in names:
        acc, close = 1.0, None
        for td in sorted(by_day):
            r = by_day[td].get(n)
            if r and r.get("pct") is not None:
                acc *= 1 + float(r["pct"]) / 100
                close = r.get("price")
        if close is not None:
            out.append({"name": n, "close": float(close), "pct": round((acc - 1) * 100, 2)})
    return out


def fetch_week_indices(wd: WeekData, session: Session | None = None) -> list[dict]:
    from .ashare import PUSH2HIS_HOSTS, push2_json
    session = session or Session()
    out = []
    for secid, name in WEEK_INDICES:
        try:
            data = push2_json(session, "/api/qt/stock/kline/get", {
                "secid": secid, "fields1": "f1,f2,f3", "fields2": "f51,f52,f53,f54,f55,f56,f57,f59",
                "klt": "102", "fqt": "1", "end": (wd.end + timedelta(days=1)).strftime("%Y%m%d"), "lmt": "2",
                "ut": "fa5fd1943c7b386f172d6893dbfba10b"}, hosts=PUSH2HIS_HOSTS)
            for k in reversed(((data.get("data") or {}).get("klines")) or []):
                f = k.split(",")
                if wd.start.isoformat() <= f[0] <= wd.end.isoformat():
                    out.append({"name": name, "close": float(f[2]), "pct": float(f[7])})
                    break
        except Exception as e:  # noqa: BLE001
            log.warning("周 K 获取失败 %s: %s", name, e)
    if not out:
        out = indices_from_metas(wd)
        if out:
            log.info("周 K 接口不可用，改用日报数据复利计算 A 股指数周涨跌幅")
    return out


def render_weekly(wd: WeekData, indices: list[dict], prev: WeekData | None, next_cal: tuple[list, list],
                  overview: str = "", llm_stats: dict | None = None) -> str:
    out = [f"# 财经周报 · {wd.year} 年第 {wd.week} 周（{wd.start:%m-%d} ~ {wd.end:%m-%d}）", "",
           f"> 汇总本周 {wd.reports} 期日报 · 去重后 {len(wd.items)} 条新闻 · 生成于 {now():%Y-%m-%d %H:%M}", ""]
    n = 0

    def sec(title: str) -> list[str]:
        nonlocal n
        n += 1
        return [f"## {'一二三四五六七八九十'[n - 1]}、{title}", ""]

    if overview:
        out += ["## 本周要点", "", overview, ""]

    if indices:
        out += sec("本周市场表现")
        out += ["| 指数 | 周收盘 | 周涨跌幅 |", "|---|---:|---:|"]
        for r in indices:
            arrow = "🔺" if r["pct"] > 0 else "🔻" if r["pct"] < 0 else ""
            out.append(f"| {r['name']} | {r['close']:,.2f} | {arrow}{r['pct']:+.2f}% |")
        out.append("")

    out += sec("本周十大新闻")
    for i, it in enumerate([x for x in wd.items if x.score >= 5][:10], 1):
        out += [_entry(it, i), ""]

    heat = theme_heat(wd.items)
    prev_heat = theme_heat(prev.items) if prev and prev.items else {}
    if heat:
        out += sec("题材热度榜")
        days = [wd.start + timedelta(days=i) for i in range(7)]
        out += ["| 题材 | 新闻数 | 热度 | 较上周 | 每日走势（一→日） | 代表新闻 |", "|---|---:|---:|---:|---|---|"]
        ranked = sorted(heat.items(), key=lambda kv: -kv[1]["heat"])[:15]
        for t, r in ranked:
            daily = [sum(1 for it in wd.per_day.get(d, []) if it.score >= 5 and t in item_themes(it)) for d in days]
            p = prev_heat.get(t, {}).get("heat")
            change = "新上榜" if not p else f"{(r['heat'] / p - 1) * 100:+.0f}%"
            top = r["top"]
            title = ((top.llm or {}).get("title") or top.title)[:30] if top else ""
            out.append(f"| {t} | {r['count']} | {r['heat']:.0f} | {change} | `{spark(daily)}` | {title} |")
        out.append("")

    stocks = hot_stocks(wd.items)
    if stocks:
        out += sec("热门公司（新闻提及次数）")
        out += ["| 公司 | 提及 | 代表新闻 |", "|---|---:|---|"]
        for name, c, it in stocks:
            out.append(f"| {name} | {c} | {((it.llm or {}).get('title') or it.title)[:40]} |")
        out.append("")

    review = capital_review(wd.metas)
    if review:
        out += sec("新闻 × 资金 周度回顾")
        out += ["> 统计本周各期日报中每个题材被判定为共振/资金先行/新闻热·资金冷的次数。", "",
                "| 题材 | 🔥共振 | 💰资金先行 | 🧊新闻热·资金冷 |", "|---|---:|---:|---:|"]
        for r in review:
            out.append(f"| {r['theme']} | {r.get('共振', 0)} | {r.get('资金先行', 0)} | {r.get('新闻热·资金冷', 0)} |")
        out.append("")

    cats = ["政策", "宏观", "海外", "市场", "行业", "公司"]
    if wd.per_day:
        out += sec("每日分类分布（得分≥4）")
        out += ["| 日期 | " + " | ".join(cats) + " | 合计 |", "|---|" + "---:|" * (len(cats) + 1)]
        for d in sorted(wd.per_day):
            if not wd.start <= d <= wd.end:
                continue
            c = Counter(it.category for it in wd.per_day[d] if it.score >= 4)
            out.append(f"| {d:%m-%d} 周{'一二三四五六日'[d.weekday()]} | " + " | ".join(str(c.get(k, 0)) for k in cats)
                       + f" | {sum(c.values())} |")
        out.append("")

    rows, evs = next_cal
    if rows or evs:
        out += sec("下周关注")
        if rows:
            out += ["| 日期 | 时间 | 国家/地区 | 指标 | 预期 | 前值 |", "|---|---|---|---|---:|---:|"]
            for r in rows[:15]:
                out.append(f"| {r['date']} | {r['time']} | {r['country']} | {r['title']} | {r['forecast']} | {r['previous']} |")
            out.append("")
        for r in evs[:12]:
            out.append(f"- {r['date']} 【{r['country']}】{r['title']}")
        out.append("")

    out.append("---")
    if llm_stats:
        out.append(f"*LLM：{llm_stats.get('model', '')} · 调用 {llm_stats.get('calls', 0)} 次*")
    out += ["", "*内容为公开信息整理与自动生成，不构成任何投资建议。*", ""]
    return "\n".join(out)


def run_weekly(day: Date | None = None, use_llm: bool = True, out_dir: Path | None = None,
               data_dir: Path = DATA_DIR) -> Path:
    day = day or now().date()
    wd = load_week(day, data_dir)
    if not wd.items:
        raise SystemExit(f"{wd.label} 没有可用的日报数据（data/ 目录下没有这一周的 items*.jsonl）")
    prev = load_week(day - timedelta(days=7), data_dir)
    indices = fetch_week_indices(wd)
    nxt = datetime.combine(wd.end + timedelta(days=1), datetime.min.time(), TZ)
    try:
        cal = fetch_calendar(nxt, nxt + timedelta(days=5), min_importance=4)
    except Exception as e:  # noqa: BLE001
        log.warning("下周日历获取失败: %s", e)
        cal = ([], [])

    overview, stats = "", {}
    client = llm_mod.LLMClient.from_env() if use_llm else None
    if client:
        heat = theme_heat(wd.items)
        prev_heat = theme_heat(prev.items) if prev.items else {}
        payload = {
            "top_news": [{"title": (it.llm or {}).get("title") or it.title,
                          "summary": (it.llm or {}).get("summary") or it.content[:120], "category": it.category}
                         for it in wd.items[:25]],
            "indices": indices,
            "themes": [{"theme": t, "count": r["count"], "heat": round(r["heat"]),
                        "prev_heat": round(prev_heat.get(t, {}).get("heat", 0))}
                       for t, r in sorted(heat.items(), key=lambda kv: -kv[1]["heat"])[:12]],
            "capital": capital_review(wd.metas),
            "next_week": [r["title"] for r in [*cal[0], *cal[1]][:15]],
        }
        try:
            text = client.chat(llm_mod._load_prompt("weekly.md"), json.dumps(payload, ensure_ascii=False, default=str),
                               max_tokens=3000)
            lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
            overview = "\n".join(ln if ln.startswith(("-", "【")) else f"- {ln}" for ln in lines[:12])
        except Exception as e:  # noqa: BLE001
            log.warning("LLM 周报要点生成失败: %s", e)
        stats = {"model": client.model, "calls": client.calls}

    md = render_weekly(wd, indices, prev, cal, overview, stats)
    wdir = (out_dir or REPORTS_DIR) / "weekly"
    wdir.mkdir(parents=True, exist_ok=True)
    path = wdir / f"{wd.label}.md"
    path.write_text(md, encoding="utf-8")
    (wdir / "latest.md").write_text(md, encoding="utf-8")
    log.info("周报已生成：%s", path)
    return path


def weekly_exists(day: Date, reports_dir: Path = REPORTS_DIR) -> bool:
    start, _ = week_bounds(day)
    y, w, _ = start.isocalendar()
    return (reports_dir / "weekly" / f"{y}-W{w:02d}.md").exists()
