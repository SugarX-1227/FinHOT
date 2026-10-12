"""涨停原因归因：每类依据给出的原因与可信度、句子里找板块、模型改写的校验、同花顺接口解析。"""

import json

from finhot.ashare import Board, MarketSnapshot, parse_ths_limit_up
from finhot.limitup import _clause_context, attribute, coverage, write_reasons
from finhot.report import _render_limitups

from test_pipeline import item


def zt(code, name, industry, boards=1, first="0930"):
    return {"code": code, "name": name, "industry": industry, "boards": boards, "first_time": first, "stat": ""}


def snapshot():
    return MarketSnapshot(
        trade_date="2026-10-09",
        zt_stocks=[
            zt("300821", "东岳硅材", "化学制品"),
            zt("002805", "丰元股份", "电池", boards=2, first="0936"),
            zt("300530", "领湃科技", "电池", boards=2, first="0954"),
            zt("603067", "振华股份", "化学原料"),
            zt("600001", "甲电池", "电池"), zt("600002", "乙电池", "电池"), zt("600003", "丙电池", "电池"),
            zt("600004", "丁传媒", "出版"),
        ],
        concepts=[Board(code="BK1", name="电解液", pct=2.15, inflow=None, leader="振华股份", kind="concept")],
    )


NEWS = [
    item("cls", "东岳硅材：预计前三季度净利润同比增长19050%-19750%"),
    item("ths", "丰元股份澄清：不涉及电池生产业务"),
    item("cls", "收评：创业板指冲高回落",
         "盘面上，固态电池概念逆势活跃，力王股份30CM涨停，传艺科技3连板，丰元股份、领湃科技涨停。"),
]


def test_each_kind_of_evidence_gives_its_reason_and_confidence():
    rows = {r.name: r for r in attribute(snapshot(), NEWS, ths_reasons={})}
    assert rows["东岳硅材"].confidence == "高" and rows["东岳硅材"].reason.startswith("预计前三季度净利润")
    # 澄清是公司在给炒作降温：原因取盘面上的板块，澄清附在后面
    assert rows["丰元股份"].reason == "固态电池走强（公司提示：不涉及电池生产业务）"
    assert rows["丰元股份"].confidence == "中"
    assert rows["领湃科技"].reason == "固态电池走强"
    assert rows["振华股份"].reason == "电解液概念领涨"
    # 只有行业：按题材推断，同题材涨停家数一起写上
    assert rows["甲电池"].reason == "锂电储能题材（同题材涨停 5 家）" and rows["甲电池"].confidence == "低"
    assert rows["丁传媒"].reason == "传媒游戏题材"
    assert coverage(list(rows.values())) == 50.0


def test_ths_reason_leads_and_a_company_catalyst_makes_it_high():
    rows = {r.name: r for r in attribute(snapshot(), NEWS, ths_reasons={"300821": "有机硅+业绩预增", "600004": "国庆档票房"})}
    assert (rows["东岳硅材"].reason, rows["东岳硅材"].confidence) == ("有机硅+业绩预增", "高")
    assert (rows["丁传媒"].reason, rows["丁传媒"].confidence) == ("国庆档票房", "中")


def test_the_board_comes_from_the_nearest_clause_before_the_stock():
    s = "市场焦点股新华传媒竞价涨停，电池产业链的时代万恒走强，锂电池概念盘中走强，紫竹高科4连板，领湃科技2连板，金圆股份涨停"
    assert _clause_context("金圆股份", s, {"紫竹高科", "领湃科技"}).startswith("锂电池概念盘中走强")
    # 股票名里的“黄金”不算板块
    assert _clause_context("西部黄金", "贵金属板块拉升，西部黄金涨停").startswith("贵金属板块拉升")


class FakeClient:
    def __init__(self, answer):
        self.answer = answer

    def chat(self, system, user, json_mode=False, max_tokens=0):
        return json.dumps({"原因": self.answer}, ensure_ascii=False)


def test_model_reasons_are_kept_only_when_they_stay_within_the_evidence():
    rows = attribute(snapshot(), NEWS, ths_reasons={})
    n = write_reasons(rows, FakeClient({
        "300821": "有机硅涨价，三季报预增近两百倍",
        "002805": "固态电池题材，领湃科技带动",      # 领湃科技在依据里出现过，可以
        "300530": "与东岳硅材合作固态电池项目",       # 依据里没有东岳硅材 → 不用
        "603067": "建议关注电解液龙头",               # 荐股口吻 → 不用
    }))
    by = {r.code: r for r in rows}
    assert n == 2
    assert by["300821"].reason == "有机硅涨价，三季报预增近两百倍" and by["300821"].written_by == "模型"
    assert by["300530"].reason == "固态电池走强" and by["603067"].reason == "电解液概念领涨"


def test_ths_pool_parsing_and_report_table():
    data = {"status_code": 0, "data": {"page": {"total": 2}, "info": [
        {"code": "002058", "name": "紫竹高科", "reason_type": "固态电池+铝塑膜", "high_days": "4天4板"},
        {"code": "600001", "name": "无原因", "reason_type": ""}]}}
    assert parse_ths_limit_up(data) == ({"002058": "固态电池+铝塑膜"}, 2)
    md = "\n".join(_render_limitups(attribute(snapshot(), NEWS, ths_reasons={})))
    assert "| 丰元股份（002805） | 2 | 09:36 | 固态电池走强（公司提示：不涉及电池生产业务） | 中 |" in md
    assert "其余：锂电储能 3 家" in md
