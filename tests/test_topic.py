"""题材档案：问财客户端（协议头、缓存、预算、额度）、列名解析、本地数据匹配、阶段判断、整张档案卡与静态站。

问财返回用假数据，但列名按真实格式带日期（如“区间涨跌幅:前复权[20260923-20261021]”），解析逻辑要能认出来。
"""

import json
from datetime import date, datetime
from pathlib import Path

import pytest

from finhot import wencai
from finhot.site import build_site
from finhot.timeutil import TZ
from finhot.topic import fetch, kb, local
from finhot.topic.build import build, build_all
from finhot.topic.fetch import IndexStat, Member, WencaiData
from finhot.topic.stage import judge
from finhot.wencai import Table, Wencai, WencaiError, WencaiFatal

KEY = "sk-test-secret-key"
AT = datetime(2026, 10, 21, 16, 0, tzinfo=TZ)


# ---------------------------------------------------------------- 假问财

class Resp:
    def __init__(self, status=200, payload=None, text=None):
        self.status_code = status
        self._payload = payload
        self.text = text if text is not None else json.dumps(payload or {}, ensure_ascii=False)

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


def cols(*names):
    return [{"key": n} for n in names]


MARKET = {
    "status_code": 0, "code_count": 3,
    "columns": cols("股票代码", "股票简称", "最新价", "总市值[20261021]", "区间涨跌幅:前复权[20260723-20261021]",
                    "区间涨跌幅:前复权[20260923-20261021]", "区间成交额[20260923-20261021]"),
    "datas": [
        {"股票代码": "601689.SH", "股票简称": "拓普集团", "最新价": "70.1", "总市值[20261021]": "121800000000",
         "区间涨跌幅:前复权[20260723-20261021]": "25.4", "区间涨跌幅:前复权[20260923-20261021]": "8.2",
         "区间成交额[20260923-20261021]": "45600000000"},
        {"股票代码": "603667.SH", "股票简称": "五洲新春", "最新价": 60, "总市值[20261021]": 22000000000,
         "区间涨跌幅:前复权[20260723-20261021]": 40.0, "区间涨跌幅:前复权[20260923-20261021]": 31.5,
         "区间成交额[20260923-20261021]": 30100000000},
        {"股票代码": "300999.SZ", "股票简称": "蹭概念", "最新价": "9", "总市值[20261021]": "3000000000",
         "区间涨跌幅:前复权[20260723-20261021]": "--", "区间涨跌幅:前复权[20260923-20261021]": "4.5",
         "区间成交额[20260923-20261021]": "800000000"},
    ],
}
FUND = {
    "status_code": 0, "code_count": 3,
    "columns": cols("股票代码", "股票简称", "主营产品名称", "营业收入(同比增长率)[20260630]",
                    "归属于母公司所有者的净利润(同比增长率)[20260630]", "市盈率(pe,ttm)[20261021]"),
    "datas": [
        {"股票代码": "601689.SH", "股票简称": "拓普集团", "主营产品名称": "减震器、内饰功能件、执行器",
         "营业收入(同比增长率)[20260630]": "18.3", "归属于母公司所有者的净利润(同比增长率)[20260630]": "12.1",
         "市盈率(pe,ttm)[20261021]": "35.2"},
        {"股票代码": "603667.SH", "股票简称": "五洲新春", "主营产品名称": "轴承、行星滚柱丝杠",
         "营业收入(同比增长率)[20260630]": "9.9", "归属于母公司所有者的净利润(同比增长率)[20260630]": "-3.0",
         "市盈率(pe,ttm)[20261021]": "88"},
        {"股票代码": "300999.SZ", "股票简称": "蹭概念", "主营产品名称": "纺织品",
         "营业收入(同比增长率)[20260630]": "1", "归属于母公司所有者的净利润(同比增长率)[20260630]": "2",
         "市盈率(pe,ttm)[20261021]": "20"},
    ],
}
INDEX_ROW = {
    "status_code": 0, "code_count": 1,
    "columns": cols("指数代码", "指数简称", "收盘价[20261021]", "均线(MA)(20日)[20261021]", "均线(MA)(60日)[20261021]",
                    "区间涨跌幅[20260923-20261021]", "区间涨跌幅[20260723-20261021]", "区间成交额[20260923-20261021]"),
    "datas": [{"指数代码": "886069", "指数简称": "人形机器人", "收盘价[20261021]": "1500", "均线(MA)(20日)[20261021]": "1400",
               "均线(MA)(60日)[20261021]": "1300", "区间涨跌幅[20260923-20261021]": "15.0",
               "区间涨跌幅[20260723-20261021]": "22.0", "区间成交额[20260923-20261021]": "900000000000"}],
}
BENCH = {"status_code": 0, "code_count": 1, "columns": cols("指数简称", "区间涨跌幅[20260923-20261021]",
                                                             "区间涨跌幅[20260723-20261021]"),
         "datas": [{"指数简称": "沪深300", "区间涨跌幅[20260923-20261021]": "1.0", "区间涨跌幅[20260723-20261021]": "4.0"}]}
KNOWN = {"status_code": 0, "code_count": 2,
         "columns": cols("股票代码", "股票简称", "总市值[20261021]", "区间涨跌幅:前复权[20260923-20261021]",
                         "主营产品名称"),
         "datas": [{"股票代码": "002050.SZ", "股票简称": "三花智控", "总市值[20261021]": "100000000000",
                    "区间涨跌幅:前复权[20260923-20261021]": "50", "主营产品名称": "热管理、机器人执行器"},
                   {"股票代码": "601689.SH", "股票简称": "拓普集团"}]}
SEARCH = {"status_code": 0, "data": [{"title": "<em>人形机器人</em>板块走强", "summary": "摘要", "url": "https://x/1",
                                      "publish_time": "2026-10-21 15:00:00"}]}


class FakeSession:
    """按问句路由的假问财；记录每次请求，检查协议头。"""

    def __init__(self, override=None):
        self.posts = []
        self.override = override

    def post(self, url, json=None, headers=None, timeout=None):  # noqa: A002
        self.posts.append((url, json, headers))
        if self.override:
            return self.override(url, json, headers)
        q = json.get("query", "")
        if url.endswith("/comprehensive/search"):
            return Resp(payload=SEARCH)
        if q.startswith("三花智控"):        # 知识层个股补查：只有三花智控不在概念成分里
            return Resp(payload=KNOWN)
        if "沪深300" in q:
            return Resp(payload=BENCH)
        if "指数" in q:
            return Resp(payload=INDEX_ROW)
        if "主营产品" in q:
            return Resp(payload=FUND)
        if "20日均线" in q:
            return Resp(payload={"status_code": 0, "code_count": 2, "columns": [], "datas": []})
        if "60日均线" in q:
            return Resp(payload={"status_code": 0, "code_count": 2, "columns": [], "datas": []})
        if "新高" in q:
            return Resp(payload={"status_code": 0, "code_count": 1, "columns": [], "datas": []})
        return Resp(payload=MARKET)


def client(tmp_path, session=None, **kw):
    return Wencai(api_key=KEY, data_dir=tmp_path, session=session or FakeSession(), sleep=lambda s: None, **kw)


# ---------------------------------------------------------------- 解析工具

def test_num_and_code():
    assert wencai.num("12.5%") == 12.5
    assert wencai.num("1.2亿") == 1.2e8
    assert wencai.num("--") is None and wencai.num(None) is None and wencai.num("") is None
    assert wencai.num(3) == 3.0
    assert wencai.code6("300024.SZ") == "300024" and wencai.code6("SH601689") == "601689"
    assert wencai.code6("09880.HK") == "09880"


def test_table_columns_by_keyword_and_range():
    t = wencai.parse_table("q", MARKET)
    assert t.total == 3 and len(t.rows) == 3
    assert t.col("总市值") == "总市值[20261021]"
    short, long_ = t.range_cols("涨跌幅")[0], t.range_cols("涨跌幅")[-1]
    assert "20260923" in short and "20260723" in long_
    assert wencai.base_name("区间涨跌幅:前复权[20260923-20261021]") == "区间涨跌幅"


def test_parse_hits_strips_highlight():
    hits = wencai.parse_hits(SEARCH, "news")
    assert hits[0].title == "人形机器人板块走强" and hits[0].published.startswith("2026-10-21")


# ---------------------------------------------------------------- 客户端

def test_client_sends_claw_headers_and_caches(tmp_path):
    s = FakeSession()
    c = client(tmp_path, s)
    t1 = c.query("人形机器人概念股")
    t2 = c.query("人形机器人概念股")
    assert len(s.posts) == 1 and not t1.cached and t2.cached and c.cache_hits == 1
    url, body, headers = s.posts[0]
    assert url == "https://openapi.iwencai.com/v1/query2data"
    assert headers["Authorization"] == f"Bearer {KEY}"
    assert headers["X-Claw-Skill-Id"] == wencai.STOCKS and len(headers["X-Claw-Trace-Id"]) == 64
    assert body["query"] == "人形机器人概念股" and body["limit"] == "50"
    # 密钥不能落盘
    for f in (tmp_path / "topics").rglob("*"):
        if f.is_file():
            assert KEY not in f.read_text(encoding="utf-8")
    assert wencai.usage(tmp_path, days=3650)[0]["calls"] == 1


def test_search_uses_search_skill_and_version(tmp_path):
    s = FakeSession()
    hits = client(tmp_path, s).search("人形机器人", "report", 5)
    assert hits and s.posts[0][2]["X-Claw-Skill-Id"] == "report-search"
    assert s.posts[0][2]["X-Claw-Skill-Version"] == "1.0.1" and s.posts[0][1]["channels"] == ["report"]


def test_budget_stops_calls(tmp_path):
    c = client(tmp_path, max_calls=1)
    c.query("a")
    with pytest.raises(WencaiFatal):
        c.query("b")


def test_quota_message_is_fatal(tmp_path):
    s = FakeSession(lambda *a: Resp(payload={"status_code": 1001, "status_msg": "今日调用额度已用完"}))
    with pytest.raises(WencaiFatal):
        client(tmp_path, s).query("a")


def test_other_errors_are_per_query_but_repeated_auth_failures_stop(tmp_path):
    s = FakeSession(lambda *a: Resp(status=401, payload={}, text="skill not allowed"))
    c = client(tmp_path, s)
    for _ in range(2):
        with pytest.raises(WencaiError) as e:
            c.query("a")
        assert not isinstance(e.value, WencaiFatal)
    with pytest.raises(WencaiFatal):
        c.query("a")


def test_query_all_pages(tmp_path):
    pages = {"1": {**MARKET, "code_count": 4, "datas": MARKET["datas"][:2]},
             "2": {**MARKET, "code_count": 4, "datas": MARKET["datas"][2:]}}
    s = FakeSession(lambda url, body, h: Resp(payload=pages.get(body["page"], {**MARKET, "datas": []})))
    t = client(tmp_path, s).query_all("x", limit=2, max_pages=5)
    assert len(t.rows) == 3 and [p[1]["page"] for p in s.posts] == ["1", "2", "3"]


# ---------------------------------------------------------------- 知识层

def test_all_topic_files_are_valid():
    topics = kb.all_topics()
    assert {t.title for t in topics} >= {"机器人", "券商", "AI应用"}
    for t in topics:
        assert t.stocks(), t.title
        assert all(s.code == "" or len(s.code) == 6 for s in t.stocks()), t.title
    assert kb.find("非银金融").title == "券商" and kb.find("robot").title == "机器人"


# ---------------------------------------------------------------- 本地数据

def write_day(root: Path, day: str, items=(), limitups=None, meta=None, suffix="-close"):
    d = root / day
    d.mkdir(parents=True, exist_ok=True)
    with open(d / f"items{suffix}.jsonl", "w", encoding="utf-8") as f:
        for it in items:
            f.write(json.dumps(it, ensure_ascii=False) + "\n")
    if limitups is not None:
        (d / f"limitup{suffix}.json").write_text(json.dumps(limitups, ensure_ascii=False), encoding="utf-8")
    if meta is not None:
        (d / f"meta{suffix}.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")


def news_item(uid, title, published, score=7.0):
    return {"uid": uid, "title": title, "published": published, "score": score, "source_name": "财联社电报",
            "url": f"https://x/{uid}", "dup_count": 3}


@pytest.fixture
def data_dir(tmp_path):
    root = tmp_path / "data"
    zt = {"trade_date": "2026-10-20", "stocks": [
        {"code": "603278", "name": "大业股份", "boards": 3, "stat": "3天3板", "reason": "人形机器人腱绳概念"},
        {"code": "002313", "name": "日海智能", "boards": 1, "reason": "端侧AI概念", "ths_reason": "端侧AI+机器人应用"}]}
    meta = {"ashare": {"trade_date": "2026-10-20", "turnover": 15000, "concepts": [
        {"name": "人形机器人", "pct": 3.44, "inflow": 50.6, "leader": "福莱新材", "kind": "concept"},
        {"name": "白酒", "pct": 1.0, "kind": "concept"}], "industries": []}}
    write_day(root, "2026-10-20", [
        news_item("a", "特斯拉称Optimus已为量产做好准备", "2026-10-20T09:00:00+08:00", 8.5),
        news_item("b", "某扫地机器人新品发布", "2026-10-20T10:00:00+08:00", 8.0),
        news_item("c", "央行降准", "2026-10-20T11:00:00+08:00", 9.0),
    ], zt, meta)
    # 第二天的盘前版又存了一次前一交易日的涨停和板块：不能重复计
    write_day(root, "2026-10-21", [
        news_item("a", "特斯拉称Optimus已为量产做好准备", "2026-10-20T09:00:00+08:00", 8.5),
        news_item("d", "人形机器人概念持续走高", "2026-10-21T14:00:00+08:00", 7.0),
    ], zt, meta, suffix="")
    return root


def test_local_news_matches_title_and_excludes(data_dir):
    t = kb.find("机器人")
    items = local.news(t, data_dir, today=date(2026, 10, 21))
    assert [n.title for n in items] == ["特斯拉称Optimus已为量产做好准备", "人形机器人概念持续走高"]
    assert local.timeline(items, min_score=6.5)[0].title == "人形机器人概念持续走高"
    h = local.heat(items, today=date(2026, 10, 21), recent=5, base=15)
    assert h["recent"] == pytest.approx(2 / 5)


def test_local_limitups_and_boards_dedupe_across_editions(data_dir):
    t = kb.find("机器人")
    zt = local.limitups(t, data_dir, today=date(2026, 10, 21))
    assert [z["name"] for z in zt] == ["大业股份"]   # 日海智能只在同花顺原因里提到机器人，不算
    bd = local.boards(t, data_dir, today=date(2026, 10, 21))
    assert [(b["date"], b["name"]) for b in bd] == [("2026-10-20", "人形机器人")]


# ---------------------------------------------------------------- 数据层与阶段

def test_collect_parses_members_index_and_breadth(tmp_path):
    t = kb.find("机器人")
    wd = fetch.collect(t, client(tmp_path))
    by = {m.name: m for m in wd.members}
    assert by["拓普集团"].mcap == 1218 and by["拓普集团"].ret20 == 8.2 and by["拓普集团"].ret60 == 25.4
    assert by["拓普集团"].amount20 == 456 and by["拓普集团"].pe == 35.2 and by["拓普集团"].np_yoy == 12.1
    assert by["蹭概念"].ret60 is None
    assert by["拓普集团"].tier == "核心" and "执行器总成（旋转、线性关节）" in by["拓普集团"].segments
    assert by["五洲新春"].tier == "相关"                      # 知识层名单
    assert by["蹭概念"].tier == "" and not by["蹭概念"].segments
    assert wd.index.close == 1500 and wd.index.ma20 == 1400 and wd.index.ma60 == 1300 and wd.index.ret20 == 15
    assert wd.bench.ret20 == 1.0
    assert wd.breadth == {"above_ma20": 2, "above_ma60": 2, "high60": 1}
    assert wd.news and wd.reports and wd.notices and not wd.errors
    # 知识层的三花智控不在概念成分里：补查到了，但不参与中军、强势股排名
    assert by["三花智控"].extra and by["三花智控"].tier == "核心" and by["三花智控"].ret20 == 50
    lead = fetch.leaders(wd.members)
    assert [m.name for m in lead["core"]] == ["拓普集团"] and [m.name for m in lead["strong"]] == ["五洲新春", "蹭概念"]
    seg = {s["segment"]: s for s in fetch.segment_stats(wd.members, t)}
    assert seg["丝杠（行星滚柱、滚珠）"]["ret20"] == 31.5


def test_collect_stops_after_fatal(tmp_path):
    s = FakeSession(lambda *a: Resp(payload={"status_code": 1, "status_msg": "额度不足"}))
    wd = fetch.collect(kb.find("机器人"), client(tmp_path, s))
    assert wd.fatal and len(s.posts) == 1


def wd_with(close, ma20, ma60, ret20, ret60, bench20=1.0, total=10, b20=5, b60=5, hi=0):
    return WencaiData(members=[Member("000001", "x", ret20=ret20)], total=total,
                      breadth={"above_ma20": b20, "above_ma60": b60, "high60": hi},
                      index=IndexStat("x", close, ma20, ma60, ret20, ret60), bench=IndexStat("沪深300", ret20=bench20))


@pytest.mark.parametrize("args,expected", [
    ((110, 100, 90, 5, 8), "发酵"),
    ((110, 100, 90, 15, 20), "主升"),                 # 跑赢沪深 300 超 10 个点
    ((95, 100, 90, -3, 5), "分歧"),
    ((105, 100, 110, 4, -5), "启动"),
    ((80, 100, 110, -8, -15), "退潮"),
    ((99, 100, 101, 0.5, -2), "蛰伏"),
])
def test_stage_rules(args, expected):
    assert judge(wd_with(*args)).name == expected


def test_stage_falls_back_to_breadth_without_index_ma():
    wd = wd_with(None, None, None, 6, 10, total=10, b20=7, b60=6, hi=3)
    st = judge(wd)
    assert st.name == "主升" and any("过半成分股" in r for r in st.reasons)
    assert judge(None).name == "数据不足"


# ---------------------------------------------------------------- 档案卡、快照、静态站

def test_build_card_with_wencai(tmp_path, data_dir):
    out = tmp_path / "reports" / "topics"
    t = kb.find("机器人")
    r = build(t, client(tmp_path / "w"), data_dir, out, AT)
    md = r.path.read_text(encoding="utf-8")
    assert r.stage.name == "主升"   # 指数站上两条均线，20 日跑赢沪深 300 14 个点
    for section in ("## 速览", "## 三、产业链", "## 四、炒到哪了", "## 五、个股梯队与正宗度", "## 八、历史行情",
                    "## 九、风险与证伪", "## 数据说明"):
        assert section in md
    assert "| 趋势中军 | 拓普集团（601689）" in md and "知识层和主营都对不上，可能是蹭概念" in md
    assert "3 只里，1 只的主营产品和知识层名单都对不上" in md and "涨得最多的：蹭概念 +4.5%" in md
    assert "**拓普集团** +8.2%" in md and "（凭记忆，待核实）" in md
    assert "大业股份（603278）" in md and "人形机器人 | +3.44%" in md
    snap = json.loads((data_dir / "topics" / "2026-10-21" / "robot.json").read_text(encoding="utf-8"))
    assert snap["stage"] == "主升" and {m["name"] for m in snap["members"]} == {"拓普集团", "五洲新春", "蹭概念", "三花智控"}
    assert "不在问财“人形机器人概念股”成分里" in md


def test_build_all_offline_and_site(tmp_path, data_dir):
    out = tmp_path / "reports" / "topics"
    results = build_all(None, None, data_dir, out, AT)
    assert {r.stage.name for r in results} == {"数据不足"}
    assert (out / "index.md").exists() and "[机器人](robot.md)" in (out / "index.md").read_text(encoding="utf-8")
    site = build_site(tmp_path / "_site", reports_dir=tmp_path / "reports")
    page = (site / "topics" / "robot.html").read_text(encoding="utf-8")
    assert "机器人 · 题材档案" in page and 'href="robot.html"' in (site / "topics" / "index.html").read_text(encoding="utf-8")
