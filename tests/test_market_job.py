"""服务器上的盘面任务：什么时候跑、怎么从精选里读出信号、推给网站的数据长什么样。不访问网络。"""

import json
from datetime import datetime

from finhot import market_job
from finhot.ashare import MarketSnapshot
from finhot.backtest import Signals
from finhot.fetchers.base import FetchResult
from finhot.market_job import due, run_market, selected_signals
from finhot.timeutil import TZ

from test_backtest import chip_board, flat
from test_pipeline import item


def at(month_day, hh, mm=0):
    return datetime(2026, 10, month_day, hh, mm, tzinfo=TZ)


def test_runs_once_after_the_close_on_weekdays_and_backs_off_after_a_failure():
    assert due(at(12, 15, 20), None, None)
    assert not due(at(12, 14, 50), None, None)                      # 还没收盘
    assert not due(at(11, 16, 0), None, None)                       # 周日
    assert not due(at(12, 16, 0), "2026-10-12", None)               # 今天推过了
    assert not due(at(12, 15, 30), None, at(12, 15, 15).isoformat())  # 15 分钟前刚失败
    assert due(at(12, 15, 50), None, at(12, 15, 15).isoformat())


def test_selected_items_with_a_direction_become_theme_signals():
    sigs = Signals()
    n = selected_signals([
        {"title": "存储芯片涨价", "reason": "利好 半导体、存储，依据：多家厂商上调合约价", "publishedAt": "2026-10-09T01:00:00Z"},
        {"title": "某银行被罚", "reason": "利空 银行，依据：监管罚单", "publishedAt": "2026-10-09T02:00:00Z"},
        {"title": "海外数据", "reason": "中性 海外宏观，依据：符合预期", "publishedAt": "2026-10-09T02:00:00Z"},
        {"title": "没写影响", "reason": None, "publishedAt": "2026-10-09T02:00:00Z"},
    ], sigs)
    got = sorted((s.kind, s.theme) for s in sigs.values())
    assert n == 2 and got == [("精选利好", "半导体"), ("精选利空", "银行")]
    assert sigs.values()[0].at == at(9, 9)  # UTC → 北京时间


class FakeEngine:
    def __init__(self, selected=()):
        self.pushed, self._selected = [], list(selected)

    def selected(self):
        return self._selected

    def push(self, kind, key, data):
        self.pushed.append((kind, key, data))


class FakePrices:
    def boards(self):
        return [{"code": "BK1", "name": "半导体", "kind": "industry"}]

    def bars(self, secid, start):
        return flat() if secid == "1.000852" else chip_board()


def snapshot(trade_date):
    return lambda: MarketSnapshot(trade_date=trade_date, breadth={"zt": 1}, zt_stocks=[
        {"code": "300821", "name": "东岳硅材", "industry": "化学制品", "boards": 1, "first_time": "0938", "stat": ""}])


def test_a_trading_day_pushes_the_limit_ups_and_the_record(tmp_path, monkeypatch):
    news = [item("cls", "东岳硅材：预计前三季度净利润同比增长19050%")]
    monkeypatch.setattr(market_job, "collect", lambda sources, since: [FetchResult("cls", "财联社", items=news)])
    engine = FakeEngine([{"title": "芯片利好", "reason": "利好 半导体，依据：涨价", "publishedAt": "2026-10-09T00:00:00Z"}])
    msg = run_market(engine, [], tmp_path, at=at(15, 15, 20), snapshot=snapshot("2026-10-15"), prices=FakePrices())
    kinds = [p[0] for p in engine.pushed]
    assert kinds == ["limitup", "record"] and "涨停 1 只" in msg
    day = engine.pushed[0][2]
    assert day["trade_date"] == "2026-10-15" and day["stocks"][0]["confidence"] == "高"
    assert json.loads((tmp_path / "2026-10-15" / "limitup.json").read_text())["stocks"][0]["name"] == "东岳硅材"
    record = engine.pushed[1][2]
    row = next(r for r in record["rows"] if r["kind"] == "精选利好")
    assert row["n"] == 1 and row["reaction_1_mean"] == 10.0 and record["benchmark"] == "中证1000"
    assert record["recent"][0]["reaction"]["1"] == 10.0


def test_a_holiday_pushes_nothing(tmp_path):
    engine = FakeEngine()
    msg = run_market(engine, [], tmp_path, at=at(13, 15, 20), snapshot=snapshot("2026-10-12"), prices=FakePrices())
    assert engine.pushed == [] and "不是交易日" in msg
