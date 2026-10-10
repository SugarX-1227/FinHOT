"""推送到网站引擎：选哪些条目、怎么分批、出错时怎么办。不访问网络。"""

from datetime import timedelta

import pytest

from finhot import push
from finhot.fetchers.base import FetchResult
from finhot.push import EngineError, PushConfig, Pusher, PushState, payload, select

from test_pipeline import T0, item

RRR = "中国人民银行决定于10月8日下调金融机构存款准备金率0.5个百分点。"
ORDER = "某某股份公告，公司与客户签订12亿元储能设备采购合同，约占上年营收的35%。"


def rrr_cluster():
    return [
        item("cls", "央行：下调存款准备金率0.5个百分点", f"【央行：下调存款准备金率0.5个百分点】财联社9月30日电，{RRR}",
             important=True, weight=1.5),
        item("wallstreetcn", "中国人民银行决定下调金融机构存款准备金率0.5个百分点", RRR, minutes=1),
        item("sina", "中国人民银行：决定于10月8日下调金融机构存款准备金率0.5个百分点", minutes=2),
    ]


def state_with(items, tmp_path) -> PushState:
    st = PushState(path=tmp_path / "state.json")
    st.add(items)
    return st


def test_select_pushes_every_source_of_an_important_event_and_drops_noise(tmp_path):
    noise = item("ths", "某某股份盘中涨超5%", minutes=3)
    st = state_with(rrr_cluster() + [noise], tmp_path)
    chosen = select(st, PushConfig(min_score=6))
    assert sorted(it.source for it, _ in chosen) == ["cls", "sina", "wallstreetcn"]
    assert all(score >= 6 for _, score in chosen)
    # 推过的不再推；每件事最多推几家，代表条目（信源标了重要的）优先
    st.pushed[chosen[0][0].uid] = "x"
    assert len(select(st, PushConfig(min_score=6))) == 2
    st.pushed.clear()
    two = select(st, PushConfig(min_score=6, max_per_event=2))
    assert [it.source for it, _ in two][0] == "cls" and len(two) == 2


def test_an_event_is_pushed_once_enough_sources_follow_it(tmp_path):
    first = item("cls", "某某股份：签订12亿元重大合同", ORDER, minutes=4)
    st = state_with([first], tmp_path)
    assert select(st, PushConfig(min_score=6)) == []
    st.add([item(s, "某某股份签订12亿元储能设备采购合同", ORDER, minutes=5 + i)
            for i, s in enumerate(["jin10", "eastmoney", "sina"])])
    chosen = select(st, PushConfig(min_score=6))
    assert {it.source for it, _ in chosen} == {"cls", "jin10", "eastmoney", "sina"}


def test_payload_keeps_the_full_text_and_gives_live_page_items_their_own_address():
    it = item("sina", "美国9月非农就业人口增加25万人", "美国9月非农就业人口增加25万人，失业率4.1%。")
    it.url, it.source_id = "https://finance.sina.com.cn/7x24/", "4321"
    p = payload(it, 7.5)
    assert p["url"] == "https://finance.sina.com.cn/7x24/?finhot_id=sina-4321"
    assert p["body"] == "美国9月非农就业人口增加25万人，失业率4.1%。"
    assert p["publishedAt"].endswith("+08:00") and p["raw"]["finhot"]["ruleScore"] == 7.5
    detail = item("cls", "标题")
    detail.url = "https://www.cls.cn/detail/123"
    assert payload(detail, 6)["url"] == "https://www.cls.cn/detail/123"


class FakeResponse:
    def __init__(self, status, body=None):
        self.status_code, self._body, self.text = status, body or {}, ""

    def json(self):
        return self._body


class FakeSession:
    def __init__(self, statuses):
        self.statuses, self.calls, self.trust_env = list(statuses), [], True

    def post(self, url, json, headers, timeout):
        self.calls.append((url, json, headers))
        status = self.statuses.pop(0) if self.statuses else 200
        return FakeResponse(status, {"ok": True, "created": len(json["items"])})


def pusher(statuses=()):
    session = FakeSession(statuses)
    return Pusher(PushConfig(engine_url="http://engine:3001", token="t" * 20), session=session), session


def test_push_batches_by_source_and_records_what_the_engine_accepted(tmp_path):
    many = [item("cls", f"第{i}条快讯", minutes=i) for i in range(60)]
    chosen = [(it, 7.0) for it in many + rrr_cluster()[1:]]
    st = state_with([it for it, _ in chosen], tmp_path)
    p, session = pusher()
    stats = p.push(chosen, st)
    assert stats["pushed"] == 62 and stats["created"] == 62
    sizes = [(c[1]["sourceId"], len(c[1]["items"])) for c in session.calls]
    assert sizes == [("cls", 50), ("cls", 10), ("wallstreetcn", 1), ("sina", 1)]
    assert session.calls[0][0] == "http://engine:3001/api/ingest/items"
    assert session.calls[0][2]["Authorization"] == "Bearer " + "t" * 20
    assert set(st.pushed) == {it.uid for it, _ in chosen}


def test_rate_limit_leaves_the_rest_for_next_round_and_a_paused_source_is_not_retried(tmp_path):
    chosen = [(it, 7.0) for it in rrr_cluster()]
    st = state_with([it for it, _ in chosen], tmp_path)
    p, _ = pusher([409, 429])
    stats = p.push(chosen, st)
    assert stats == {"pushed": 0, "created": 0, "skipped": 1, "failed": 2}
    assert list(st.pushed) == [chosen[0][0].uid]


def test_a_wrong_token_stops_the_loop(tmp_path):
    chosen = [(it, 7.0) for it in rrr_cluster()]
    p, _ = pusher([401])
    with pytest.raises(EngineError):
        p.push(chosen, state_with([it for it, _ in chosen], tmp_path))


def test_state_survives_a_restart_and_forgets_old_items(tmp_path):
    st = state_with(rrr_cluster(), tmp_path)
    st.pushed["old"] = (T0 - timedelta(days=3)).isoformat()
    st.pushed["new"] = T0.isoformat()
    st.last_fetch = T0
    st.save()
    back = PushState.load(tmp_path / "state.json")
    assert set(back.buffer) == set(st.buffer) and back.last_fetch == T0
    back.prune(T0 + timedelta(hours=1))
    assert set(back.pushed) == {"new"} and len(back.buffer) == 3
    back.prune(T0 + timedelta(hours=4))
    assert back.buffer == {}


def test_one_round_fetches_since_the_last_round_and_pushes(tmp_path, monkeypatch):
    seen = {}

    def fake_collect(sources, since, max_pages):
        seen["since"], seen["max_pages"] = since, max_pages
        return [FetchResult("cls", "CLS", items=rrr_cluster())]

    monkeypatch.setattr(push, "collect", fake_collect)
    monkeypatch.setattr(push, "now", lambda: T0 + timedelta(minutes=10))
    st = PushState(path=tmp_path / "state.json", last_fetch=T0 + timedelta(minutes=8))
    p, session = pusher()
    stats = push.run_once(PushConfig(min_score=6), st, p, [{"id": "cls"}])
    assert seen["since"] == T0 + timedelta(minutes=3) and seen["max_pages"] == push.MAX_PAGES
    assert stats["chosen"] == 3 and stats["pushed"] == 3 and len(session.calls) == 3
    assert (tmp_path / "state.json").exists()
    # 下一轮同样的条目不会再推
    stats = push.run_once(PushConfig(min_score=6), st, p, [{"id": "cls"}])
    assert stats["new"] == 0 and stats["chosen"] == 0


def test_push_sources_default_to_the_flash_sources():
    cfg = PushConfig()
    sources = [{"id": "cls", "type": "cls"}, {"id": "fed", "type": "rss"}, {"id": "off", "type": "x", "enabled": False}]
    assert [s["id"] for s in push.push_sources(cfg, sources)] == ["cls"]
    cfg.sources = ["fed"]
    assert [s["id"] for s in push.push_sources(cfg, sources)] == ["fed"]
