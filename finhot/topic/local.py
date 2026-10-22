"""FinHOT 自己存下的数据：data/<日期>/ 里的快讯（items*.jsonl）、涨停原因（limitup*.json）、板块（meta*.json）。

不花问财额度、不联网，所以没有问财 key 也能出一版档案卡。局限：快讯从 2026-09-30 才开始积累；
板块只存了当天涨幅前后各 30 个，题材板块没进榜的日子就没有记录。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from ..config import DATA_DIR
from ..timeutil import now
from .kb import Topic

DAY_DIR = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass
class News:
    published: str     # ISO 时间
    title: str
    source: str
    url: str
    score: float
    dup: int = 1

    @property
    def day(self) -> str:
        return self.published[:10]


def _day_dirs(data_dir: Path, since: date) -> list[Path]:
    return sorted(p for p in data_dir.iterdir() if p.is_dir() and DAY_DIR.match(p.name)
                  and p.name >= since.isoformat()) if data_dir.exists() else []


def _norm(title: str) -> str:
    return re.sub(r"[\s，。：:、！!？?“”\"'（）()【】\[\]—-]", "", title)[:18]


def news(topic: Topic, data_dir: Path = DATA_DIR, days: int = 30, min_score: float = 5.0,
         today: date | None = None) -> list[News]:
    """命中题材关键词的快讯（标题里命中才算，正文顺带提一句不算），按时间排序、跨文件去重。"""
    kw, ex = topic.pattern("keywords"), topic.pattern("exclude")
    since = (today or now().date()) - timedelta(days=days)
    seen_uid, seen_title, out = set(), set(), []
    for d in _day_dirs(data_dir, since - timedelta(days=3)):
        for f in sorted(d.glob("items*.jsonl")):
            for line in f.read_text(encoding="utf-8").splitlines():
                try:
                    it = json.loads(line)
                except ValueError:
                    continue
                if it.get("uid") in seen_uid:
                    continue
                seen_uid.add(it.get("uid"))
                title = (it.get("llm") or {}).get("title") or it.get("title") or ""
                if it.get("score", 0) < min_score or it.get("published", "")[:10] < since.isoformat():
                    continue
                if not kw.search(title) or (ex and ex.search(title)):
                    continue
                key = _norm(title)
                if key in seen_title:
                    continue
                seen_title.add(key)
                out.append(News(it["published"], title, it.get("source_name", ""), it.get("url", ""),
                                round(float(it.get("score", 0)), 1), int(it.get("dup_count", 1))))
    return sorted(out, key=lambda n: n.published)


def timeline(items: list[News], per_day: int = 2, limit: int = 14, min_score: float = 6.5) -> list[News]:
    """催化时间线：每天取分最高的几条，新的在前。"""
    by_day: dict[str, list[News]] = {}
    for n in items:
        if n.score >= min_score:
            by_day.setdefault(n.day, []).append(n)
    picked = [n for d in sorted(by_day, reverse=True)
              for n in sorted(by_day[d], key=lambda x: -x.score)[:per_day]]
    return picked[:limit]


def heat(items: list[News], today: date | None = None, recent: int = 5, base: int = 15) -> dict:
    """快讯热度：最近 recent 天的日均条数 vs 之前 base 天的日均条数（自然日）。"""
    today = today or now().date()
    r0 = today - timedelta(days=recent)
    b0 = r0 - timedelta(days=base)
    rec = sum(1 for n in items if n.day > r0.isoformat())
    bas = sum(1 for n in items if b0.isoformat() < n.day <= r0.isoformat())
    daily = {}
    for n in items:
        daily[n.day] = daily.get(n.day, 0) + 1
    return {"recent": rec / recent, "base": bas / base if base else 0.0, "recent_days": recent,
            "base_days": base, "daily": dict(sorted(daily.items()))}


def _prefer_close(d: Path, stem: str) -> Path | None:
    for name in (f"{stem}-close.json", f"{stem}.json"):
        if (d / name).exists():
            return d / name
    return None


def limitups(topic: Topic, data_dir: Path = DATA_DIR, days: int = 20, codes: set[str] | None = None,
             today: date | None = None) -> list[dict]:
    """FinHOT 涨停原因记录里属于这个题材的涨停股：原因/同花顺原因/行业命中关键词，或代码在产业链名单里。"""
    kw, ex = topic.pattern("keywords"), topic.pattern("exclude")
    codes = codes or set()
    since = (today or now().date()) - timedelta(days=days)
    out, seen = [], set()
    # 新的目录在前：同一交易日既有盘后文件，也可能被第二天盘前版再存一次，保留先读到的（较新的）那份
    for d in reversed(_day_dirs(data_dir, since)):
        f = _prefer_close(d, "limitup")
        if not f:
            continue
        try:
            day = json.loads(f.read_text(encoding="utf-8"))
        except ValueError:
            continue
        trade_date = day.get("trade_date") or d.name
        for s in day.get("stocks") or []:
            # 只看 FinHOT 归纳的一句话原因（没有才用同花顺原因）：同花顺原因常把几个热门概念并列，太宽
            text = str(s.get("reason") or s.get("ths_reason") or "")
            hit = s.get("code") in codes or (kw.search(text) and not (ex and ex.search(text)))
            if hit and (trade_date, s.get("code")) not in seen:
                seen.add((trade_date, s.get("code")))
                out.append({"date": trade_date, "code": s.get("code", ""),
                            "name": s.get("name", ""), "boards": s.get("boards", 1), "stat": s.get("stat", ""),
                            "reason": s.get("reason") or s.get("ths_reason") or ""})
    return sorted(out, key=lambda z: (z["date"], z.get("boards") or 1), reverse=True)


def boards(topic: Topic, data_dir: Path = DATA_DIR, days: int = 20, today: date | None = None) -> list[dict]:
    """当天进了涨幅榜前后 30 名的相关板块（东方财富行业/概念）。"""
    pat = topic.pattern("boards")
    if not pat:
        return []
    since = (today or now().date()) - timedelta(days=days)
    out, seen = [], set()
    for d in reversed(_day_dirs(data_dir, since)):
        f = _prefer_close(d, "meta")
        if not f:
            continue
        try:
            a = json.loads(f.read_text(encoding="utf-8")).get("ashare") or {}
        except ValueError:
            continue
        if not a.get("trade_date"):
            continue
        for b in [*(a.get("concepts") or []), *(a.get("industries") or [])]:
            key = (a["trade_date"], b.get("name"))
            same = (a["trade_date"], b.get("pct"), b.get("inflow"), b.get("leader"))  # 保险Ⅱ / 保险Ⅲ 这种同一组数
            if pat.search(b.get("name", "")) and key not in seen and same not in seen:
                seen.update((key, same))
                out.append({"date": a["trade_date"], "name": b["name"], "pct": b.get("pct"),
                            "inflow": b.get("inflow"), "leader": b.get("leader", ""), "kind": b.get("kind", "")})
    return sorted(out, key=lambda x: x["date"])


def market_turnover(data_dir: Path = DATA_DIR, days: int = 30, today: date | None = None) -> dict[str, float]:
    """两市成交额（亿元），按交易日。"""
    since = (today or now().date()) - timedelta(days=days)
    out = {}
    for d in _day_dirs(data_dir, since):
        f = _prefer_close(d, "meta")
        if not f:
            continue
        try:
            a = json.loads(f.read_text(encoding="utf-8")).get("ashare") or {}
        except ValueError:
            continue
        if a.get("trade_date") and a.get("turnover"):
            out[a["trade_date"]] = float(a["turnover"])
    return out
