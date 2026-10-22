"""生成题材档案卡：知识层（topics/*.yaml）+ 问财数据 + FinHOT 本地数据 → reports/topics/<slug>.md。

每次有问财数据时，把成分股快照存到 data/topics/<日期>/<slug>.json：概念成分、涨幅、主营只有“当前值”，
每天存一份，复盘库以后才有历史可查。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from ..config import DATA_DIR, REPORTS_DIR
from ..timeutil import now
from ..wencai import SECTORS, STOCKS, Wencai, WencaiError
from . import local
from .fetch import Q_INDEX, Q_MARKET, WencaiData, collect
from .kb import TOPICS_DIR, Topic, all_topics, find
from .render import render
from .stage import Stage, judge

log = logging.getLogger(__name__)
OUT_DIR = REPORTS_DIR / "topics"


@dataclass
class Result:
    topic: Topic
    path: Path
    stage: Stage
    wencai: WencaiData | None = None
    errors: list[str] = field(default_factory=list)


def gather_local(topic: Topic, data_dir: Path = DATA_DIR, today: date | None = None) -> dict:
    items = local.news(topic, data_dir, days=30, min_score=5.0, today=today)
    codes = {s.code for s in topic.stocks() if s.code}
    return {"news": items, "timeline": local.timeline(items), "heat": local.heat(items, today),
            "limitups": local.limitups(topic, data_dir, 20, codes, today),
            "boards": local.boards(topic, data_dir, 20, today)}


def snapshot(topic: Topic, wd: WencaiData, stage: Stage, at: datetime, data_dir: Path = DATA_DIR) -> Path:
    path = data_dir / "topics" / at.strftime("%Y-%m-%d") / f"{topic.slug}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    keep = {"at": at.isoformat(timespec="minutes"), "topic": topic.title, "stage": stage.name,
            "metrics": stage.metrics, "total": wd.total, "breadth": wd.breadth,
            "index": wd.to_dict()["index"], "bench": wd.to_dict()["bench"],
            "members": [{k: v for k, v in m.items() if v not in (None, "", [])}
                        for m in wd.to_dict()["members"]]}
    path.write_text(json.dumps(keep, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def build(topic: Topic, client: Wencai | None = None, data_dir: Path = DATA_DIR, out_dir: Path = OUT_DIR,
          at: datetime | None = None, bench_cache: dict | None = None) -> Result:
    at = at or now()
    loc = gather_local(topic, data_dir, at.date())
    wd = collect(topic, client, bench_cache) if client else None
    st = judge(wd, loc["heat"])
    if wd and (wd.members or wd.index):
        note = f"行情：问财（{at:%m-%d %H:%M} 取数，盘后为当日收盘）"
    elif wd:
        note = "行情：问财取数失败，见文末数据说明"
    else:
        note = ""
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{topic.slug}.md"
    path.write_text(render(topic, at, wd, st, loc, note), encoding="utf-8")
    if wd and wd.members:
        snapshot(topic, wd, st, at, data_dir)
    return Result(topic, path, st, wd, list(wd.errors) if wd else [])


def write_index(results: list[Result], out_dir: Path = OUT_DIR, at: datetime | None = None) -> Path:
    """reports/topics/index.md：所有题材一览（静态站“题材”页用）。读已有档案卡，没重跑的题材也列出来。"""
    at = at or now()
    rows = []
    for t in all_topics():
        p = out_dir / f"{t.slug}.md"
        stage, when = "—", "—"
        if p.exists():
            head = p.read_text(encoding="utf-8")[:600]
            for part in head.split("<!-- topic:", 1)[-1].split("-->", 1)[0].split():
                if part.startswith("stage="):
                    stage = part[6:]
                if part.startswith("at="):
                    when = part[3:]
        rows.append(f"| [{t.title}]({t.slug}.md) | {stage} | {str(t.get('one_liner', '')).split('。')[0]}。 | {when} |")
    text = "\n".join(["# 题材档案", "", f"> 更新于 {at:%Y-%m-%d %H:%M}。阶段按收盘数据判断，见各档案“炒到哪了”一节。", "",
                      "| 题材 | 阶段 | 一句话 | 档案日期 |", "| --- | --- | --- | --- |", *rows, ""])
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "index.md"
    path.write_text(text, encoding="utf-8")
    return path


def build_all(names: list[str] | None = None, client: Wencai | None = None, data_dir: Path = DATA_DIR,
              out_dir: Path = OUT_DIR, at: datetime | None = None) -> list[Result]:
    at = at or now()
    topics = [find(n) for n in names] if names else all_topics()
    missing = [n for n, t in zip(names or [], topics) if t is None]
    if missing:
        raise SystemExit(f"找不到题材：{'、'.join(missing)}（topics/ 下有：{'、'.join(t.title for t in all_topics())}）")
    bench: dict = {}
    results = []
    for t in topics:
        r = build(t, client, data_dir, out_dir, at, bench)
        results.append(r)
        if r.wencai and r.wencai.fatal:
            log.warning("问财停止（%s），后面的题材只用本地数据", r.wencai.fatal)
            client = None
    write_index(results, out_dir, at)
    return results


def probe(topic: Topic, client: Wencai) -> list[str]:
    """检查问财返回的列名：只取少量数据（约 4 次调用），把每个问句返回的列和第一行打印出来。不打印密钥。"""
    u, idx = topic.get("wencai")["universe"], topic.get("wencai")["index"]
    lines = []
    for label, fn in (("成分股行情", lambda: client.query(Q_MARKET.format(u=u), STOCKS, limit=3)),
                      ("题材指数", lambda: client.query(Q_INDEX.format(i=idx), SECTORS, limit=3)),
                      ("站上20日线家数", lambda: client.count(f"{u}，收盘价大于20日均线")),
                      ("新闻", lambda: client.search(topic.get("wencai")["search"], "news", 3))):
        try:
            res = fn()
        except WencaiError as e:
            lines.append(f"[{label}] 失败：{e}")
            continue
        if isinstance(res, int):
            lines.append(f"[{label}] code_count = {res}")
        elif isinstance(res, list):
            lines.append(f"[{label}] {len(res)} 条：" + "；".join(f"{h.published[:10]} {h.title[:30]}" for h in res))
        else:
            lines.append(f"[{label}] 问句：{res.query}")
            lines.append(f"    共 {res.total} 条，本页 {len(res.rows)} 行，列：{res.columns}")
            if res.rows:
                lines.append(f"    第一行：{json.dumps(res.rows[0], ensure_ascii=False)[:600]}")
    lines.append(f"真实调用 {client.calls} 次，命中缓存 {client.cache_hits} 次")
    return lines


__all__ = ["build", "build_all", "probe", "write_index", "gather_local", "OUT_DIR", "TOPICS_DIR"]
