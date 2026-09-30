"""完整流水线：采集 → 去重 → 规则打分 →（可选）LLM → 行情 → 日报 + 数据落盘。"""

from __future__ import annotations

import json
import logging
from datetime import timedelta
from pathlib import Path

from . import llm as llm_mod
from .collect import collect
from .config import DATA_DIR, REPORTS_DIR, load_sources
from .dedup import dedup
from .market import fetch_quotes
from .report import ReportContext, render
from .scoring import score_all
from .timeutil import now

log = logging.getLogger(__name__)


def edition_of(hour: int, minute: int) -> tuple[str, str]:
    """按运行时刻决定版次：返回 (显示名, 文件名后缀)。"""
    t = hour * 60 + minute
    if t < 9 * 60 + 30:
        return "盘前版", ""
    if t < 15 * 60:
        return "盘中版", "-noon"
    return "盘后版", "-close"


def run(hours: float = 24, use_llm: bool = True, only: list[str] | None = None,
        keep: int = 400, out_dir: Path | None = None) -> Path:
    cfg = load_sources()
    until = now()
    since = until - timedelta(hours=hours)
    date = until.strftime("%Y-%m-%d")
    edition, suffix = edition_of(until.hour, until.minute)

    log.info("采集窗口 %s ~ %s", since.strftime("%m-%d %H:%M"), until.strftime("%m-%d %H:%M"))
    results = collect(cfg["sources"], since, only=only)
    raw = [it for r in results for it in r.items]
    if not raw:
        raise SystemExit("所有信源都没有返回数据，请检查网络或运行 `python -m finhot sources` 排查")

    items = score_all(dedup(raw))
    log.info("原始 %d 条 → 去重后 %d 条", len(raw), len(items))

    quotes = fetch_quotes(cfg["markets"])

    llm_stats: dict = {}
    overview = ""
    client = llm_mod.LLMClient.from_env() if use_llm else None
    if client:
        log.info("LLM 分析中（%s）…", client.model)
        llm_stats = llm_mod.enrich(items, client)
        overview = llm_mod.overview(items, quotes, client)
        llm_stats.update(client.usage, calls=client.calls, model=client.model)

    ctx = ReportContext(date=date, edition=edition, since=since, until=until, items=items,
                        results=results, quotes=quotes, overview=overview,
                        raw_count=len(raw), llm_stats=llm_stats)
    md = render(ctx)

    reports = out_dir or REPORTS_DIR
    reports.mkdir(parents=True, exist_ok=True)
    report_path = reports / f"{date}{suffix}.md"
    report_path.write_text(md, encoding="utf-8")
    (reports / "latest.md").write_text(md, encoding="utf-8")

    # 结构化数据：供回测"高分新闻 → 次日板块表现"、校准打分规则
    ddir = (out_dir / "data" if out_dir else DATA_DIR) / date
    ddir.mkdir(parents=True, exist_ok=True)
    with open(ddir / f"items{suffix}.jsonl", "w", encoding="utf-8") as f:
        for it in items[:keep]:
            f.write(json.dumps(it.to_dict(max_content=300), ensure_ascii=False) + "\n")
    meta = {
        "date": date, "edition": edition, "since": since.isoformat(), "until": until.isoformat(),
        "raw_count": len(raw), "dedup_count": len(items), "quotes": quotes, "llm": llm_stats,
        "sources": [{"id": r.source_id, "name": r.name, "ok": r.ok, "count": len(r.items),
                     "pages": r.pages, "seconds": r.seconds, "error": r.error} for r in results],
    }
    (ddir / f"meta{suffix}.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("日报已生成：%s", report_path)
    return report_path
