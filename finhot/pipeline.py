"""完整流水线：采集 → 去重 → 规则打分 →（可选）LLM → 行情/盘面/日历 → 新闻×资金 → 日报 + 数据落盘。"""

from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path

from . import llm as llm_mod
from .ashare import MarketSnapshot, fetch_snapshot
from .calendar_watch import Watchlist, fetch_watchlist
from .collect import collect
from .config import DATA_DIR, REPORTS_DIR, load_sources
from .crosscheck import cross_check
from .dedup import dedup
from .editions import EDITIONS, edition_by_time, window_since
from .market import fetch_quotes
from .report import ReportContext, render
from .scoring import score_all
from .timeutil import now

log = logging.getLogger(__name__)


def _ashare_brief(s: MarketSnapshot) -> dict:
    """给 LLM 的盘面摘要（只放事实数据）。"""
    return {
        "trade_date": s.trade_date, "turnover_yi": s.turnover, "breadth": s.breadth,
        "ladder": s.ladder[:3], "zt_industries": s.zt_industries[:5],
        "top_industries": [{"name": b.name, "pct": b.pct, "inflow_yi": b.inflow} for b in s.industries[:5]],
        "top_concepts": [{"name": b.name, "pct": b.pct} for b in s.concepts[:5]],
    }


def run(hours: float | None = None, use_llm: bool = True, only: list[str] | None = None,
        keep: int = 400, out_dir: Path | None = None, edition: str | None = None,
        market: bool = True) -> Path:
    cfg = load_sources()
    until = now()
    ed = EDITIONS[edition] if edition else edition_by_time(until)
    data_root = (out_dir / "data") if out_dir else DATA_DIR
    # 用仓库里的历史 meta 决定窗口（--out 只影响输出位置）
    since = window_since(until, hours, DATA_DIR)
    date = until.strftime("%Y-%m-%d")

    log.info("%s · 采集窗口 %s ~ %s", ed.name, since.strftime("%m-%d %H:%M"), until.strftime("%m-%d %H:%M"))
    watch_day = until + timedelta(days=1) if ed.key == "close" else until
    with ThreadPoolExecutor(max_workers=3) as pool:
        f_quotes = pool.submit(fetch_quotes, cfg["markets"])
        f_snap = pool.submit(fetch_snapshot) if market else None
        f_watch = pool.submit(fetch_watchlist, watch_day) if market else None
        results = collect(cfg["sources"], since, only=only)
        quotes = f_quotes.result()
        snap = f_snap.result() if f_snap else MarketSnapshot()
        watch = f_watch.result() if f_watch else Watchlist()

    raw = [it for r in results for it in r.items]
    if not raw:
        raise SystemExit("所有信源都没有返回数据，请检查网络或运行 `python -m finhot sources` 排查")
    items = score_all(dedup(raw))
    log.info("原始 %d 条 → 去重后 %d 条", len(raw), len(items))

    llm_stats: dict = {}
    overview = ""
    client = llm_mod.LLMClient.from_env() if use_llm else None
    if client:
        log.info("LLM 分析中（%s）…", client.model)
        llm_stats = llm_mod.enrich(items, client)

    themes = cross_check(items, snap) if snap.ok else []

    if client:
        extra = {}
        if snap.ok:
            extra["ashare"] = _ashare_brief(snap)
        if themes:
            extra["themes"] = [{"theme": r.theme, "verdict": r.verdict, "news": r.news_count,
                                "board_pct": r.pct, "inflow_yi": r.inflow, "zt": r.zt} for r in themes]
        if not watch.empty:
            extra["watch"] = {"day": watch.day, "data": [r["title"] for r in watch.data[:8]],
                              "events": [r["title"] for r in watch.events[:8]]}
        overview = llm_mod.overview(items, quotes, client, extra=extra)
        llm_stats.update(client.usage, calls=client.calls, model=client.model)

    ctx = ReportContext(date=date, edition=ed.name, edition_key=ed.key, since=since, until=until, items=items,
                        results=results, quotes=quotes, overview=overview, raw_count=len(raw),
                        llm_stats=llm_stats, snapshot=snap, watch=watch, themes=themes)
    md = render(ctx)

    reports = out_dir or REPORTS_DIR
    reports.mkdir(parents=True, exist_ok=True)
    report_path = reports / f"{date}{ed.suffix}.md"
    report_path.write_text(md, encoding="utf-8")
    (reports / "latest.md").write_text(md, encoding="utf-8")

    # 结构化数据：供周报、回测"高分新闻 → 次日板块表现"、校准打分规则
    ddir = data_root / date
    ddir.mkdir(parents=True, exist_ok=True)
    with open(ddir / f"items{ed.suffix}.jsonl", "w", encoding="utf-8") as f:
        for it in items[:keep]:
            f.write(json.dumps(it.to_dict(max_content=300), ensure_ascii=False) + "\n")
    meta = {
        "date": date, "edition": ed.name, "edition_key": ed.key,
        "since": since.isoformat(), "until": until.isoformat(),
        "raw_count": len(raw), "dedup_count": len(items), "quotes": quotes, "llm": llm_stats,
        "ashare": snap.to_dict() if snap.ok else None,
        "watch": watch.to_dict() if not watch.empty else None,
        "themes": [{"theme": r.theme, "verdict": r.verdict, "news": r.news_count, "heat": round(r.heat, 1),
                    "pct": r.pct, "inflow": r.inflow, "zt": r.zt} for r in themes],
        "sources": [{"id": r.source_id, "name": r.name, "ok": r.ok, "count": len(r.items),
                     "pages": r.pages, "seconds": r.seconds, "error": r.error} for r in results],
    }
    (ddir / f"meta{ed.suffix}.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("日报已生成：%s", report_path)
    return report_path
