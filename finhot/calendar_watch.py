"""今日关注：经济数据日历、重要事件、新股申购/上市、限售解禁。

- 经济数据与事件：华尔街见闻财经日历（importance 1~4，FD=数据，FE=事件）
- 新股、解禁：东方财富数据中心
盘前/午间版看"今天"，盘后版看"明天"。
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta

from .http import Session
from .timeutil import TZ, from_ts

log = logging.getLogger(__name__)
WSCN_CAL = "https://api-one-wscn.awtmt.com/apiv1/finance/macrodatas"
DATACENTER = "https://datacenter-web.eastmoney.com/api/data/v1/get"


@dataclass
class Watchlist:
    day: str = ""
    data: list[dict] = field(default_factory=list)     # 经济数据
    events: list[dict] = field(default_factory=list)   # 重要事件
    ipo_apply: list[dict] = field(default_factory=list)
    ipo_list: list[dict] = field(default_factory=list)
    lifts: list[dict] = field(default_factory=list)    # 未来一周大额解禁
    errors: list[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not (self.data or self.events or self.ipo_apply or self.ipo_list or self.lifts)

    def to_dict(self) -> dict:
        return asdict(self)


def parse_wscn_calendar(data: dict, min_importance: int = 3) -> tuple[list[dict], list[dict]]:
    rows, evs = [], []
    for i in ((data.get("data") or {}).get("items") or []):
        if int(i.get("importance") or 0) < min_importance:
            continue
        t = from_ts(i["public_date"])
        hm = t.strftime("%H:%M")
        # 只有日期没有具体时刻的事件，接口里统一给的是 12:02 或 00:00
        if i.get("calendar_type") == "FE" and hm in ("12:02", "00:00"):
            hm = "待定"
        rec = {"time": hm, "ts": i["public_date"], "country": i.get("country", ""), "title": i.get("title", ""),
               "importance": int(i.get("importance") or 0)}
        if i.get("calendar_type") == "FD":
            unit = i.get("unit") or ""
            fmt = lambda v: f"{v}{unit}" if v not in ("", None) else "-"  # noqa: E731
            rec.update(forecast=fmt(i.get("forecast")), previous=fmt(i.get("previous")), actual=fmt(i.get("actual")))
            rows.append(rec)
        else:
            rec["note"] = (i.get("foresight") or "").replace("前瞻 | ", "").strip()[:120]
            evs.append(rec)
    key = lambda r: (r["time"] == "待定", r["ts"], -r["importance"])  # noqa: E731
    return sorted(rows, key=key), sorted(evs, key=lambda r: (-r["importance"], r["time"] == "待定", r["ts"]))


def parse_ipo(data: dict) -> list[dict]:
    out = []
    for r in ((data.get("result") or {}).get("data")) or []:
        price = r.get("ISSUE_PRICE")
        out.append({"code": r.get("SECURITY_CODE"), "name": r.get("SECURITY_NAME"), "apply_code": r.get("APPLY_CODE"),
                    "market": r.get("MARKET") or r.get("TRADE_MARKET") or "",
                    "price": price, "upper": r.get("ONLINE_APPLY_UPPER"), "industry_pe": r.get("INDUSTRY_PE"),
                    "pe": r.get("AFTER_ISSUE_PE"), "business": (r.get("MAIN_BUSINESS") or "")[:40]})
    return out


def parse_lifts(data: dict, top: int = 8) -> list[dict]:
    rows = ((data.get("result") or {}).get("data")) or []
    out = [{"code": r.get("SECURITY_CODE"), "name": r.get("SECURITY_NAME_ABBR"), "date": (r.get("FREE_DATE") or "")[5:10],
            "cap": round((r.get("LIFT_MARKET_CAP") or 0) / 1e4, 1),          # 亿元
            "ratio": round(r.get("FREE_RATIO") or 0, 2),                     # 占流通股 %
            "type": r.get("FREE_SHARES_TYPE") or ""} for r in rows]
    out.sort(key=lambda r: -r["cap"])
    return out[:top]


def _dc(session: Session, report: str, flt: str, sort: str, page_size: int = 50) -> dict:
    return session.get_json(DATACENTER, params={
        "sortColumns": sort, "sortTypes": "-1", "pageSize": str(page_size), "pageNumber": "1",
        "reportName": report, "columns": "ALL", "source": "WEB", "client": "WEB", "filter": flt})


def fetch_watchlist(day: datetime, session: Session | None = None) -> Watchlist:
    session = session or Session()
    day0 = day.astimezone(TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    d = day0.strftime("%Y-%m-%d")
    w = Watchlist(day=d)

    def step(name, fn):
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            w.errors.append(f"{name}: {type(e).__name__}: {e}"[:200])
            log.warning("今日关注 %s 获取失败: %s", name, e)

    def cal():
        data = session.get_json(WSCN_CAL, params={"start": int(day0.timestamp()),
                                                  "end": int((day0 + timedelta(days=1)).timestamp()) - 1})
        w.data, w.events = parse_wscn_calendar(data)

    def ipo():
        w.ipo_apply = parse_ipo(_dc(session, "RPTA_APP_IPOAPPLY", f"(APPLY_DATE>='{d}')(APPLY_DATE<='{d}')", "APPLY_DATE"))
        w.ipo_list = parse_ipo(_dc(session, "RPTA_APP_IPOAPPLY", f"(LISTING_DATE>='{d}')(LISTING_DATE<='{d}')", "LISTING_DATE"))

    def lifts():
        end = (day0 + timedelta(days=6)).strftime("%Y-%m-%d")
        w.lifts = parse_lifts(_dc(session, "RPT_LIFT_STAGE", f"(FREE_DATE>='{d}')(FREE_DATE<='{end}')",
                                  "LIFT_MARKET_CAP", page_size=200))

    for name, fn in (("财经日历", cal), ("新股", ipo), ("解禁", lifts)):
        step(name, fn)
    return w


def fetch_calendar(start: datetime, end: datetime, session: Session | None = None,
                   min_importance: int = 3) -> tuple[list[dict], list[dict]]:
    """任意时间段的财经日历（周报"下周关注"用）。"""
    session = session or Session()
    data = session.get_json(WSCN_CAL, params={"start": int(start.timestamp()), "end": int(end.timestamp()) - 1})
    rows, evs = parse_wscn_calendar(data, min_importance)
    for r in [*rows, *evs]:
        r["date"] = from_ts(r["ts"]).strftime("%m-%d")
    return rows, evs
