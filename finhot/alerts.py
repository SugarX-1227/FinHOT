"""服务器上推送进程里的个人部分（python -m finhot push 每一轮顺带跑）：

1. 到点出简报：画像里的常看时段（hours）到了、今天这一版还没出，就生成并发到画像配的群；
2. 持仓 / 自选的公告：每隔 announce_every 秒按代码查一次，类型表里 push 为 true 的（业绩预告、重组、控制权、
   监管问询、停复牌、减持……）即时提醒；
3. 持仓 / 自选成为新闻主体、排序分够 push.min_rank 的快讯：即时提醒；
4. 持仓股盘中异动：涨跌幅超过 push.move_pct，每只每天每个方向一次。

每人每天有上限，免打扰时段里的不发、记成“延后”（下一份简报照样包含）；同一条只推一次。
“新闻热·资金冷 → 共振”不做即时提醒：回测里共振的可交易口径 T+1 是 0.00%，没有证据值得为它打扰人，
而且服务器上拿不到东方财富的板块资金（只有新浪的涨幅），盘中判定会来回跳；它照常出现在简报的“关注题材”里。

个人状态（哪些出过、推过）在 FINHOT_PRIVATE_DIR/alerts-state.json，不和 data/push-state.json 混在一起。
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Callable

from . import announce, history
from .entities import book as default_book, secid
from .models import NewsItem
from .notify import feedback_links, send
from .personal import Pick, Ranker, default_weights, push_worthy
from .profile import Profile, all_profiles, in_quiet, private_dir
from .timeutil import TZ

log = logging.getLogger(__name__)
TRADING = ((time(9, 30), time(11, 30)), (time(13, 0), time(15, 0)))
OVERLAP = timedelta(minutes=10)


@dataclass
class AlertState:
    briefs: dict[str, str] = field(default_factory=dict)    # "画像:版次" → 日期
    last_ann: str | None = None                             # 上次查公告的时间
    moves: dict[str, str] = field(default_factory=dict)     # "画像:代码:涨/跌" → 日期

    @classmethod
    def path(cls) -> Path:
        return private_dir() / "alerts-state.json"

    @classmethod
    def load(cls) -> "AlertState":
        try:
            return cls(**json.loads(cls.path().read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError):
            return cls()

    def save(self) -> None:
        path = self.path()
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)


def trading(at: datetime) -> bool:
    at = at.astimezone(TZ)
    return at.weekday() < 5 and any(a <= at.time() <= b for a, b in TRADING)


# ------------------------------------------------------------------ 行情
def parse_quotes(data: dict) -> dict[str, dict]:
    """东方财富 ulist：{代码: {name, price, pct}}。"""
    out = {}
    for d in ((data.get("data") or {}).get("diff") or []):
        if d.get("f3") in (None, "-") or d.get("f2") in (None, "-"):
            continue
        out[str(d["f12"])] = {"name": d.get("f14") or "", "price": float(d["f2"]), "pct": float(d["f3"])}
    return out


def fetch_quotes(codes: list[str], session=None) -> dict[str, dict]:
    from .ashare import push2_json
    from .http import Session
    if not codes:
        return {}
    data = push2_json(session or Session(timeout=10, retries=1), "/api/qt/ulist.np/get",
                      {"fltt": "2", "secids": ",".join(secid(c) for c in codes), "fields": "f12,f14,f2,f3"})
    return parse_quotes(data)


# ------------------------------------------------------------------ 发
@dataclass
class Alert:
    profile: Profile
    text: str                  # 一行 Markdown
    pick: Pick | None = None
    id: str = ""


def _line(pk: Pick) -> str:
    it = pk.item
    title = (it.llm.get("title") or it.title).strip()
    link = f"[原文]({it.url})" if it.url else ""
    return f"- **{title}**\n  {pk.reason} · {it.published.astimezone(TZ):%H:%M} {link}"


def deliver(alerts: list[Alert], at: datetime, w: dict, sender: Callable = send) -> dict[str, int]:
    """按人合成一条消息发出去：去重、每日上限、免打扰（延后）。"""
    stats = {"sent": 0, "deferred": 0, "capped": 0, "dup": 0, "failed": 0}
    by_profile: dict[str, list[Alert]] = {}
    for a in alerts:
        by_profile.setdefault(a.profile.name, []).append(a)
    day0 = at.astimezone(TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    defaults = w.get("push") or {}
    for name, rows in by_profile.items():
        p = rows[0].profile
        if p.push.channel == "none":
            continue
        done = {r["id"] for r in history.shown(name) if r.get("channel") in ("push", "deferred")}
        rows = [a for a in rows if a.id not in done]
        stats["dup"] += len(by_profile[name]) - len(rows)
        if not rows:
            continue
        quiet = p.push.quiet or defaults.get("quiet")
        limit = int(p.push.daily_limit or defaults.get("daily_limit", 8))
        if in_quiet(at, quiet):
            _record(rows, at, "deferred")
            stats["deferred"] += len(rows)
            continue
        today = len(history.shown(name, day0, channel="push"))
        room = max(0, limit - today)
        send_now, over = rows[:room], rows[room:]
        stats["capped"] += len(over)
        if over:
            _record(over, at, "deferred")
        if not send_now:
            continue
        body = "\n".join(_with_links(a) for a in send_now)
        if over:
            body += f"\n\n<font color='grey'>今天的提醒到上限了（{limit} 条），另有 {len(over)} 条放进下一份简报。</font>"
        try:
            sender(p, f"持仓 / 自选提醒 · {at.astimezone(TZ):%m-%d %H:%M}", body
                   + "\n\n<font color='grey'>相关信息提示，不构成投资建议。</font>")
        except Exception as e:  # noqa: BLE001 —— 发不出去下一轮再试（没记账）
            log.warning("给 %s 的提醒没发出去：%s", name, e)
            stats["failed"] += len(send_now)
            continue
        _record(send_now, at, "push")
        stats["sent"] += len(send_now)
    return stats


def _with_links(a: Alert) -> str:
    links = feedback_links(a.profile.name, a.id) if a.pick else ""
    return a.text + (f"  {links}" if links else "")


def _record(rows: list[Alert], at: datetime, channel: str) -> None:
    from .personal import title_grams
    history.record_shown([history.Shown(a.profile.name, a.id, (a.pick.item.title if a.pick else a.text)[:80],
                                        sorted(title_grams(a.pick.item)) if a.pick else [], at.isoformat(),
                                        a.pick.section if a.pick else "持仓", a.pick.reason_kind if a.pick else "持仓·异动",
                                        channel, a.pick.code if a.pick else "", a.pick.item.url if a.pick else "")
                          for a in rows])


# ------------------------------------------------------------------ 一轮
def news_alerts(profiles: list[Profile], items: list[NewsItem], at: datetime, w: dict) -> list[Alert]:
    out = []
    for p in profiles:
        if not p.codes or p.push.channel == "none":
            continue
        ranker = Ranker(p, w)
        for it in items:
            pk = ranker.rank_one(it, at)
            if pk and push_worthy(pk, w):
                out.append(Alert(p, _line(pk), pk, pk.id))
    return out


def move_alerts(profiles: list[Profile], quotes: dict[str, dict], at: datetime, w: dict, state: AlertState) -> list[Alert]:
    threshold = float((w.get("push") or {}).get("move_pct", 5.0))
    day = at.astimezone(TZ).date().isoformat()
    out = []
    for p in profiles:
        if p.push.channel == "none":
            continue
        for code in p.holdings:
            q = quotes.get(code)
            if not q or abs(q["pct"]) < threshold:
                continue
            side = "涨" if q["pct"] > 0 else "跌"
            key = f"{p.name}:{code}:{side}"
            if state.moves.get(key) == day:
                continue
            state.moves[key] = day
            out.append(Alert(p, f"- **持仓 {q['name'] or code} 盘中{side} {abs(q['pct']):.2f}%**（现价 {q['price']}，"
                                f"{at.astimezone(TZ):%H:%M}）", None, f"move:{key}:{day}"))
    return out


def run_round(at: datetime, fresh_news: list[NewsItem], state: AlertState | None = None,
              profiles: list[Profile] | None = None, fetch_ann: Callable | None = None,
              quotes: Callable | None = None, run_brief: Callable | None = None, sender: Callable = send) -> dict:
    """推送进程每一轮调用一次。任何一步失败只记日志。"""
    from . import brief as brief_mod
    w = default_weights()
    state = state or AlertState.load()
    profiles = all_profiles() if profiles is None else profiles
    stats: dict = {"profiles": len(profiles)}
    if not profiles:
        return stats
    # 1. 到点的简报
    for p in profiles:
        ed = brief_mod.due(p, at, state.briefs)
        if not ed:
            continue
        try:
            (run_brief or brief_mod.run)(p, at=at, edition=ed, send=p.push.channel != "none")
            stats.setdefault("briefs", []).append(f"{p.name}:{ed}")
        except Exception as e:  # noqa: BLE001
            log.warning("给 %s 出%s简报失败：%s", p.name, ed, e)
        state.briefs[f"{p.name}:{ed}"] = at.date().isoformat()   # 失败也不在这一版反复重试（日志里看得到）
    alerts: list[Alert] = news_alerts(profiles, fresh_news, at, w)
    # 2. 公告
    every = float((w.get("push") or {}).get("announce_every", 300))
    last = datetime.fromisoformat(state.last_ann) if state.last_ann else None
    if not last or (at - last).total_seconds() >= every:
        b = default_book()
        codes = {c: (b.get(c).org_id if b.get(c) else "") for p in profiles for c in p.codes}
        if codes:
            try:
                items, _ = (fetch_ann or announce.fetch)((last or at - timedelta(hours=1)) - OVERLAP, codes)
                alerts += news_alerts(profiles, items, at, w)
                stats["announcements"] = len(items)
                state.last_ann = at.isoformat()
            except Exception as e:  # noqa: BLE001
                log.warning("查公告失败：%s", e)
    # 3. 盘中异动
    if trading(at):
        holdings = sorted({c for p in profiles for c in p.holdings})
        try:
            alerts += move_alerts(profiles, (quotes or fetch_quotes)(holdings), at, w, state)
        except Exception as e:  # noqa: BLE001
            log.warning("取持仓行情失败：%s", e)
    stats.update(deliver(alerts, at, w, sender))
    state.save()
    return stats
