"""服务器上的盘面任务：什么时候跑、推给网站的涨停原因长什么样、接口没取到时报错而不是当成节假日。不访问网络。"""

import json
from datetime import datetime

import pytest

from finhot import market_job
from finhot.ashare import MarketSnapshot
from finhot.fetchers.base import FetchResult
from finhot.market_job import due, run_market
from finhot.timeutil import TZ

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


class FakeSite:
    def __init__(self):
        self.pushed = []

    def push(self, kind, key, data):
        self.pushed.append((kind, key, data))


def snapshot(trade_date, stocks=True, errors=()):
    zt = [{"code": "300821", "name": "东岳硅材", "industry": "化学制品", "boards": 1, "first_time": "0938", "stat": ""}]
    return lambda: MarketSnapshot(trade_date=trade_date, breadth={"zt": 1}, zt_stocks=zt if stocks else [],
                                  zt_reasons={"300821": "有机硅+业绩预增"}, errors=list(errors))


def test_a_trading_day_pushes_the_limit_ups(tmp_path, monkeypatch):
    news = [item("cls", "东岳硅材：预计前三季度净利润同比增长19050%")]
    monkeypatch.setattr(market_job, "collect", lambda sources, since: [FetchResult("cls", "财联社", items=news)])
    site = FakeSite()
    msg = run_market(site, [], tmp_path, at=at(15, 15, 20), snapshot=snapshot("2026-10-15"))
    assert [p[0] for p in site.pushed] == ["limitup"] and "涨停 1 只" in msg and "同花顺原因 1 条" in msg
    day = site.pushed[0][2]
    assert day["trade_date"] == "2026-10-15" and day["stocks"][0]["reason"] == "有机硅+业绩预增"
    assert day["stocks"][0]["confidence"] == "高"
    assert json.loads((tmp_path / "2026-10-15" / "limitup.json").read_text())["stocks"][0]["name"] == "东岳硅材"


def test_a_holiday_pushes_nothing(tmp_path):
    site = FakeSite()
    msg = run_market(site, [], tmp_path, at=at(13, 15, 20), snapshot=snapshot("2026-10-12"))
    assert site.pushed == [] and "不是交易日" in msg


def test_a_pool_the_api_did_not_return_is_a_failure_not_a_holiday(tmp_path):
    with pytest.raises(RuntimeError, match="涨停池没取到：涨停池: ConnectionError"):
        run_market(FakeSite(), [], tmp_path, at=at(15, 15, 20),
                   snapshot=snapshot("", stocks=False, errors=["涨停池: ConnectionError: Remote end closed"]))
