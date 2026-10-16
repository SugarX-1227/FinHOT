"""测试不能改仓库里的文件、不能访问网络：
- 板块列表一律写到临时目录（不改 sources/eastmoney_boards.json）；
- 股票清单不同步（会联网、会改 sources/entities/stocks.json）；
- 公告源默认为空（要测公告的用例自己传 sources 或桩函数）；
- 个人画像、个人数据目录放在临时目录（不碰仓库里的 profiles/、.private/）。"""

import pytest

from finhot import announce, backtest, entities


@pytest.fixture(autouse=True)
def _boards_in_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(backtest, "BOARDS", tmp_path / "eastmoney_boards.json")


@pytest.fixture(autouse=True)
def _offline_announcements(monkeypatch):
    monkeypatch.setattr(announce, "announcement_sources", lambda cfg=None: [])


@pytest.fixture(autouse=True)
def _private_in_tmp(tmp_path, monkeypatch):
    monkeypatch.setenv("FINHOT_PROFILES_DIR", str(tmp_path / "profiles"))
    monkeypatch.setenv("FINHOT_PRIVATE_DIR", str(tmp_path / "private"))
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)


@pytest.fixture(autouse=True)
def _no_entity_sync(monkeypatch):
    def refuse(*a, **k):
        raise RuntimeError("测试里不同步股票清单")
    monkeypatch.setattr(entities, "sync", refuse)
