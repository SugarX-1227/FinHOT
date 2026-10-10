"""A 股市场快照：指数与成交额、涨跌分布、涨停/跌停/炸板、连板梯队、行业/概念板块、龙虎榜、沪深港通。

数据来自东方财富网页端公开接口。盘前运行时拿到的是上一交易日收盘数据（快照里带 trade_date）。
任何一个子项失败都只记录到 errors，不影响其他部分和日报生成。
"""

from __future__ import annotations

import logging
import re
import time
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta

from .http import Session
from .timeutil import now

log = logging.getLogger(__name__)

# push2 在海外（如 GitHub Actions）偶尔连续 502，依次尝试备用域名
PUSH2_HOSTS = ("https://push2.eastmoney.com", "https://push2delay.eastmoney.com", "https://82.push2.eastmoney.com")
PUSH2HIS_HOSTS = ("https://push2his.eastmoney.com", "https://61.push2his.eastmoney.com", "https://33.push2his.eastmoney.com")
PUSH2EX = "https://push2ex.eastmoney.com"
DATACENTER = "https://datacenter-web.eastmoney.com/api/data/v1/get"
UT_POOL = "7eea3edcaed734bea9cbfc24409ed989"

INDICES = [
    ("1.000001", "上证指数"), ("0.399001", "深证成指"), ("0.399006", "创业板指"), ("1.000688", "科创50"),
    ("1.000300", "沪深300"), ("1.000852", "中证1000"), ("0.899050", "北证50"),
]
# 概念板块里有不少"昨日涨停""融资融券""MSCI"之类的统计/指数型板块，排行时剔除
_JUNK_CONCEPT = re.compile(
    r"昨日|连板|涨停|首板|破净|^ST|融资融券|MSCI|沪股通|深股通|标普|富时|AH股|转债|HS300|上证|深证|中证|创业板综|"
    r"央视|茅指数|宁组合|基金重仓|社保重仓|QFII|机构重仓|证金|汇金|预盈|预增|扭亏|送转|高送|次新|注册制|股权激励|"
    r"百元股|低价股|微盘|小盘|大盘|中盘|陆股通|北交所概念|专精特新|独角兽")


@dataclass
class Board:
    code: str
    name: str
    pct: float
    inflow: float          # 主力净流入（亿元）
    up: int = 0
    down: int = 0
    leader: str = ""       # 领涨股
    leader_pct: float = 0.0
    kind: str = "industry"  # industry | concept


@dataclass
class MarketSnapshot:
    trade_date: str = ""
    indices: list[dict] = field(default_factory=list)
    turnover: float = 0.0                  # 沪深京成交额（亿元）
    breadth: dict = field(default_factory=dict)   # up/down/flat/zt/dt/zb/seal_rate
    ladder: list[dict] = field(default_factory=list)  # 连板梯队 [{boards: n, stocks: [...]}]
    zt_industries: list[dict] = field(default_factory=list)  # 涨停行业分布 [{name, count, stocks}]
    zt_stocks: list[dict] = field(default_factory=list)
    industries: list[Board] = field(default_factory=list)
    concepts: list[Board] = field(default_factory=list)
    lhb_date: str = ""
    lhb_buy: list[dict] = field(default_factory=list)
    lhb_sell: list[dict] = field(default_factory=list)
    hsgt: dict = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.trade_date or self.indices or self.industries)

    def to_dict(self) -> dict:
        d = asdict(self)
        # 板块全量较大，落盘只保留前后各 30 个
        for k in ("industries", "concepts"):
            rows = d[k]
            d[k] = rows[:30] + rows[-30:] if len(rows) > 60 else rows
        return d


# ------------------------------------------------------------------ 解析（纯函数，便于离线测试）
def parse_indices(data: dict) -> tuple[list[dict], float]:
    names = dict((s.split(".", 1)[1], n) for s, n in INDICES)
    order = [s.split(".", 1)[1] for s, _ in INDICES]
    rows = {}
    for d in ((data.get("data") or {}).get("diff") or []):
        if d.get("f2") in (None, "-"):
            continue
        rows[d["f12"]] = {"code": d["f12"], "name": names.get(d["f12"], d.get("f14")), "price": d["f2"],
                          "pct": d.get("f3"), "amount": round((d.get("f6") or 0) / 1e8, 1)}
    out = [rows[c] for c in order if c in rows]
    turnover = sum(r["amount"] for r in out if r["code"] in ("000001", "399001", "899050"))
    return out, round(turnover, 1)


def parse_fenbu(data: dict) -> dict:
    up = down = flat = 0
    for kv in ((data.get("data") or {}).get("fenbu") or []):
        for k, v in kv.items():
            k = int(k)
            if k > 0:
                up += v
            elif k < 0:
                down += v
            else:
                flat += v
    return {"up": up, "down": down, "flat": flat}


def parse_zt_pool(data: dict) -> tuple[str, list[dict]]:
    d = data.get("data") or {}
    qdate = str(d.get("qdate") or "")
    date = f"{qdate[:4]}-{qdate[4:6]}-{qdate[6:]}" if len(qdate) == 8 else ""
    stocks = []
    for p in d.get("pool") or []:
        stocks.append({
            "code": p.get("c"), "name": p.get("n"), "pct": round(p.get("zdp") or 0, 2),
            "boards": int(p.get("lbc") or 1),                    # 连板数
            "industry": p.get("hybk") or "",
            "first_time": f"{int(p.get('fbt') or 0):06d}"[:4],   # 首次封板 HHMM
            "seal_fund": round((p.get("fund") or 0) / 1e8, 2),   # 封单（亿）
            "breaks": int(p.get("zbc") or 0),                    # 炸板次数
            "stat": f"{(p.get('zttj') or {}).get('days', '')}天{(p.get('zttj') or {}).get('ct', '')}板",
        })
    return date, stocks


def build_ladder(stocks: list[dict], top: int = 6) -> list[dict]:
    by = defaultdict(list)
    for s in stocks:
        by[s["boards"]].append(s["name"])
    return [{"boards": n, "stocks": by[n]} for n in sorted(by, reverse=True) if n >= 2][:top]


def zt_by_industry(stocks: list[dict], top: int = 8) -> list[dict]:
    cnt = Counter(s["industry"] for s in stocks if s["industry"])
    out = []
    for name, c in cnt.most_common(top):
        members = sorted((s for s in stocks if s["industry"] == name), key=lambda s: -s["boards"])
        out.append({"name": name, "count": c, "stocks": [s["name"] for s in members[:4]]})
    return out


def parse_boards(data: dict, kind: str) -> list[Board]:
    out = []
    for d in ((data.get("data") or {}).get("diff") or []):
        if d.get("f3") in (None, "-"):
            continue
        name = d.get("f14") or ""
        if kind == "concept" and _JUNK_CONCEPT.search(name):
            continue
        out.append(Board(code=d.get("f12", ""), name=name, pct=float(d["f3"]),
                         inflow=round(float(d.get("f62") or 0) / 1e8, 2), up=int(d.get("f104") or 0),
                         down=int(d.get("f105") or 0), leader=d.get("f128") or "",
                         leader_pct=float(d.get("f136") or 0) if d.get("f136") not in (None, "-") else 0.0,
                         kind=kind))
    return out


def parse_lhb(data: dict) -> tuple[str, list[dict]]:
    rows = ((data.get("result") or {}).get("data")) or []
    if not rows:
        return "", []
    date = max(r["TRADE_DATE"][:10] for r in rows)
    seen, out = set(), []
    for r in rows:
        if r["TRADE_DATE"][:10] != date or r["SECURITY_CODE"] in seen:
            continue
        seen.add(r["SECURITY_CODE"])
        out.append({"code": r["SECURITY_CODE"], "name": r["SECURITY_NAME_ABBR"],
                    "pct": round(r.get("CHANGE_RATE") or 0, 2),
                    "net": round((r.get("BILLBOARD_NET_AMT") or 0) / 1e8, 2),
                    "reason": (r.get("EXPLAIN") or "").strip(),
                    "rule": (r.get("EXPLANATION") or "").strip()})
    return date, out


def parse_hsgt(data: dict) -> dict:
    d = data.get("data") or {}
    def g(k, f):
        return float((d.get(k) or {}).get(f) or 0)
    return {
        "date": (d.get("hk2sh") or {}).get("date2", ""),
        # 北向资金自 2024 年 8 月起不再披露实时净买入，只看成交额（单位：亿元）
        "north_turnover": round((g("hk2sh", "buySellAmt") + g("hk2sz", "buySellAmt")) / 1e4, 1),
        "south_net": round((g("sh2hk", "netBuyAmt") + g("sz2hk", "netBuyAmt")) / 1e4, 1),
        "south_turnover": round((g("sh2hk", "buySellAmt") + g("sz2hk", "buySellAmt")) / 1e4, 1),
    }


# ------------------------------------------------------------------ 抓取
def push2_json(session: Session, path: str, params: dict, hosts: tuple[str, ...] = PUSH2_HOSTS) -> dict:
    last: Exception | None = None
    for host in hosts:
        try:
            data = session.get_json(f"{host}{path}", params=params)
            if data.get("data") is not None:
                return data
            last = RuntimeError(f"{host} 返回空数据")
        except Exception as e:  # noqa: BLE001
            last = e
    raise last or RuntimeError("push2 不可用")


def _pool(session: Session, api: str, date: str, sort: str = "fbt:asc") -> dict:
    return session.get_json(f"{PUSH2EX}/{api}", params={
        "ut": UT_POOL, "dpt": "wz.ztzt", "Pageindex": "0", "pagesize": "1000", "sort": sort, "date": date})


def _boards(session: Session, fs: str, kind: str) -> list[Board]:
    """按涨幅排序分页拉取全部板块。第 1 页之后某页失败就保留已拿到的（前几页即涨幅靠前的板块）。"""
    out: list[Board] = []
    for pn in range(1, 8):
        if pn > 1:
            time.sleep(0.5)
        try:
            data = push2_json(session, "/api/qt/clist/get", {
                "pn": str(pn), "pz": "100", "po": "1", "np": "1", "fltt": "2", "invt": "2", "fid": "f3", "fs": fs,
                "fields": "f12,f14,f3,f62,f104,f105,f128,f136"})
        except Exception as e:  # noqa: BLE001
            if pn == 1:
                raise
            log.warning("%s板块第 %d 页获取失败，保留前 %d 个: %s", kind, pn, len(out), e)
            break
        out.extend(parse_boards(data, kind))
        total = int(((data.get("data") or {}).get("total")) or 0)
        if not (data.get("data") or {}).get("diff") or pn * 100 >= total:
            break
    out.sort(key=lambda b: -b.pct)
    return out


def fetch_snapshot(session: Session | None = None, today: datetime | None = None) -> MarketSnapshot:
    session = session or Session()
    today = today or now()
    snap = MarketSnapshot()

    def step(name, fn):
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            snap.errors.append(f"{name}: {type(e).__name__}: {e}"[:200])
            log.warning("行情快照 %s 失败: %s", name, e)

    def indices():
        data = push2_json(session, "/api/qt/ulist.np/get", {
            "fltt": "2", "secids": ",".join(s for s, _ in INDICES), "fields": "f12,f14,f2,f3,f4,f6,f124"})
        snap.indices, snap.turnover = parse_indices(data)

    def zt():
        # 传今天的日期即可：非交易日/盘前接口返回最近一个交易日，qdate 给出实际日期
        for back in range(0, 6):
            date = (today - timedelta(days=back)).strftime("%Y%m%d")
            tdate, stocks = parse_zt_pool(_pool(session, "getTopicZTPool", date))
            if stocks:
                break
        snap.trade_date = tdate
        snap.zt_stocks = sorted(stocks, key=lambda s: (-s["boards"], s["first_time"]))
        snap.ladder = build_ladder(stocks)
        snap.zt_industries = zt_by_industry(stocks)
        qd = tdate.replace("-", "") or today.strftime("%Y%m%d")
        dt = (_pool(session, "getTopicDTPool", qd, "fund:asc").get("data") or {}).get("tc") or 0
        zb = (_pool(session, "getTopicZBPool", qd).get("data") or {}).get("tc") or 0
        snap.breadth.update(zt=len(stocks), dt=int(dt), zb=int(zb),
                            seal_rate=round(len(stocks) / (len(stocks) + zb) * 100, 1) if stocks else 0.0)

    def fenbu():
        data = session.get_json(f"{PUSH2EX}/getTopicZDFenBu", params={"ut": UT_POOL, "dpt": "wz.ztzt"})
        snap.breadth.update(parse_fenbu(data))

    def industries():
        snap.industries = _boards(session, "m:90+t:2", "industry")

    def concepts():
        snap.concepts = _boards(session, "m:90+t:3", "concept")

    def lhb():
        start = (today - timedelta(days=10)).strftime("%Y-%m-%d")
        data = session.get_json(DATACENTER, params={
            "sortColumns": "TRADE_DATE,BILLBOARD_NET_AMT", "sortTypes": "-1,-1", "pageSize": "500", "pageNumber": "1",
            "reportName": "RPT_DAILYBILLBOARD_DETAILSNEW", "columns": "ALL", "source": "WEB", "client": "WEB",
            "filter": f"(TRADE_DATE>='{start}')"})
        snap.lhb_date, rows = parse_lhb(data)
        snap.lhb_buy = sorted((r for r in rows if r["net"] > 0), key=lambda r: -r["net"])[:8]
        snap.lhb_sell = sorted((r for r in rows if r["net"] < 0), key=lambda r: r["net"])[:5]

    def hsgt():
        data = push2_json(session, "/api/qt/kamt/get", {
            "fields1": "f1,f2,f3,f4", "fields2": "f51,f52,f53,f54,f56,f62,f63,f65,f66"})
        snap.hsgt = parse_hsgt(data)

    for name, fn in (("指数", indices), ("涨停池", zt), ("涨跌分布", fenbu), ("行业板块", industries), ("概念板块", concepts),
                     ("龙虎榜", lhb), ("沪深港通", hsgt)):
        step(name, fn)
    return snap
