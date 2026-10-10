import json
from datetime import datetime, timedelta

from finhot import ashare, calendar_watch
from finhot.crosscheck import cross_check
from finhot.editions import EDITIONS, edition_by_time, scheduled_slot, window_since
from finhot.models import NewsItem
from finhot.timeutil import TZ

from fixtures import (BOARDS_CON, BOARDS_IND, FENBU, INDICES, IPO, KAMT, LHB, LIFTS, WSCN_CAL, ZT_POOL)


def snapshot() -> ashare.MarketSnapshot:
    s = ashare.MarketSnapshot()
    s.trade_date, s.zt_stocks = ashare.parse_zt_pool(ZT_POOL)
    s.ladder = ashare.build_ladder(s.zt_stocks)
    s.zt_industries = ashare.zt_by_industry(s.zt_stocks)
    s.industries = ashare.parse_boards(BOARDS_IND, "industry")
    s.concepts = ashare.parse_boards(BOARDS_CON, "concept")
    s.indices, s.turnover = ashare.parse_indices(INDICES)
    s.breadth = {**ashare.parse_fenbu(FENBU), "zt": 4, "dt": 8, "zb": 1, "seal_rate": 80.0}
    s.lhb_date, rows = ashare.parse_lhb(LHB)
    s.lhb_buy = [r for r in rows if r["net"] > 0]
    s.lhb_sell = [r for r in rows if r["net"] < 0]
    s.hsgt = ashare.parse_hsgt(KAMT)
    return s


def test_ashare_parsers():
    s = snapshot()
    assert s.trade_date == "2026-10-09"
    assert s.ladder[0] == {"boards": 5, "stocks": ["时代万恒"]} and s.ladder[1]["boards"] == 4
    assert s.zt_industries[0]["name"] == "电池" and s.zt_industries[0]["count"] == 3
    assert [c.name for c in s.concepts] == ["AI语料"]           # 统计型概念板块被剔除
    assert s.industries[1].inflow == 50.44                        # 元 → 亿
    assert s.turnover == round(8885.6 + 10119.9 + 163.3, 1)        # 沪+深+北
    assert s.breadth["up"] == 1039 and s.breadth["down"] == 754 and s.breadth["flat"] == 103
    assert s.lhb_date == "2026-10-09" and len(s.lhb_buy) == 1 and s.lhb_buy[0]["net"] == 1.88  # 同股去重、只取最新日期
    assert s.hsgt["north_turnover"] == 2967.4 and s.hsgt["south_net"] == 2.9


def test_calendar_parsers():
    data, events = calendar_watch.parse_wscn_calendar(WSCN_CAL)
    assert [r["country"] for r in data] == ["中国", "美国"]       # 重要性<3 的被过滤，按时间排序
    assert data[1]["forecast"] == "3.6%" and data[0]["forecast"] == "-"
    assert events[0]["title"] == "2026世界储能大会" and events[0]["time"] == "待定"   # 12:02 视为待定
    [ipo] = calendar_watch.parse_ipo(IPO)
    assert ipo["apply_code"] == "001381" and ipo["price"] is None
    lifts = calendar_watch.parse_lifts(LIFTS)
    assert lifts[0]["name"] == "陕西能源" and lifts[0]["cap"] == 238.6 and lifts[0]["date"] == "10-12"


def item(title, score=7.0, sectors=None):
    it = NewsItem(source="cls", source_name="财联社", title=title, content=title, url="",
                  published=datetime(2026, 10, 9, 20, tzinfo=TZ), source_id=title, score=score)
    if sectors:
        it.llm = {"sectors": sectors}
    return it


def test_cross_check_verdicts():
    s = snapshot()
    items = [item("宁德时代发布新一代固态电池"), item("多家电池企业宣布扩产储能电池"),
             item("银行业净息差继续收窄"), item("国有大行下调存款利率"), item("银行间市场流动性宽松", sectors=["银行"])]
    rows = {r.theme: r for r in cross_check(items, s)}
    assert rows["锂电储能"].verdict == "共振" and rows["锂电储能"].zt == 3
    assert rows["传媒游戏"].verdict == "资金先行"
    assert rows["银行"].verdict == "新闻热·资金冷"


def test_editions_and_window(tmp_path):
    t = datetime(2026, 10, 12, 5, 47, tzinfo=TZ)   # 周一清晨
    assert edition_by_time(t).key == "pre" and edition_by_time(t.replace(hour=16)).key == "close"
    reports = tmp_path / "reports"
    reports.mkdir()
    assert scheduled_slot(t, reports).key == "pre"
    (reports / "2026-10-12.md").write_text("x")
    assert scheduled_slot(t, reports) is None                       # 已生成则跳过
    assert scheduled_slot(t.replace(hour=16), reports).key == "close"
    assert scheduled_slot(datetime(2026, 10, 10, 16, tzinfo=TZ), reports) is None   # 周六不出盘后版
    assert scheduled_slot(t.replace(hour=13), reports) is None

    data = tmp_path / "data"
    (data / "2026-10-09").mkdir(parents=True)
    (data / "2026-10-09" / "meta-close.json").write_text(json.dumps({"until": "2026-10-09T16:00:00+08:00"}))
    assert window_since(t, None, data) == datetime(2026, 10, 9, 16, tzinfo=TZ)   # 周一盘前覆盖整个周末
    later = datetime(2026, 10, 14, 6, tzinfo=TZ)
    assert window_since(later, None, data) == later - timedelta(hours=72)       # 断档太久夹到 72 小时上限
    since = window_since(datetime(2026, 10, 9, 22, tzinfo=TZ), None, data)
    assert since == datetime(2026, 10, 9, 16, tzinfo=TZ)              # 接着上一份
    assert window_since(t, 12, data) == t - timedelta(hours=12)
    assert EDITIONS["close"].suffix == "-close"


def test_overseas_news_only_counts_global_themes():
    from finhot.themes import item_themes
    us_bank = item("美国银行存款升至19.69万亿美元")
    us_bank.category = "海外"
    assert item_themes(us_bank) == []
    nvda = item("英伟达发布新一代GPU")
    nvda.category = "海外"
    assert item_themes(nvda) == ["算力"]


def test_auto_weekly_failure_does_not_block_daily(monkeypatch, capsys):
    import finhot.__main__ as cli
    import finhot.editions as editions
    import finhot.pipeline as pipeline
    import finhot.weekly as weekly
    monkeypatch.setattr(cli, "now", lambda: datetime(2026, 10, 10, 7, 0, tzinfo=TZ))   # 周六 07:00
    monkeypatch.setattr(editions, "scheduled_slot", lambda t: EDITIONS["pre"])
    monkeypatch.setattr(pipeline, "run", lambda **kw: "reports/2026-10-10.md")
    monkeypatch.setattr(weekly, "weekly_exists", lambda d: False)

    def boom(*a, **k):
        raise SystemExit("没有数据")
    monkeypatch.setattr(weekly, "run_weekly", boom)
    assert cli.main(["auto", "--no-llm"]) == 0
    out = capsys.readouterr().out
    assert "reports/2026-10-10.md" in out and "周报生成失败" in out
