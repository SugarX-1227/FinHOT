"""服务器上的盘面任务：交易日收盘后算好涨停原因和信号战绩，推给网站的“盘面”页（web/modules/market）。

推送进程（python -m finhot push）每轮顺带看一眼：北京时间工作日 15:10 以后、今天还没推成功，就跑一次。
行情快照（涨停池、同花顺原因；东方财富板块连不上时用新浪的）+ 最近 30 小时的快讯作依据，和日报里的“涨停原因”
同一套规则；配了模型（LLM_*）时按依据改写一句话原因。存一份到 data/<日期>/limitup.json。
节假日（交易日期不是今天）只记一下，不推。推送失败 30 分钟后再试。

信号战绩也在这里算（finhot/backtest.py）：网站精选里的利好 / 利空 + 这里存下的涨停主线。东方财富的日 K 在服务器上
取得到（GitHub 的机器上经常被断开）；板块列表（clist）在这里取不到，用随代码发布的 sources/eastmoney_boards.json。
战绩没算成不影响涨停原因（已经推了），只在结果里说一声。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Callable

from .ashare import MarketSnapshot, fetch_snapshot
from .backtest import run_backtest
from .collect import collect
from .crosscheck import cross_check
from .dedup import dedup
from .limitup import announced, attribute, coverage, write_reasons
from .scoring import score_all
from .site_api import Site
from .timeutil import TZ, now

log = logging.getLogger(__name__)

AFTER_CLOSE = time(15, 10)
RETRY = timedelta(minutes=30)
EVIDENCE_HOURS = 30
ANNOUNCE_HOURS = 48   # 公告常在前一天晚上发


def due(at: datetime, done: str | None, tried: str | None) -> bool:
    """工作日收盘后、今天还没成功过、离上次失败超过 30 分钟。"""
    at = at.astimezone(TZ)
    if at.weekday() >= 5 or at.time() < AFTER_CLOSE or done == at.date().isoformat():
        return False
    return not tried or at - datetime.fromisoformat(tried) >= RETRY


def limitup_announcements(snap: MarketSnapshot, at: datetime, fetch=None) -> tuple[list, str]:
    """涨停股前两天的公告（按代码查，比全市场翻页省得多）。公告源都连不上时返回空列表，涨停原因照常出。"""
    from . import announce
    from .entities import book

    fetch = fetch or announce.fetch
    b = book()
    codes = {s["code"]: (b.get(s["code"]).org_id if b.get(s["code"]) else "") for s in snap.zt_stocks}
    try:
        items, results = fetch(at - timedelta(hours=ANNOUNCE_HOURS), codes)
    except Exception as e:  # noqa: BLE001
        return [], f"公告取不到：{type(e).__name__}"
    bad = [r.source_id for r in results if not r.ok]
    return items, f"公告 {len(items)} 条" + (f"，{'、'.join(bad)} 没连上" if bad else "")


def limitup_day(snap: MarketSnapshot, rows: list) -> dict:
    return {"trade_date": snap.trade_date, "coverage": coverage(rows), "announced": announced(rows), "breadth": snap.breadth,
            "ladder": snap.ladder, "stocks": [r.to_dict() for r in rows]}


def push_record(site: Site, data_dir: Path) -> str:
    """信号战绩：算好推到网站。结果文件放在 data/record/ 下（不和按日期的目录混在一起）。"""
    run_backtest(days=90, out_dir=data_dir / "record", data_dir=data_dir, site=site, push=True)
    summary = json.loads((data_dir / "record" / "data" / "backtest" / "summary.json").read_text(encoding="utf-8"))
    return "信号战绩 " + ("、".join(f"{r['kind']} {r['n']}" for r in summary["rows"]) or "暂无信号")


def run_market(site: Site, sources: list[dict], data_dir: Path, at: datetime | None = None,
               snapshot: Callable[[], MarketSnapshot] = fetch_snapshot, llm=None,
               record: Callable[[Site, Path], str] = push_record) -> str:
    """跑一次盘面任务，返回一句结果。抛异常表示没推成功（稍后再试）。"""
    at = at or now()
    today = at.astimezone(TZ).date()
    snap = snapshot()
    for e in snap.errors:
        log.info("行情快照：%s", e)
    pool_errors = [e for e in snap.errors if e.startswith("涨停池")]
    if not snap.trade_date or not snap.zt_stocks:
        raise RuntimeError(f"涨停池没取到：{pool_errors[0] if pool_errors else '接口返回空'}")
    if snap.trade_date != today.isoformat():
        return f"今天不是交易日（最近交易日 {snap.trade_date}），不推"

    raw = [it for r in collect(sources, since=at - timedelta(hours=EVIDENCE_HOURS)) for it in r.items]
    items = score_all(dedup(raw))
    anns, ann_note = limitup_announcements(snap, at)
    rows = attribute(snap, items, cross_check(items, snap) if snap.ok else [], announcements=anns)
    rewritten = write_reasons(rows, llm) if llm else 0
    day = limitup_day(snap, rows)
    ddir = data_dir / today.isoformat()
    ddir.mkdir(parents=True, exist_ok=True)
    (ddir / "limitup.json").write_text(json.dumps(day, ensure_ascii=False, indent=1), encoding="utf-8")
    site.push("limitup", snap.trade_date, day)
    msg = (f"涨停 {len(rows)} 只，有依据 {coverage(rows):.0f}%，用上公告 {announced(rows)} 只（{ann_note}），"
           f"同花顺原因 {len(snap.zt_reasons)} 条，模型改写 {rewritten} 条；依据新闻 {len(items)} 条")
    try:
        return f"{msg}；{record(site, data_dir)}"
    except Exception as e:  # noqa: BLE001 —— 涨停原因已经推了，战绩下一个交易日再算
        log.warning("信号战绩没算成：%s", e)
        return f"{msg}；信号战绩没算成：{e}"
