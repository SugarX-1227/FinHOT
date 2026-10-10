"""金标样本：抽样分布、引擎 gold.jsonl 格式、标注页。"""

import json
from collections import Counter

from finhot import gold

from test_pipeline import item


def pool(n=400):
    cats = ["政策", "宏观", "海外", "行业", "公司", "市场"]
    out = []
    for i in range(n):
        it = item("cls", f"第{i}条：某事件{i}发生，涉及金额{i * 7}亿元", minutes=i)
        it.score = (i % 100) / 10
        it.category = cats[i % len(cats)]
        out.append(it)
    return out


def test_sample_leans_on_the_threshold_and_spreads_categories():
    picked = gold.sample(pool(), n=100)
    assert len(picked) == 100 and len({it.uid for it in picked}) == 100
    near = sum(1 for it in picked if 5 <= it.score < 7)
    assert near >= 50  # 门槛附近的难例最多
    cats = Counter(it.category for it in picked)
    assert len(cats) == 6 and max(cats.values()) - min(cats.values()) <= 6


def test_cases_follow_the_engine_gold_format():
    it = item("cls", "央行：下调存款准备金率0.5个百分点", "中国人民银行决定下调存款准备金率0.5个百分点。")
    it.category = "宏观"
    case = gold.to_case(it, "development", {"cls": {"kind": "external", "tier": "T2"}})
    assert case["caseId"] == f"finhot-{it.uid}"
    assert case["material"]["bodyOriginal"].startswith("中国人民银行") and case["material"]["bodyZh"] is None
    assert case["material"]["publishedAt"].endswith("+08:00")
    assert case["sourceFacts"] == {"sourceKind": "external", "sourceTier": "T2", "firstParty": False, "language": "zh"}
    assert case["samplingContext"] == {"benchmarkSplit": "development", "samplingStratum": "宏观"}
    cases = gold.build_cases(gold.sample(pool(), n=40))
    assert Counter(c["samplingContext"]["benchmarkSplit"] for c in cases) == {"development": 30, "holdout": 10}


def test_labels_become_gold_lines_and_unknown_values_are_skipped():
    cases = [{"caseId": "a"}, {"caseId": "b"}, {"caseId": "c"}]
    rows = gold.with_labels(cases, {"a": "select", "b": "maybe"})
    assert rows == [{"caseId": "a", "gold": {"decision": "select"}}]


def test_label_page_embeds_the_cases_safely(tmp_path):
    cases = [{"caseId": "x", "material": {"title": "</script><b>标题</b>"}}]
    page = gold.render_page(cases, standalone=False)
    assert page.startswith("<title>") and "</script><b>" not in page
    assert json.dumps("x") in page
    full = gold.render_page(cases)
    assert full.startswith("<!doctype html>") and full.rstrip().endswith("</html>")
