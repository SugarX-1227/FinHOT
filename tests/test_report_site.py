import json
from datetime import date, datetime, timedelta

from finhot import calendar_watch
from finhot.crosscheck import cross_check
from finhot.fetchers.base import FetchResult
from finhot.models import NewsItem
from finhot.report import ReportContext, render
from finhot.site import build_site
from finhot.timeutil import TZ
from finhot.weekly import load_week, render_weekly

from fixtures import IPO, LIFTS, WSCN_CAL
from test_market_features import snapshot

T = datetime(2026, 10, 9, 16, 30, tzinfo=TZ)


def news(i, title, score, cat, **kw):
    it = NewsItem(source="cls", source_name="财联社电报", title=title, content=f"{title}。第{i}条新闻的正文。",
                  url=f"https://www.cls.cn/detail/{i}", published=T - timedelta(hours=i), source_id=str(i),
                  score=score, category=cat, **kw)
    return it


def sample_items():
    a = news(1, "宁德时代发布新一代固态电池", 9.2, "公司", dup_sources=["华尔街见闻", "金十数据"], imp_votes=2.5)
    a.llm = {"title": "宁德时代发布固态电池", "summary": "宁德时代发布新一代固态电池，能量密度提升。", "direction": "利好",
             "sectors": ["固态电池", "锂电"], "stocks": ["宁德时代"], "horizon": "中期逻辑", "basis": "原文称发布新一代固态电池"}
    return [a, news(2, "多家电池企业宣布扩产储能电池", 7.5, "行业"), news(3, "证监会发布公募基金运作办法征求意见稿", 8.8, "政策"),
            news(4, "美联储理事：可能需要继续加息", 8.0, "海外"), news(5, "银行业净息差继续收窄", 6.0, "宏观")]


def daily_md(edition="盘后版", key="close"):
    items = sample_items()
    snap = snapshot()
    w = calendar_watch.Watchlist(day="2026-10-10")
    w.data, w.events = calendar_watch.parse_wscn_calendar(WSCN_CAL)
    w.ipo_apply = calendar_watch.parse_ipo(IPO)
    w.lifts = calendar_watch.parse_lifts(LIFTS)
    ctx = ReportContext(date="2026-10-09", edition=edition, edition_key=key, since=T - timedelta(hours=8), until=T,
                        items=items, results=[FetchResult("cls", "财联社电报", items)], raw_count=10,
                        quotes=[{"name": "道琼斯", "price": 51384.32, "change": 152.68, "pct": 0.3, "time": "10-09 22:36"}],
                        overview="- 要点一\n- 要点二", snapshot=snap, watch=w, themes=cross_check(items, snap))
    return render(ctx), items


def test_daily_render_has_all_sections():
    md, _ = daily_md()
    for s in ("## 要点速览", "今日必读", "明日关注（10-10）", "外盘与大宗", "A股盘面（2026-10-09）", "新闻 × 资金",
              "连板梯队", "龙虎榜净买入（2026-10-09）", "限售解禁", "申购：皇冠新材", "🔥共振"):
        assert s in md, s
    assert "1. **宁德时代发布固态电池** 🔴\\\n" in md          # 硬换行
    assert "\n\n   > 🔺利好 · 板块：固态电池、锂电" in md      # 利好映射是独立引用块


def test_weekly_and_site(tmp_path):
    md, items = daily_md()
    reports, data = tmp_path / "reports", tmp_path / "data"
    (reports / "weekly").mkdir(parents=True)
    (reports / "2026-10-09-close.md").write_text(md, encoding="utf-8")
    (reports / "2026-10-09.md").write_text(md.replace("盘后版", "盘前版"), encoding="utf-8")
    ddir = data / "2026-10-09"
    ddir.mkdir(parents=True)
    with open(ddir / "items-close.jsonl", "w", encoding="utf-8") as f:
        for it in items:
            f.write(json.dumps(it.to_dict(), ensure_ascii=False) + "\n")
    (ddir / "meta-close.json").write_text(json.dumps({"until": T.isoformat(), "themes": [
        {"theme": "锂电储能", "verdict": "共振"}, {"theme": "银行", "verdict": "新闻热·资金冷"}]}), encoding="utf-8")

    wd = load_week(date(2026, 10, 9), data)
    assert wd.label == "2026-W41" and len(wd.items) == 5
    wmd = render_weekly(wd, [{"name": "上证指数", "close": 3813.79, "pct": -0.74}], None, ([], []))
    for s in ("本周十大新闻", "题材热度榜", "锂电储能", "新闻 × 资金 周度回顾", "上证指数"):
        assert s in wmd, s
    (reports / "weekly" / "2026-W41.md").write_text(wmd, encoding="utf-8")

    out = build_site(tmp_path / "_site", reports, base_url="https://example.github.io/FinHOT")
    index = (out / "index.html").read_text(encoding="utf-8")
    assert "财经热点日报 · 2026-10-09（盘后版）" in index and '<span class="up">▲</span>' in index
    assert (out / "daily" / "2026-10-09.html").exists() and (out / "weekly" / "2026-W41.html").exists()
    page = (out / "daily" / "2026-10-09-close.html").read_text(encoding="utf-8")
    assert '<div class="table-wrap"><table>' in page and "← 10-09 周五 · 盘前" in page
    feed = (out / "feed.xml").read_text(encoding="utf-8")
    assert "https://example.github.io/FinHOT/daily/2026-10-09-close.html" in feed and "要点一" in feed
    assert "10-09 周五" in (out / "archive.html").read_text(encoding="utf-8")
