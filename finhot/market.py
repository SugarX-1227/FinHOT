"""隔夜/实时行情快照（东方财富行情接口）。失败时返回空列表，日报中该节显示"暂不可用"。"""

from __future__ import annotations

import logging

from .http import Session
from .timeutil import from_ts

log = logging.getLogger(__name__)
URL = "https://push2.eastmoney.com/api/qt/ulist.np/get"


def parse_quotes(data: dict, markets: list[dict]) -> list[dict]:
    names = {m["secid"].split(".", 1)[1]: m["name"] for m in markets}
    order = [m["secid"].split(".", 1)[1] for m in markets]
    rows = {}
    for d in ((data.get("data") or {}).get("diff") or []):
        code = d.get("f12")
        if d.get("f2") in (None, "-"):
            continue
        rows[code] = {
            "code": code,
            "name": names.get(code) or d.get("f14", code),
            "price": d.get("f2"),
            "change": d.get("f4"),
            "pct": d.get("f3"),
            "time": from_ts(d["f124"]).strftime("%m-%d %H:%M") if d.get("f124") else "",
        }
    return [rows[c] for c in order if c in rows]


def fetch_quotes(markets: list[dict], session: Session | None = None) -> list[dict]:
    if not markets:
        return []
    session = session or Session()
    try:
        data = session.get_json(URL, params={
            "fltt": "2",
            "secids": ",".join(m["secid"] for m in markets),
            "fields": "f12,f13,f14,f2,f3,f4,f124",
        }, headers={"Referer": "https://quote.eastmoney.com/"})
        return parse_quotes(data, markets)
    except Exception as e:  # noqa: BLE001
        log.warning("行情获取失败: %s", e)
        return []
