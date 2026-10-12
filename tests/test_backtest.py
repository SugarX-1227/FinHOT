"""回测：信号从哪里来、怎么合并、两种口径的收益怎么算、胜率怎么判。价格用假的，不访问网络。"""

import json
from datetime import date, datetime, timedelta

from finhot import backtest as bt
from finhot.backtest import Bar, Signal, evaluate, load_signals, parse_board_list, parse_klines, summarize, theme_boards
from finhot.timeutil import TZ

DAYS = [date(2026, 10, 8), date(2026, 10, 9), date(2026, 10, 12), date(2026, 10, 13), date(2026, 10, 14), date(2026, 10, 15)]


def at(d, hh, mm=0):
    return datetime(2026, 10, d, hh, mm, tzinfo=TZ)


def flat(code="BENCH"):
    return [Bar(d, 100.0, 100.0) for d in DAYS]


def chip_board():
    # 10/9 高开到 105、收 110；10/12 收 121，之后横盘
    closes = [100, 110, 121, 121, 121, 121]
    opens = [100, 105, 115, 121, 121, 121]
    return [Bar(d, float(o), float(c)) for d, o, c in zip(DAYS, opens, closes)]


MAPPING = {"半导体": [{"code": "BK1", "name": "半导体", "kind": "industry"}]}


def test_reaction_counts_from_the_last_close_before_the_news_and_tradable_from_the_next_open():
    sigs = [Signal("利好新闻", at(9, 8), "半导体", "盘前利好"),
            Signal("利好新闻", at(9, 10), "半导体", "盘中利好"),
            Signal("利好新闻", at(9, 20), "半导体", "盘后利好")]
    pre, intraday, after = evaluate(sigs, {"BK1": chip_board()}, flat(), MAPPING)
    assert (pre.reaction[1], pre.reaction[3]) == (10.0, 21.0)          # 10/8 收盘 → 10/9、10/13 收盘
    assert pre.tradable[1] == round((110 / 105 - 1) * 100, 2)            # 10/9 开盘买 → 当天收盘
    assert intraday.reaction[1] == 10.0                                  # 盘中消息：基准仍是前一天收盘
    assert intraday.tradable[1] == round((121 / 115 - 1) * 100, 2)       # 只能下一个交易日开盘买
    assert after.reaction[1] == 10.0 and after.reaction[5] is None       # 10/9 收盘 → 10/12；T+5 还没到


def test_win_rate_follows_the_signal_direction():
    sigs = [Signal("利好新闻", at(9, 8), "半导体", "a"), Signal("利空新闻", at(9, 8), "半导体", "b"),
            Signal("资金先行", at(9, 8), "半导体", "c")]
    rows = {r["kind"]: r for r in summarize(evaluate(sigs, {"BK1": chip_board()}, flat(), MAPPING))}
    assert rows["利好新闻"]["reaction_1_win"] == 100 and rows["利空新闻"]["reaction_1_win"] == 0
    assert rows["资金先行"]["reaction_1_win"] is None and rows["资金先行"]["reaction_1_mean"] == 10.0


def test_a_theme_without_boards_or_prices_is_left_out_not_guessed():
    o = evaluate([Signal("利好新闻", at(9, 8), "前沿科技", "x")], {}, flat(), MAPPING)[0]
    assert o.boards == [] and o.reaction == {1: None, 3: None, 5: None}


def write(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def news(title, published, direction="利好", score=8, sectors=("半导体",)):
    return {"source": "cls", "source_name": "财联社", "title": title, "content": title, "url": "https://x/" + title,
            "published": published.isoformat(), "category": "行业",
            "llm": {"direction": direction, "score": score, "sectors": list(sectors), "title": title}}


def test_signals_are_read_from_the_saved_reports_and_merged_per_day_theme_and_kind(tmp_path):
    write(tmp_path / "2026-10-09" / "items.jsonl", [
        news("芯片利好一", at(9, 7)), news("芯片利好二", at(9, 8)),          # 同一天同题材：合并成一个信号
        news("芯片利好三", at(9, 16)),                                       # 收盘后：算下一个交易日的信号
        news("分数不够", at(9, 8), score=6), news("中性", at(9, 8), direction="中性")])
    (tmp_path / "2026-10-09" / "meta.json").write_text(json.dumps(
        {"until": at(9, 8).isoformat(), "themes": [{"theme": "黄金", "verdict": "共振", "news": 5, "zt": 1}]}), encoding="utf-8")
    (tmp_path / "2026-10-10").mkdir()
    (tmp_path / "2026-10-10" / "limitup.json").write_text(json.dumps({"trade_date": "2026-10-09", "stocks": [
        {"name": n, "themes": ["传媒游戏"]} for n in ("上海电影", "中文在线", "芒果超媒")]}), encoding="utf-8")
    sigs = load_signals(tmp_path)
    got = sorted((s.kind, s.theme, s.count) for s in sigs)
    assert got == [("共振", "黄金", 1), ("利好新闻", "半导体", 1), ("利好新闻", "半导体", 2), ("涨停主线", "传媒游戏", 1)]
    lead = next(s for s in sigs if s.kind == "涨停主线")
    assert lead.at == at(9, 15) and "上海电影" in lead.title


def test_boards_prefer_industry_boards_and_kline_parsing():
    boards = parse_board_list({"data": {"diff": [{"f12": "BK0917", "f14": "半导体"}, {"f12": "BK1", "f14": ""}]}}, "industry")
    boards += parse_board_list({"data": {"diff": [{"f12": "BK1036", "f14": "半导体概念"}, {"f12": "BK0985", "f14": "黄金概念"}]}}, "concept")
    m = theme_boards({"半导体", "黄金", "前沿科技"}, boards)
    assert [b["code"] for b in m["半导体"]] == ["BK0917"] and [b["code"] for b in m["黄金"]] == ["BK0985"]
    assert "前沿科技" not in m
    bars = parse_klines({"data": {"klines": ["2026-10-09,105.0,110.0,111,104,1,1", "bad"]}})
    assert bars == [Bar(date(2026, 10, 9), 105.0, 110.0)]


class FakeSource:
    def boards(self):
        return [{"code": "BK1", "name": "半导体", "kind": "industry"}]

    def bars(self, secid, start):
        return flat() if secid == bt.BENCHMARK[0] else chip_board()


def test_a_bar_that_has_not_closed_yet_is_not_used(tmp_path, monkeypatch):
    data = tmp_path / "data"
    write(data / "2026-10-09" / "items.jsonl", [news("芯片利好", at(9, 8))])
    monkeypatch.setattr(bt, "now", lambda: at(13, 10))  # 10/13 盘中：只能用到 10/12 的收盘
    bt.run_backtest(days=30, out_dir=tmp_path / "out", data_dir=data, source=FakeSource())
    row = json.loads((tmp_path / "out" / "data" / "backtest" / "outcomes.jsonl").read_text().splitlines()[0])
    assert row["reaction"]["1"] == 10.0 and row["reaction"]["3"] is None


def test_run_writes_the_report_and_the_outcomes(tmp_path, monkeypatch):
    data = tmp_path / "data"
    write(data / "2026-10-09" / "items.jsonl", [news("芯片利好", at(9, 8))])
    monkeypatch.setattr(bt, "now", lambda: at(15, 20))
    path = bt.run_backtest(days=30, out_dir=tmp_path / "out", data_dir=data, source=FakeSource())
    md = path.read_text(encoding="utf-8")
    assert "| 利好新闻 | 1 | +10.00% · 100%<br><sub>1 次</sub>" in md
    rows = [json.loads(line) for line in (tmp_path / "out" / "data" / "backtest" / "outcomes.jsonl").read_text().splitlines()]
    assert rows[0]["signal"]["theme"] == "半导体" and rows[0]["reaction"]["3"] == 21.0


class DownSource(FakeSource):
    def bars(self, secid, start):
        raise ConnectionError("Remote end closed connection without response")


def test_no_benchmark_prices_means_no_result_and_the_previous_report_stays(tmp_path, monkeypatch):
    data = tmp_path / "data"
    write(data / "2026-10-09" / "items.jsonl", [news("芯片利好", at(9, 8))])
    monkeypatch.setattr(bt, "now", lambda: at(15, 20))
    old = tmp_path / "out" / "backtest" / "latest.md"
    old.parent.mkdir(parents=True)
    old.write_text("上一次的结果", encoding="utf-8")
    try:
        bt.run_backtest(days=30, out_dir=tmp_path / "out", data_dir=data, source=DownSource())
        raise AssertionError("expected PricesUnavailable")
    except bt.PricesUnavailable as e:
        assert "中证1000" in str(e)
    assert old.read_text(encoding="utf-8") == "上一次的结果"


def test_selected_items_with_a_direction_become_theme_signals():
    sigs = bt.Signals()
    n = bt.selected_signals([
        {"title": "存储芯片涨价", "reason": "利好 半导体、存储，依据：多家厂商上调合约价", "publishedAt": "2026-10-09T01:00:00Z"},
        {"title": "某银行被罚", "reason": "利空 银行，依据：监管罚单", "publishedAt": "2026-10-09T02:00:00Z"},
        {"title": "海外数据", "reason": "中性 海外宏观，依据：符合预期", "publishedAt": "2026-10-09T02:00:00Z"},
        {"title": "没写影响", "reason": None, "publishedAt": "2026-10-09T02:00:00Z"},
    ], sigs)
    got = sorted((s.kind, s.theme) for s in sigs.values())
    assert n == 2 and got == [("精选利好", "半导体"), ("精选利空", "银行")]
    assert sigs.values()[0].at == at(9, 9)  # UTC → 北京时间


class FakeSite:
    def __init__(self, selected=(), token="t" * 20, down=False):
        self._selected, self.token, self.down, self.pushed = list(selected), token, down, []

    def selected(self):
        if self.down:
            raise ConnectionError("site unreachable")
        return self._selected

    def push(self, kind, key, data):
        self.pushed.append((kind, key, data))


def test_with_the_site_configured_its_selected_calls_join_and_the_record_is_pushed(tmp_path, monkeypatch):
    data = tmp_path / "data"
    write(data / "2026-10-09" / "items.jsonl", [news("芯片利好", at(9, 8))])
    monkeypatch.setattr(bt, "now", lambda: at(15, 20))
    site = FakeSite([{"title": "存储涨价", "reason": "利好 半导体，依据：涨价", "publishedAt": "2026-10-09T00:30:00Z"}])
    bt.run_backtest(days=30, out_dir=tmp_path / "out", data_dir=data, source=FakeSource(), site=site)
    assert [(k, key) for k, key, _ in site.pushed] == [("record", "2026-10-15")]
    rows = {r["kind"]: r for r in site.pushed[0][2]["rows"]}
    assert rows["精选利好"]["reaction_1_mean"] == 10.0 and rows["利好新闻"]["n"] == 1


def test_an_unreachable_site_still_gives_the_static_record(tmp_path, monkeypatch):
    data = tmp_path / "data"
    write(data / "2026-10-09" / "items.jsonl", [news("芯片利好", at(9, 8))])
    monkeypatch.setattr(bt, "now", lambda: at(15, 20))
    site = FakeSite(down=True)
    md = bt.run_backtest(days=30, out_dir=tmp_path / "out", data_dir=data, source=FakeSource(), site=site).read_text()
    assert "| 利好新闻 | 1 |" in md and "网站精选读取失败：ConnectionError" in md
    assert [k for k, _, _ in site.pushed] == ["record"]
