"""测试不能改仓库里的 sources/eastmoney_boards.json：板块列表一律写到临时目录。"""

import pytest

from finhot import backtest


@pytest.fixture(autouse=True)
def _boards_in_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(backtest, "BOARDS", tmp_path / "eastmoney_boards.json")
