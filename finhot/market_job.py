"""服务器上的盘面任务：交易日收盘后算好涨停原因和信号战绩，推给网站的“盘面”页（web/modules/market）。

推送进程（python -m finhot push）每轮顺带看一眼：北京时间工作日 15:10 以后、今天还没推成功，就跑一次。
- 涨停原因：行情快照（涨停池、同花顺原因、板块）+ 最近 30 小时的快讯作依据，和日报里的“涨停原因”同一套规则；
  配了模型（LLM_*）时按依据改写一句话原因。存一份到 data/<日期>/limitup.json，作为“涨停主线”信号的历史；
- 信号战绩：网站精选里 A股影响写了利好 / 利空的条目（读 /api/v1/selected/snapshot），加上历史的涨停主线，
  对应题材之后几天相对中证 1000 的表现（finhot/backtest.py）。
节假日（交易日期不是今天）只记一下，不推。推送失败 30 分钟后再试。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any, Callable

import requests

from .ashare import MarketSnapshot, fetch_snapshot
from .backtest import PriceSource, Signals, compute, load_signals, record_payload
from .collect import collect
from .crosscheck import cross_check
from .dedup import dedup
from .limitup import attribute, coverage, write_reasons
from .scoring import score_all
from .themes import board_themes, themes_in
from .timeutil import TZ, now

log = logging.getLogger(__name__)

AFTER_CLOSE = time(15, 10)
RETRY = timedelta(minutes=30)
EVIDENCE_HOURS = 30
RECORD_DAYS = 90

_REASON = re.compile(r"^\s*(利好|利空|中性)\s*(.*)$", re.S)


def due(at: datetime, done: str | None, tried: str | None) -> bool:
    """工作日收盘后、今天还没成功过、离上次失败超过 30 分钟。"""
    at = at.astimezone(TZ)
    if at.weekday() >= 5 or at.time() < AFTER_CLOSE or done == at.date().isoformat():
        return False
    return not tried or at - datetime.fromisoformat(tried) >= RETRY


def selected_signals(items: list[dict[str, Any]], into: Signals) -> int:
    """精选里“A股影响”写了利好 / 利空的条目 → 题材信号。A股影响的格式：“利好 半导体、存储，依据……”。"""
    n = 0
    for it in items:
        m = _REASON.match(str(it.get("reason") or ""))
        published = it.get("publishedAt") or it.get("discoveredAt")
        if not m or m.group(1) == "中性" or not published:
            continue
        targets = re.split(r"[，,。；;]", m.group(2), maxsplit=1)[0]
        themes: list[str] = []
        for t in re.split(r"[、/／\s]+", targets):
            if t:
                themes += board_themes(t) or themes_in(t)
        themes += themes_in(str(it.get("title") or ""))
        for theme in list(dict.fromkeys(themes))[:2]:
            into.add(f"精选{m.group(1)}", datetime.fromisoformat(published.replace("Z", "+00:00")), theme,
                     str(it.get("title") or "")[:60])
            n += 1
    return n


@dataclass
class Engine:
    """网站引擎的接口：读精选快照、推盘面数据。"""
    url: str
    token: str
    session: requests.Session

    def selected(self, pages: int = 20) -> list[dict[str, Any]]:
        out, page = [], None
        for _ in range(pages):
            params = {"limit": "500", **({"page": page} if page else {})}
            res = self.session.get(f"{self.url}/api/v1/selected/snapshot", params=params, timeout=30)
            res.raise_for_status()
            body = res.json()
            out += body.get("items") or []
            page = body.get("nextPage")
            if not body.get("hasMore") or not page:
                break
        return out

    def push(self, kind: str, key: str, data: dict) -> None:
        res = self.session.post(f"{self.url}/api/finhot/market", json={"kind": kind, "key": key, "data": data},
                                headers={"Authorization": f"Bearer {self.token}"}, timeout=60)
        if res.status_code == 404:
            raise RuntimeError("网站还没有“盘面”模块（先 deploy/finhot.sh update 更新网站）")
        res.raise_for_status()


def limitup_day(snap: MarketSnapshot, rows: list) -> dict:
    return {"trade_date": snap.trade_date, "coverage": coverage(rows), "breadth": snap.breadth,
            "ladder": snap.ladder, "stocks": [r.to_dict() for r in rows]}


def run_market(engine: Engine, sources: list[dict], data_dir: Path, at: datetime | None = None,
               snapshot: Callable[[], MarketSnapshot] = fetch_snapshot, prices: PriceSource | None = None,
               llm=None) -> str:
    """跑一次盘面任务，返回一句结果。抛异常表示没推成功（稍后再试）。"""
    at = at or now()
    today = at.astimezone(TZ).date()
    snap = snapshot()
    if snap.trade_date != today.isoformat():
        return f"今天不是交易日（最近交易日 {snap.trade_date or '未知'}），不推"

    pushed = []
    if snap.zt_stocks:
        raw = [it for r in collect(sources, since=at - timedelta(hours=EVIDENCE_HOURS)) for it in r.items]
        items = score_all(dedup(raw))
        rows = attribute(snap, items, cross_check(items, snap) if snap.ok else [])
        if llm:
            write_reasons(rows, llm)
        day = limitup_day(snap, rows)
        ddir = data_dir / today.isoformat()
        ddir.mkdir(parents=True, exist_ok=True)
        (ddir / "limitup.json").write_text(json.dumps(day, ensure_ascii=False, indent=1), encoding="utf-8")
        engine.push("limitup", snap.trade_date, day)
        pushed.append(f"涨停 {len(rows)} 只（有依据 {coverage(rows):.0f}%）")

    start = today - timedelta(days=RECORD_DAYS)
    signals = Signals()
    n_selected = selected_signals(engine.selected(), signals)
    load_signals(data_dir, since=start, into=signals)  # 服务器上存下的涨停主线
    picked = [s for s in signals.values() if s.at.astimezone(TZ).date() >= start]
    outcomes, errors = compute(picked, prices or PriceSource(), at)
    for e in errors[:5]:
        log.warning("战绩：%s", e)
    engine.push("record", today.isoformat(), record_payload(outcomes, at, start))
    pushed.append(f"战绩 {len(outcomes)} 个信号（精选 {n_selected} 条）")
    return "；".join(pushed)
