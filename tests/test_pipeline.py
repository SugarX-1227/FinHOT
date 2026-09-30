from datetime import datetime, timedelta

from finhot.dedup import dedup
from finhot.fetchers.base import FetchResult
from finhot.models import NewsItem
from finhot.report import ReportContext, render
from finhot.scoring import classify, score_all
from finhot.textutil import split_title

T0 = datetime(2026, 9, 30, 8, 0).astimezone()


def item(src, title, content="", minutes=0, **kw):
    return NewsItem(source=src, source_name=src.upper(), title=title, content=content or title,
                    url=f"https://x/{src}/{title}", published=T0 + timedelta(minutes=minutes), source_id=title, **kw)


def test_split_title():
    assert split_title("【央行：降准0.5个百分点】财联社9月30日电，……")[0] == "央行：降准0.5个百分点"
    t, _ = split_title("财联社9月30日电，美国9月非农就业人口增加25万人。失业率4.1%。")
    assert t == "美国9月非农就业人口增加25万人。"


def test_dedup_merges_same_event_across_sources():
    items = [
        item("cls", "央行：下调存款准备金率0.5个百分点", "【央行：下调存款准备金率0.5个百分点】财联社9月30日电，中国人民银行决定于10月8日下调金融机构存款准备金率0.5个百分点。", important=True, weight=1.5),
        item("wscn", "中国人民银行决定下调金融机构存款准备金率0.5个百分点", "中国人民银行决定于10月8日下调金融机构存款准备金率0.5个百分点。", minutes=1),
        item("sina", "中国人民银行：决定于10月8日下调金融机构存款准备金率0.5个百分点", minutes=2),
        item("ths", "宁德时代发布新一代固态电池", "宁德时代今日发布新一代固态电池，能量密度提升50%。", minutes=3),
    ]
    out = dedup(items)
    assert len(out) == 2
    rrr = next(x for x in out if "准备金" in x.title)
    assert rrr.source == "cls" and rrr.dup_count == 3 and rrr.important
    assert set(rrr.dup_sources) == {"WSCN", "SINA"}


def test_dedup_keeps_distant_events_apart():
    a = item("cls", "港股收评：恒生指数涨0.37%，科技股走强", minutes=0)
    b = item("cls2", "港股收评：恒生指数涨0.37%，科技股走强", minutes=60 * 20)
    assert len(dedup([a, b])) == 2


def test_classify_and_score():
    assert classify(item("a", "国务院办公厅印发《关于发展体育赛事激发消费活力的意见》")) == "政策"
    assert classify(item("a", "中国人民银行：将开展1.2万亿元买断式逆回购操作")) == "宏观"
    assert classify(item("a", "美联储理事沃勒：支持12月再次降息")) == "海外"
    assert classify(item("a", "东吴证券：发行股份购买资产事项中止审核")) == "公司"
    big = item("a", "央行宣布降准0.5个百分点", important=True)
    big.dup_sources = ["B", "C", "D"]
    noise = item("b", "某某股份盘中快速拉升涨超5%")
    score_all([big, noise])
    assert big.score >= 8 and noise.score < 3


def test_render_contains_sections():
    items = score_all(dedup([
        item("cls", "央行宣布降准0.5个百分点", important=True),
        item("ths", "宁德时代发布新一代固态电池，能量密度提升50%", "宁德时代今日发布新一代固态电池，能量密度提升50%，预计2027年量产。"),
    ]))
    ctx = ReportContext(date="2026-09-30", edition="盘前版", since=T0 - timedelta(hours=24), until=T0,
                        items=items, results=[FetchResult("cls", "财联社", items[:1]),
                                              FetchResult("x", "坏源", ok=False, error="HTTPError: 403")],
                        raw_count=2, min_score=0)
    md = render(ctx)
    assert "# 财经热点日报 · 2026-09-30（盘前版）" in md
    assert "今日必读" in md and "央行宣布降准" in md
    assert "坏源 | ❌" in md
    assert "不构成任何投资建议" in md
