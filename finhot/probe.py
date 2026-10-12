"""行情数据接口体检：在这台机器上，盘面和回测用到的每个接口能不能连通、取到多少条。

东方财富对部分机房 IP（比如国内云服务器、海外机器）会断开连接，同一份代码在不同机器上结果不一样，
排查时在出问题的机器上跑一下：python -m finhot probe（服务器上 deploy/finhot.sh probe）。
"""

from __future__ import annotations

import time
from typing import Callable

from .ashare import (DATACENTER, INDICES, PUSH2_HOSTS, PUSH2EX, PUSH2HIS_HOSTS, SINA_BOARDS, THS_LIMIT_UP, UT_POOL,
                     parse_boards, parse_indices, parse_lhb, parse_sina_boards, parse_ths_limit_up, parse_zt_pool)
from .backtest import parse_klines
from .http import Session
from .timeutil import now


def checks(session: Session) -> list[tuple[str, str, Callable[[], int]]]:
    """(接口, 用在哪里, 取数函数 → 条数)。每个接口只试一个域名，看的是这个域名本身通不通。"""
    day = now().strftime("%Y%m%d")

    def push2(host: str, path: str, params: dict, parse) -> Callable[[], int]:
        return lambda: parse(session.get_json(f"{host}{path}", params=params))

    clist = {"pn": "1", "pz": "100", "po": "1", "np": "1", "fltt": "2", "invt": "2", "fid": "f3", "fs": "m:90+t:2",
             "fields": "f12,f14,f3,f62,f104,f105,f128,f136"}
    kline = {"fields1": "f1,f2,f3", "fields2": "f51,f52,f53", "klt": "101", "fqt": "0", "lmt": "20", "end": "20500101",
             "ut": "fa5fd1943c7b386f172d6893dbfba10b"}
    out: list[tuple[str, str, Callable[[], int]]] = []
    for host in PUSH2_HOSTS:
        out.append((f"东财板块列表 {host.split('//')[1]}", "盘面板块、回测选板块",
                    push2(host, "/api/qt/clist/get", clist, lambda d: len(parse_boards(d, "industry")))))
    out.append((f"东财指数 {PUSH2_HOSTS[0].split('//')[1]}", "盘面指数", push2(
        PUSH2_HOSTS[0], "/api/qt/ulist.np/get", {"fltt": "2", "secids": ",".join(s for s, _ in INDICES), "fields": "f12,f14,f2,f3,f4,f6"},
        lambda d: len(parse_indices(d)[0]))))
    for host in PUSH2HIS_HOSTS:
        out.append((f"东财日K（中证1000）{host.split('//')[1]}", "回测基准",
                    push2(host, "/api/qt/stock/kline/get", {**kline, "secid": "1.000852"}, lambda d: len(parse_klines(d)))))
    out.append((f"东财板块日K（银行 BK0475）{PUSH2HIS_HOSTS[0].split('//')[1]}", "回测题材收益",
                push2(PUSH2HIS_HOSTS[0], "/api/qt/stock/kline/get", {**kline, "secid": "90.BK0475"}, lambda d: len(parse_klines(d)))))
    out.append(("东财涨停池 push2ex", "涨停原因", lambda: len(parse_zt_pool(session.get_json(
        f"{PUSH2EX}/getTopicZTPool", params={"ut": UT_POOL, "dpt": "wz.ztzt", "Pageindex": "0", "pagesize": "100",
                                             "sort": "fbt:asc", "date": day}))[1])))
    out.append(("东财龙虎榜 datacenter", "日报龙虎榜", lambda: len(parse_lhb(session.get_json(DATACENTER, params={
        "sortColumns": "TRADE_DATE", "sortTypes": "-1", "pageSize": "50", "pageNumber": "1",
        "reportName": "RPT_DAILYBILLBOARD_DETAILSNEW", "columns": "ALL", "source": "WEB", "client": "WEB"}))[1])))
    out.append(("同花顺涨停原因", "涨停原因", lambda: len(parse_ths_limit_up(session.get_json(THS_LIMIT_UP, params={
        "page": "1", "limit": "100", "field": "199112,10,9001,330323,330324,330325,9002,330329,133971,133970,1968584,3475914,9003,9004",
        "filter": "HS,GEM2STAR", "order_field": "330324", "order_type": "0", "date": ""},
        headers={"Referer": "https://data.10jqka.com.cn/datacenterph/limitup/limtupInfo.html"}))[0])))

    def sina() -> int:
        r = session.get(SINA_BOARDS["industry"], headers={"Referer": "https://finance.sina.com.cn/"})
        r.raise_for_status()
        return len(parse_sina_boards(r.content.decode("gbk", "replace"), "industry"))

    out.append(("新浪行业板块", "东财板块不可用时的备用", sina))
    return out


def probe(session: Session | None = None) -> int:
    session = session or Session(timeout=10, retries=0)
    print(f"{'接口':<40} {'用途':<20} 结果")
    failed = 0
    for name, use, fn in checks(session):
        t0 = time.time()
        try:
            n = fn()
            status = f"✅ {n} 条（{time.time() - t0:.1f}s）" if n else f"⚠️ 连上了但没数据（{time.time() - t0:.1f}s）"
        except Exception as e:  # noqa: BLE001
            failed += 1
            status = f"❌ {type(e).__name__}: {str(e)[:80]}"
        print(f"{name:<40} {use:<20} {status}")
    return 1 if failed else 0
