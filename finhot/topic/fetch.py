"""数据层：从问财取题材的成分股、行情、基本面、阶段指标、新闻研报公告。

一个题材大约 10~13 次真实调用（成分股较多时翻页另算），同一交易日内重复运行走缓存。
问句用 topics/<名称>.yaml 的 wencai.universe / index / search 拼出来；问财返回的列名带日期，
所以解析一律按关键词找列（Table.col），找不到的字段显示“—”，并记进 errors 方便排查。
"""

from __future__ import annotations

import logging
import statistics
from dataclasses import asdict, dataclass, field

from ..wencai import INDEX, SECTORS, STOCKS, Hit, Table, Wencai, WencaiError, WencaiFatal, code6, num, span_days
from .kb import Topic

log = logging.getLogger(__name__)

Q_MARKET = "{u}，总市值，近20个交易日区间涨跌幅，近60个交易日区间涨跌幅，近20个交易日区间成交额"
Q_FUND = "{u}，总市值从大到小排名，主营产品名称，营业收入同比增长率，归属母公司股东的净利润同比增长率，市盈率(pe,ttm)"
Q_ABOVE20 = "{u}，收盘价大于20日均线"
Q_ABOVE60 = "{u}，收盘价大于60日均线"
Q_HIGH60 = "{u}，创60日新高"
Q_INDEX = "{i}指数，收盘价，20日均线，60日均线，近20个交易日区间涨跌幅，近60个交易日区间涨跌幅，近20个交易日区间成交额"
Q_BENCH = "沪深300指数，收盘价，近20个交易日区间涨跌幅，近60个交易日区间涨跌幅"
Q_KNOWN = ("{names}，总市值，近20个交易日区间涨跌幅，近60个交易日区间涨跌幅，近20个交易日区间成交额，主营产品名称，"
           "营业收入同比增长率，归属母公司股东的净利润同比增长率，市盈率(pe,ttm)")


@dataclass
class Member:
    code: str
    name: str
    mcap: float | None = None       # 总市值，亿元
    ret20: float | None = None      # 近 20 个交易日涨跌幅，%
    ret60: float | None = None
    amount20: float | None = None   # 近 20 个交易日成交额，亿元
    pe: float | None = None
    rev_yoy: float | None = None    # 营收同比，%
    np_yoy: float | None = None     # 归母净利同比，%
    products: str = ""
    segments: list[str] = field(default_factory=list)
    tier: str = ""                  # 核心 / 相关（知识层）；主营相关（主营产品命中环节关键词）
    note: str = ""
    extra: bool = False             # 知识层列了、但不在问财这个概念的成分股里（单独补查的）


@dataclass
class IndexStat:
    name: str
    close: float | None = None
    ma20: float | None = None
    ma60: float | None = None
    ret20: float | None = None
    ret60: float | None = None
    amount20: float | None = None   # 亿元


@dataclass
class WencaiData:
    members: list[Member] = field(default_factory=list)
    total: int = 0
    breadth: dict[str, int | None] = field(default_factory=dict)
    index: IndexStat | None = None
    bench: IndexStat | None = None
    news: list[Hit] = field(default_factory=list)
    reports: list[Hit] = field(default_factory=list)
    notices: list[Hit] = field(default_factory=list)
    queries: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    fatal: str = ""
    calls: int = 0
    cache_hits: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------- 解析

def _yi(v) -> float | None:
    """金额统一成亿元：问财一般给元，数值很大；已经是亿的小数值原样保留。"""
    x = num(v)
    if x is None:
        return None
    return round(x / 1e8, 2) if abs(x) > 1e5 else round(x, 2)


def _ret_cols(t: Table) -> tuple[str | None, str | None]:
    """（20 日涨幅列, 60 日涨幅列）。同一问句要了两个区间时按区间长短区分。"""
    cols = t.range_cols("涨跌幅")
    if len(cols) >= 2:
        return cols[0], cols[-1]
    if len(cols) == 1:
        return (cols[0], None) if (span_days(cols[0]) or 0) <= 45 else (None, cols[0])
    c = t.col("区间涨跌幅") or t.col("涨跌幅", exclude=("最新",))
    return c, None


def _ma_col(t: Table, n: int) -> str | None:
    for c in t.columns:
        if ("均线" in c or "MA" in c.upper()) and (f"{n}日" in c or f"MA{n}" in c.upper() or f"({n})" in c
                                                   or f"[{n}]" in c or f"{n}天" in c):
            return c
    return None


def parse_members(market: Table | None, fund: Table | None) -> list[Member]:
    by_code: dict[str, Member] = {}
    for t in (market, fund):
        if not t or not t.rows:
            continue
        c_code, c_name = t.col("代码"), t.col("简称") or t.col("名称")
        if not c_code:
            continue
        c20, c60 = _ret_cols(t)
        c_mcap = t.col("总市值")
        c_amt = (t.range_cols("成交额") or [None])[0] or t.col("成交额")
        c_pe = t.col("市盈率")
        c_rev = t.col("营业收入", "同比") or t.col("营业收入", "增长") or t.col("营收", "增长")
        c_np = t.col("净利润", "同比", exclude=("扣非",)) or t.col("净利润", "增长", exclude=("扣非",))
        c_prod = t.col("主营产品") or t.col("主营业务") or t.col("经营范围")
        for r in t.rows:
            code = code6(r.get(c_code))
            if not code:
                continue
            m = by_code.setdefault(code, Member(code, str(r.get(c_name) or "")))
            m.name = m.name or str(r.get(c_name) or "")
            for attr, col, conv in (("mcap", c_mcap, _yi), ("ret20", c20, num), ("ret60", c60, num),
                                    ("amount20", c_amt, _yi), ("pe", c_pe, num), ("rev_yoy", c_rev, num),
                                    ("np_yoy", c_np, num)):
                if col and getattr(m, attr) is None:
                    v = conv(r.get(col))
                    setattr(m, attr, round(v, 2) if v is not None else None)
            if c_prod and not m.products:
                p = r.get(c_prod)
                m.products = "、".join(map(str, p)) if isinstance(p, list) else str(p or "")
    return list(by_code.values())


def parse_index(t: Table | None, name: str) -> IndexStat | None:
    if not t or not t.rows:
        return None
    c_name = t.col("简称") or t.col("名称")
    row = t.rows[0]
    if c_name:  # 有多行时挑名字最像的
        for r in t.rows:
            if name and name in str(r.get(c_name) or ""):
                row = r
                break
    c20, c60 = _ret_cols(t)
    c_close = t.col("收盘价") or t.col("最新价") or t.col("收盘")
    c_amt = (t.range_cols("成交额") or [None])[0] or t.col("成交额")
    m20, m60 = _ma_col(t, 20), _ma_col(t, 60)
    get = lambda c: num(row.get(c)) if c else None  # noqa: E731
    return IndexStat(name=str(row.get(c_name) or name) if c_name else name, close=get(c_close),
                     ma20=get(m20), ma60=get(m60), ret20=get(c20), ret60=get(c60),
                     amount20=_yi(row.get(c_amt)) if c_amt else None)


def classify(members: list[Member], topic: Topic) -> None:
    """给成分股标环节和正宗度：知识层名单优先，其次主营产品命中环节关键词。"""
    known: dict[str, list] = {}
    for s in topic.stocks():
        for key in filter(None, (s.code, s.name)):
            known.setdefault(key, []).append(s)
    segs = [(seg["name"], [k for k in seg.get("keywords") or [] if k]) for seg in topic.segments()]
    for m in members:
        for s in known.get(m.code, []) or known.get(m.name, []):
            if s.segment not in m.segments:
                m.segments.append(s.segment)
            if s.tier == "核心" or not m.tier:
                m.tier, m.note = s.tier, s.note
        for name, kws in segs:
            if m.products and any(k in m.products for k in kws) and name not in m.segments:
                m.segments.append(name)
                if not m.tier:
                    m.tier = "主营相关"


# ---------------------------------------------------------------- 取数

def collect(topic: Topic, client: Wencai, bench_cache: dict | None = None) -> WencaiData:
    w = topic.get("wencai", {})
    u, idx, kw = w["universe"], w["index"], w["search"]
    wd = WencaiData()
    calls0, hits0 = client.calls, client.cache_hits

    def run(label: str, fn, *args, **kw_):
        if wd.fatal:
            return None
        try:
            res = fn(*args, **kw_)
        except WencaiFatal as e:
            wd.fatal = str(e)
            wd.errors.append(f"{label}：{e}")
            return None
        except WencaiError as e:
            wd.errors.append(f"{label}：{e}")
            return None
        if isinstance(res, Table):
            wd.queries.append({"label": label, "query": res.query, "rows": len(res.rows), "total": res.total,
                               "cached": res.cached, "columns": res.columns})
        elif isinstance(res, list):
            wd.queries.append({"label": label, "query": args[0] if args else "", "rows": len(res)})
        elif isinstance(res, int):
            wd.queries.append({"label": label, "query": args[0] if args else "", "total": res})
        return res

    market = run("成分股行情", client.query_all, Q_MARKET.format(u=u), STOCKS, 100, 4)
    fund = run("成分股基本面", client.query, Q_FUND.format(u=u), STOCKS, 100, 1)
    wd.members = parse_members(market, fund)
    wd.total = max(market.total if market else 0, len(wd.members))
    if market and not wd.members:
        wd.errors.append(f"成分股行情：返回了 {len(market.rows)} 行但没找到代码列，列名：{market.columns[:8]}")
    # 知识层列了、但不在问财概念成分里的公司（如只有部分业务相关的中军），按名称补查一次
    have = {m.code for m in wd.members} | {m.name for m in wd.members}
    missing = list(dict.fromkeys(s.name for s in topic.stocks() if s.name not in have and s.code not in have))[:25]
    if missing and market is not None:
        t_known = run("知识层个股", client.query, Q_KNOWN.format(names="，".join(missing)), STOCKS, 50)
        for m in parse_members(t_known, None):
            if m.code not in have:
                m.extra = True
                wd.members.append(m)
    classify(wd.members, topic)

    wd.breadth = {"above_ma20": run("站上20日线家数", client.count, Q_ABOVE20.format(u=u)),
                  "above_ma60": run("站上60日线家数", client.count, Q_ABOVE60.format(u=u)),
                  "high60": run("创60日新高家数", client.count, Q_HIGH60.format(u=u))}

    t_idx = run("题材指数", client.query, Q_INDEX.format(i=idx), SECTORS, 5)
    wd.index = parse_index(t_idx, idx)
    if bench_cache is not None and "bench" in bench_cache:
        wd.bench = bench_cache["bench"]
    else:
        t_b = run("沪深300", client.query, Q_BENCH, INDEX, 1)
        if t_b is None and not wd.fatal:
            t_b = run("沪深300（板块接口）", client.query, Q_BENCH, SECTORS, 1)
        wd.bench = parse_index(t_b, "沪深300")
        if bench_cache is not None and wd.bench:
            bench_cache["bench"] = wd.bench

    wd.news = run("新闻", client.search, kw, "news", 15) or []
    wd.reports = run("研报", client.search, kw, "report", 10) or []
    wd.notices = run("风险公告", client.search, f"{kw} 澄清", "announcement", 10) or []
    wd.calls, wd.cache_hits = client.calls - calls0, client.cache_hits - hits0
    return wd


# ---------------------------------------------------------------- 派生

def median(xs) -> float | None:
    xs = [x for x in xs if x is not None]
    return round(statistics.median(xs), 2) if xs else None


def segment_stats(members: list[Member], topic: Topic) -> list[dict]:
    """各环节近 20 / 60 日涨幅中位数：资金在炒哪个细分。"""
    out = []
    for seg in topic.segments():
        ms = [m for m in members if seg["name"] in m.segments]
        if not ms:
            continue
        out.append({"segment": seg["name"], "stage": seg["stage"], "n": len(ms),
                    "ret20": median(m.ret20 for m in ms), "ret60": median(m.ret60 for m in ms),
                    "best": max((m for m in ms if m.ret20 is not None), key=lambda m: m.ret20, default=None)})
    return sorted(out, key=lambda s: -(s["ret20"] if s["ret20"] is not None else -999))


def leaders(members: list[Member], n_core: int = 5, n_strong: int = 8) -> dict[str, list[Member]]:
    """趋势中军：市值前 30% 里近 20 日上涨、成交额最大的几只；强势股：近 20 日涨幅最大的几只。"""
    members = [m for m in members if not m.extra]   # 中军、强势股只在概念成分股里排
    caps = sorted(m.mcap for m in members if m.mcap is not None)
    cut = caps[int(len(caps) * 0.7)] if caps else None
    big = [m for m in members if m.mcap is not None and cut is not None and m.mcap >= cut]
    rising = [m for m in big if m.ret20 is not None and m.ret20 > 0] or big
    key = (lambda m: m.amount20 or 0) if any(m.amount20 for m in rising) else (lambda m: m.mcap or 0)
    core = sorted(rising, key=key, reverse=True)[:n_core]
    strong = sorted((m for m in members if m.ret20 is not None and m.ret20 > 0 and m not in core),
                    key=lambda m: m.ret20, reverse=True)[:n_strong]
    return {"core": core, "strong": strong}
