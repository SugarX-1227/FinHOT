import json
from datetime import datetime

from finhot import llm
from finhot.models import NewsItem
from finhot.timeutil import TZ


class FakeClient(llm.LLMClient):
    def __init__(self, reply):
        super().__init__(api_key="x")
        self.reply = reply

    def chat(self, system, user, json_mode=False, max_tokens=4000):
        self.calls += 1
        self.last_user = user
        return self.reply(json.loads(user)) if json_mode else "- 要点一\n- 要点二\n说明文字"


def mk(title, content):
    return NewsItem(source="cls", source_name="财联社", title=title, content=content, url="",
                    published=datetime(2026, 9, 30, 8, tzinfo=TZ), source_id=title, score=5)


def test_enrich_applies_results_and_filters_hallucinated_stocks():
    items = [mk("宁德时代发布固态电池", "宁德时代今日发布新一代固态电池。"), mk("某股盘中拉升", "某股盘中拉升5%。")]

    def reply(batch):
        return "```json\n" + json.dumps({"items": [
            {"id": "0", "keep": True, "category": "行业", "score": 9, "title": "宁德时代发布固态电池",
             "summary": "摘要", "direction": "利好", "sectors": ["固态电池"], "stocks": ["宁德时代", "比亚迪"],
             "horizon": "中期逻辑", "basis": "原文称发布新一代固态电池"},
            {"id": "1", "keep": False, "score": 1},
        ]}, ensure_ascii=False) + "\n```"

    stats = llm.enrich(items, FakeClient(reply), max_items=10)
    assert stats == {"analyzed": 2, "failed": 0}
    top = items[0]
    assert top.title == "宁德时代发布固态电池" and top.llm["direction"] == "利好"
    assert top.llm["stocks"] == ["宁德时代"]          # 原文没有"比亚迪"，被过滤
    assert top.score > items[1].score


def test_enrich_survives_bad_output():
    items = [mk("a", "a")]
    stats = llm.enrich(items, FakeClient(lambda b: "not json"), max_items=10)
    assert stats["failed"] == 1 and items[0].llm == {}


def test_overview_keeps_only_bullets():
    text = llm.overview([mk("a", "a")], [], FakeClient(lambda b: ""))
    assert text == "- 要点一\n- 要点二"


def test_from_env_requires_key(monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    assert llm.LLMClient.from_env() is None


def test_from_env_empty_vars_fall_back_to_defaults(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_BASE_URL", "")
    monkeypatch.setenv("LLM_MODEL", "")
    c = llm.LLMClient.from_env()
    assert c.base_url == "https://api.deepseek.com/v1" and c.model == "deepseek-chat"
