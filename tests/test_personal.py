"""个性化：实体库、实体链接、画像、公告、涨停归因用公告、个人排序、简报、推送、反馈、评估（全部离线）。"""

from datetime import date, datetime

import pytest

from finhot import alerts, announce, history
from finhot.backtest import Bar
from finhot.brief import BriefInput, build, merge_site, neutral_reason
from finhot.entities import Stock, build as build_book, exchange_of, merge_sync, parse_cninfo_stocks, secid
from finhot.evaluate import compare, event_returns, Event, feedback_report, summarize_events
from finhot.fetchers.base import FetchContext
from finhot.fetchers.notices import parse_cninfo, parse_em_notice
from finhot.feedback_server import handle
from finhot.http import Session
from finhot.linker import Linker
from finhot.models import NewsItem
from finhot.notify import feedback_sign, feishu_body, wecom_body
from finhot.personal import Ranker, push_worthy, select, sigmoid_learned
from finhot.profile import (PrivacyError, Profile, Relation, _safe_dir, extract, from_dict, in_quiet, merge_relations,
                            refuse_ci, validate)
from finhot.relations import suggest
from finhot.timeutil import TZ


def at(d, h, m=0):
    return datetime(2026, 10, d, h, m, tzinfo=TZ)


STOCKS = {"complete": True, "updated": "2026-10-16", "stocks": [
    {"code": "300750", "name": "宁德时代", "industry": "电池"},
    {"code": "002594", "name": "比亚迪", "industry": "乘用车"},
    {"code": "601127", "name": "赛力斯", "industry": "乘用车"},
    {"code": "600030", "name": "中信证券", "industry": "证券"},
    {"code": "601998", "name": "中信银行", "industry": "银行"},
    {"code": "601066", "name": "中信建投", "industry": "证券"},
    {"code": "300433", "name": "蓝思科技", "industry": "消费电子"},
    {"code": "300024", "name": "机器人", "industry": "自动化设备"},
    {"code": "600693", "name": "东百集团", "industry": "百货"},
    {"code": "002460", "name": "赣锋锂业", "industry": "能源金属"},
    {"code": "603588", "name": "高能时代", "industry": "环保", "former": ["高能环境"]},
    {"code": "688981", "name": "中芯国际", "industry": "半导体"},
]}
BOOK = build_book(STOCKS, {"stocks": {"600519": {"name": "贵州茅台", "aliases": ["茅台"]}}, "generic": ["机器人"]})
LINKER = Linker(BOOK)


def news(title, content="", published=None, score=6.0, category="公司", stocks=(), source="cls", **kw):
    it = NewsItem(source=source, source_name="财联社电报", title=title, content=content or title, url=f"https://x/{hash(title)}",
                  published=published or at(15, 20), stocks=list(stocks), **kw)
    it.score, it.category = score, category
    return it


def ann(code, name, title, published=None):
    return NewsItem(source="cninfo", source_name="巨潮资讯公告", title=f"{name}：{title}", content=title, url=f"https://a/{code}",
                    published=published or at(15, 19), codes=[code], tags=[announce.classify_type(title).name], tier="official")


def roles(title, content=""):
    return {m.name: m.role for m in LINKER.link(news(title, content))}


# ------------------------------------------------------------------ 实体库
def test_cninfo_stock_list_and_sync_records_renames_st_and_delisting():
    fresh = parse_cninfo_stocks({"stockList": [
        {"orgId": "gssz0300750", "category": "A股", "code": "300750", "pinyin": "ndsd", "zwjc": "宁德时代"},
        {"orgId": "x", "category": "B股", "code": "200001", "zwjc": "B股不要"},
        {"orgId": "gssh0603588", "category": "A股", "code": "603588", "zwjc": "高能时代"},
        {"orgId": "gssz0000001", "category": "A股", "code": "000001", "zwjc": "*ST 平安"}]})
    assert [r["code"] for r in fresh] == ["300750", "603588", "000001"] and fresh[0]["org_id"] == "gssz0300750"
    old = [Stock("300750", "宁德时代"), Stock("603588", "高能环境"), Stock("000001", "平安银行"), Stock("600000", "退市股")]
    stocks, changes = merge_sync(old, fresh, {"300750": "电池"})
    by = {s.code: s for s in stocks}
    assert by["603588"].former == ["高能环境"] and by["603588"].name == "高能时代"
    assert by["000001"].st and by["000001"].former == ["平安银行"]       # 改名了（不只是戴帽）
    assert by["600000"].status == "delisted" and by["300750"].industry == "电池"
    assert set(changes) == {"改名", "退市"}
    # 一条都没取到：不能把全部标成退市
    stocks, changes = merge_sync(old, [])
    assert all(s.status == "listed" for s in stocks) and not changes


def test_st_cap_is_not_a_rename():
    stocks, _ = merge_sync([Stock("000002", "万科A")], [{"code": "000002", "name": "*ST万科A"}])
    assert stocks[0].st and stocks[0].former == []


def test_exchange_and_secid():
    assert (exchange_of("600519"), exchange_of("300750"), exchange_of("830799"), exchange_of("920001")) == ("SH", "SZ", "BJ", "BJ")
    assert secid("600519") == "1.600519" and secid("000001") == "0.000001"


def test_book_resolves_aliases_former_names_and_bare_st_names():
    assert BOOK.resolve("茅台")[0].code == "600519"
    assert BOOK.resolve("高能环境")[0].code == "603588"
    st = build_book({"stocks": [{"code": "000001", "name": "*ST平安"}]})
    assert st.resolve("平安")[0].code == "000001"


# ------------------------------------------------------------------ 实体链接
def test_broker_opinion_is_a_source_not_the_subject():
    assert roles("中信证券：金价底部或抬升 黄金板块有望先行") == {"中信证券": "观点来源"}
    assert roles("中信证券：前三季度净利润同比增长20%") == {"中信证券": "主体"}
    assert roles("长江证券：维持蓝思科技“买入”评级") == {"蓝思科技": "标题点名"}


def test_intermediaries_lists_and_longest_match():
    assert roles("企查查 撤回IPO", "上交所披露，企查查及保荐人中信证券申请撤回发行上市申请文件") == {"中信证券": "中介"}
    assert roles("收评：沪指跌1.59%", "银行股逆势走强，中国银行、中信银行、工商银行续创历史新高") == {"中信银行": "行情名单"}
    assert roles("中信建投：AI算力与高速互连景气持续") == {"中信建投": "观点来源"}   # 不会被当成“中信……”


def test_subject_title_codes_and_generic_names():
    assert roles("东百集团：终止收购杭州快灵光电部分股权") == {"东百集团": "主体"}
    assert roles("赛力斯与宁德时代签署长期战略合作协议") == {"赛力斯": "主体", "宁德时代": "主体"}
    assert roles("动力电池装车量公布", "排名第一的是（300750）") == {"宁德时代": "正文提及"}
    assert roles("风电板块走强", "多股走强（300750）领涨") == {"宁德时代": "行情名单"}
    assert roles("机器人产业规模突破千亿") == {}
    assert roles("机器人：拟回购股份") == {"机器人": "主体"}
    assert roles("新能源车销量", "比亚迪销量继续增长", ) == {"比亚迪": "正文提及"}
    it = news("新能源车销量", "比亚迪销量继续增长", stocks=["比亚迪"])
    assert LINKER.link(it)[0].role == "信源关联"


def test_company_category_needs_a_company_prefix():
    from finhot.scoring import classify
    it = news("黎巴嫩总统重申：坚持通过谈判结束黎以敌对状态", category="")
    assert classify(it) != "公司"
    assert classify(news("中信证券：看好射频氮化镓行业发展", category="")) != "公司"
    assert classify(news("人社部：将构建技能导向的薪酬分配制度", category="")) == "政策"


# ------------------------------------------------------------------ 画像
def test_profile_validation_and_import():
    p = from_dict({"name": "me", "holdings": [300750], "watch": ["002594"], "themes": ["锂电储能", "不存在"],
                   "push": {"channel": "feishu", "webhook": "env:NO_SUCH_VAR"}})
    assert p.holdings == ["300750"]
    problems = validate(p, BOOK)
    assert any("不存在" in w for w in problems) and any("webhook" in w for w in problems)
    text = "代码\t名称\nSZ300750\t宁德时代\n600030.SH,中信证券\n赛力斯\n没有这家\n"
    codes, unresolved = extract(text, BOOK)
    assert codes == ["300750", "600030", "601127"] and unresolved == ["没有这家"]


def test_privacy_guards(tmp_path, monkeypatch):
    from finhot.config import DATA_DIR, REPORTS_DIR
    with pytest.raises(PrivacyError):
        _safe_dir(REPORTS_DIR / "me", "x")
    with pytest.raises(PrivacyError):
        _safe_dir(DATA_DIR / "2026-10-16", "x")
    assert _safe_dir(DATA_DIR / "private", "x")          # 已在 .gitignore 里
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    with pytest.raises(PrivacyError):
        refuse_ci()


def test_gitignore_keeps_profiles_and_private_data_out():
    from finhot.config import ROOT
    text = (ROOT / ".gitignore").read_text(encoding="utf-8")
    for line in ("profiles/*", "!profiles/example.yaml", ".private/", "data/private/"):
        assert line in text


def test_quiet_hours_cross_midnight():
    assert in_quiet(at(16, 23), "22:30-07:30") and in_quiet(at(16, 6), "22:30-07:30")
    assert not in_quiet(at(16, 12), "22:30-07:30") and in_quiet(at(16, 13), "12:00-14:00")


# ------------------------------------------------------------------ 公告
def test_announcement_types():
    t = announce.classify_type
    assert t("2026年前三季度业绩预告").name == "业绩预告" and t("2026年前三季度业绩预告").push
    assert t("关于持股5%以上股东减持股份计划的公告").name == "减持"
    assert t("关于控股股东协议转让股份暨控制权拟发生变更的提示性公告").name == "控制权变更"
    assert t("关于收到上海证券交易所问询函的公告").name == "监管问询"
    assert t("关于公司股票交易异常波动的公告").catalyst == "提示"
    assert t("2026年第三季度报告摘要").name == "其他"                    # 摘要不重复算
    assert not t("第九届董事会第三次会议决议公告").whitelist


def test_cninfo_and_eastmoney_notice_parsers():
    ctx = FetchContext({"id": "cninfo", "name": "巨潮资讯公告", "type": "cninfo", "tier": "official"}, Session(), at(15, 0))
    items = parse_cninfo(ctx, {"announcements": [
        {"secCode": "300750", "secName": "宁德时代", "orgId": "gssz0300750", "announcementId": "1224",
         "announcementTitle": "<em>宁德时代</em>：2026年前三季度业绩预告", "announcementTime": 1792065600000},
        {"secCode": "", "secName": "债券", "announcementTitle": "x", "announcementTime": 1792065600000}]})
    assert len(items) == 1 and items[0].codes == ["300750"] and items[0].title == "宁德时代：2026年前三季度业绩预告"
    assert items[0].tags == ["业绩预告"] and "announcementId=1224" in items[0].url and announce.is_announcement(items[0])
    ctx = FetchContext({"id": "em_notice", "name": "东方财富公告", "type": "eastmoney_notice", "tier": "official"}, Session(), at(15, 0))
    items = parse_em_notice(ctx, {"data": {"list": [
        {"art_code": "AN1", "codes": [{"stock_code": "002594", "short_name": "比亚迪"}], "title": "比亚迪:关于回购股份的公告",
         "display_time": "2026-10-15 18:01:02:123"}]}})
    assert items[0].codes == ["002594"] and items[0].content == "关于回购股份的公告" and items[0].tags == ["回购"]


def test_two_announcement_sources_are_merged():
    from finhot.fetchers.base import FetchResult
    a, b = ann("300750", "宁德时代", "2026年前三季度业绩预告"), ann("300750", "宁德时代", "2026年前三季度业绩预告")
    b.source = "em_notice"
    merged = announce.merge_sources([FetchResult("cninfo", "巨潮", [a]), FetchResult("em_notice", "东财", [b])])
    assert len(merged) == 1 and merged[0].source == "cninfo"


# ------------------------------------------------------------------ 涨停归因用公告
def test_limitup_uses_announcements_and_does_not_take_losses_as_catalysts():
    from finhot.ashare import MarketSnapshot
    from finhot.limitup import announced, attribute
    snap = MarketSnapshot(trade_date="2026-10-15", zt_stocks=[
        {"code": "600693", "name": "东百集团", "industry": "百货"},
        {"code": "603956", "name": "威派格", "industry": "专用设备"},
        {"code": "002460", "name": "赣锋锂业", "industry": "能源金属"}])
    items = [news("威派格：上半年营收1.41亿元 净亏损1.30亿元")]
    anns = [ann("600693", "东百集团", "关于拟收购杭州快灵光电52.67%股权的公告"),
            ann("002460", "赣锋锂业", "关于公司股票交易异常波动的公告")]
    rows = {r.name: r for r in attribute(snap, items, announcements=anns, ths_reasons={})}
    assert rows["东百集团"].confidence == "高" and rows["东百集团"].evidence[0].kind == "公告"
    assert rows["威派格"].confidence != "高"
    assert rows["赣锋锂业"].confidence != "高" and announced(list(rows.values())) == 2


# ------------------------------------------------------------------ 个人排序与简报
ME = Profile("me", holdings=["300750"], watch=["002594"], themes=["锂电储能", "半导体"],
             relations=[Relation("300750", "002460", "上游"), Relation("300750", "赛力斯", "客户", status="待审核",
                                                                   source="https://x", quote="赛力斯向宁德时代采购")])


def ranker():
    return Ranker(ME, book=BOOK)


def test_reasons_cite_only_facts():
    r = ranker()
    p = r.rank_one(ann("300750", "宁德时代", "2026年前三季度业绩预告"), at(16, 8))
    assert (p.section, p.reason) == ("持仓", "持仓 宁德时代 · 公告（业绩预告）") and push_worthy(p)
    p = r.rank_one(news("宁德时代：拟出资4亿元参与投资创投基金"), at(16, 8))
    assert p.reason == "持仓 宁德时代 · 新闻主体"
    p = r.rank_one(news("赣锋锂业：前三季度净利润同比增长50%"), at(16, 8))
    assert p.section == "关联公司" and p.reason.startswith("持仓 宁德时代 的上游 · 赣锋锂业新闻主体")
    p = r.rank_one(news("赛力斯：前三季度净利润同比增长"), at(16, 8))
    assert "客户" not in p.reason and p.reason_kind == "同行业"            # 待审核的关系不用；同行业（乘用车）照算
    p = r.rank_one(news("碳酸锂跌破11万元/吨", category="行业"), at(16, 8))
    assert p.reason == "关注题材 锂电储能"
    p = r.rank_one(news("美联储官员称今年或再加息", category="海外"), at(16, 8))
    assert p.section == "宏观"
    assert r.rank_one(news("某公司拿到订单", category="公司"), at(16, 8)) is None


def test_weak_mentions_are_folded_and_not_pushed():
    r = ranker()
    p = r.rank_one(news("18股特大单净流入超2亿元", "宁德时代、比亚迪等获特大单净流入", score=8), at(16, 8))
    assert p.section == "持仓" and not push_worthy(p)
    assert select([p])["持仓"] == []


def test_brief_build_sections_repeats_and_wording():
    inp = BriefInput(
        news=[news("宁德时代：拟出资4亿元参与投资创投基金", published=at(15, 18)),
              news("碳酸锂跌破11万元/吨 储能或成需求增长引擎", category="行业", score=7),
              news("美联储官员称今年或再加息", category="海外", score=8),
              news("美联储三把手：无需在2027年加息", category="海外", score=7),
              news("比亚迪匈牙利工厂第四季度量产", score=6)],
        announcements=[ann("300750", "宁德时代", "2026年前三季度业绩预告")])
    seen = ({inp.news[4].uid}, [])
    br = build(ME, inp, at(16, 8, 20), at(15, 15), b=BOOK, seen=seen)
    md = br.markdown
    assert "# 我的盘前 5 分钟" in md and "为什么给你：持仓 宁德时代 · 公告（业绩预告）" in md
    assert "关注题材 锂电储能" in md and "**美联储**（2 条）" in md
    assert "比亚迪匈牙利" not in md and br.stats["repeats"] == 1            # 之前给过的不再给
    assert "利好" not in md and "利空" not in md and "不构成任何投资建议" in md


def test_site_items_are_merged_with_neutral_wording():
    assert neutral_reason("利好 半导体、存储，依据：原文称……") == "可能影响：半导体、存储"
    items = [news("英伟达拟本周宣布欧洲20亿美元AI投资")]
    n = merge_site(items, [{"url": items[0].url, "score": 72, "reason": "利好 算力，依据……", "publishedAt": "2026-10-15T15:00:00Z",
                            "title": "x"},
                           {"url": "https://other", "score": 65, "title": "引擎自己的 RSS 条目", "publishedAt": "2026-10-15T14:00:00Z"}],
                   at(15, 0))
    assert n == 2 and items[0].llm["engine_reason"] == "可能影响：算力" and len(items) == 2


# ------------------------------------------------------------------ 推送与反馈
def test_feishu_and_wecom_bodies():
    body = feishu_body("标题", "内容", secret="abc", ts=1700000000)
    assert body["timestamp"] == "1700000000" and body["sign"] and body["card"]["header"]["title"]["content"] == "标题"
    assert len(wecom_body("t", "长" * 5000)["markdown"]["content"].encode()) <= 4000


def test_feedback_links_are_signed():
    secret = "s" * 20
    good = f"p=me&i=abc&v=useful&s={feedback_sign('me', 'abc', 'useful', secret)}"
    assert handle(good, secret)[0] == 200 and history.feedback("me") == {"abc": "useful"}
    assert handle(good.replace("useful&", "irrelevant&"), secret)[0] == 403
    assert handle("p=me&i=abc&v=bad&s=x", secret)[0] == 400


def test_deliver_caps_quiet_hours_and_dedup():
    sent = []
    p = Profile("me", holdings=["300750"])
    p.push.channel, p.push.webhook, p.push.daily_limit = "feishu", "https://hook", 2
    w = {"push": {"quiet": "22:30-07:30", "daily_limit": 8}}
    rows = [alerts.Alert(p, f"- 第{i}条", None, f"id{i}") for i in range(3)]
    stats = alerts.deliver(rows, at(16, 23), w, sender=lambda *a: sent.append(a))
    assert stats["deferred"] == 3 and not sent                          # 免打扰：延后，不发
    rows = [alerts.Alert(p, f"- 新{i}条", None, f"new{i}") for i in range(3)]
    stats = alerts.deliver(rows, at(16, 10), w, sender=lambda *a: sent.append(a))
    assert stats["sent"] == 2 and stats["capped"] == 1 and len(sent) == 1   # 上限 2，合成一条消息
    stats = alerts.deliver(rows[:1], at(16, 11), w, sender=lambda *a: sent.append(a))
    assert stats["dup"] == 1 and len(sent) == 1


def test_run_round_briefs_announcements_and_moves(monkeypatch):
    p = Profile("me", holdings=["300750"], hours=["08:20"])
    p.push.channel, p.push.webhook = "feishu", "https://hook"
    briefs, sent = [], []
    monkeypatch.setattr(alerts, "default_book", lambda: BOOK)
    state = alerts.AlertState()
    fetch = lambda since, codes: ([ann("300750", "宁德时代", "关于收到深圳证券交易所问询函的公告")], [])  # noqa: E731
    quotes = lambda codes: {"300750": {"name": "宁德时代", "price": 300.0, "pct": 6.2}}  # noqa: E731
    stats = alerts.run_round(at(16, 10, 0), [], state, [p], fetch, quotes, run_brief=lambda pr, **k: briefs.append(k["edition"]),
                             sender=lambda *a: sent.append(a))
    assert briefs == ["pre"] and stats["sent"] == 2 and "问询函" in sent[0][2] and "盘中涨 6.20%" in sent[0][2]
    stats = alerts.run_round(at(16, 10, 2), [], state, [p], fetch, quotes, run_brief=lambda pr, **k: briefs.append(1),
                             sender=lambda *a: sent.append(a))
    assert briefs == ["pre"] and stats.get("sent", 0) == 0               # 简报一天一次，异动一天一次，公告查过了


# ------------------------------------------------------------------ 关系库
def test_relations_come_from_real_sentences_and_never_overwrite():
    items = [news("宁德时代与赛力斯签署长期战略合作协议"), news("赣锋锂业向宁德时代供货碳酸锂")]
    found = suggest(ME, items, BOOK, at(16, 8))
    got = {(r.code, r.target, r.kind) for r in found}
    assert ("300750", "601127", "合作") in got and ("300750", "002460", "供应商") in got
    assert all(r.status == "待审核" and r.quote and r.source.startswith("https://") for r in found)
    merged, added = merge_relations(ME.relations, found + [Relation("300750", "002460", "上游", source="https://y")])
    assert merged[0].source == "人工维护" and added == len(found)


# ------------------------------------------------------------------ 评估
def test_blind_pool_comparison():
    rows = [{"id": "a", "in": {"b1": ["personal"]}}, {"id": "b", "in": {"b1": ["generic"]}},
            {"id": "c", "in": {"b1": ["personal", "generic"]}}]
    r = compare(rows, {"a": "useful", "b": "irrelevant", "c": "useful"})
    assert r["personal"]["precision"] == 100.0 and r["generic"]["precision"] == 50.0


def test_event_study_returns():
    days = [date(2026, 10, d) for d in (12, 13, 14, 15, 16)]
    bench = [Bar(d, 100, 100) for d in days]
    stock = [Bar(days[0], 10, 10), Bar(days[1], 11, 11), Bar(days[2], 12, 12), Bar(days[3], 12, 12), Bar(days[4], 12, 12)]
    rows = event_returns([Event("300750", "业绩预告", datetime(2026, 10, 12, 20, 0, tzinfo=TZ), "x")],
                         {"300750": stock}, bench)
    assert rows[0]["reaction_1"] == 10.0 and rows[0]["tradable_1"] == 0.0 and rows[0]["reaction_3"] == 20.0
    s = summarize_events(rows)
    assert s[0]["kind"] == "业绩预告" and s[0]["reaction_1_up"] == 100


def test_feedback_report_and_learned_factors():
    for i in range(6):
        history.record_shown([history.Shown("me", f"x{i}", "t", [], at(16, 8).isoformat(), "持仓", "持仓·主体")])
        history.add_feedback("me", f"x{i}", "useful" if i < 5 else "irrelevant", at(16, 9))
    md = feedback_report("me", 7, at(16, 12))
    assert "相关占比 83%" in md and "持仓·主体" in md
    assert history.learned_factors("me", {"feedback": {"min_labels": 5}})["持仓·主体"] > 1.2
    assert sigmoid_learned(0, 0) == 1.0 and sigmoid_learned(0, 20) < 0.6


def test_per_sector_direction():
    from finhot.llm import overall_direction, parse_impacts
    from finhot.report import _impacts_text
    imp = parse_impacts({"impacts": [{"sector": "石油石化", "direction": "利好"}, {"sector": "航空机场", "direction": "利空"}]})
    assert overall_direction(imp) == "分化"
    assert parse_impacts({"direction": "利好", "sectors": ["半导体"]}) == [{"sector": "半导体", "direction": "利好"}]
    assert _impacts_text({"direction": "分化", "impacts": imp}) == "🔺利好 · 板块：石油石化；🔻利空 · 板块：航空机场"


def test_signals_use_each_sectors_own_direction(tmp_path):
    import json

    from finhot.backtest import load_signals
    day = tmp_path / "2026-10-14"
    day.mkdir()
    it = news("中东局势升级", score=8)
    it.llm = {"score": 8, "title": "中东局势升级", "direction": "分化",
              "impacts": [{"sector": "石油石化", "direction": "利好"}, {"sector": "航空机场", "direction": "利空"}]}
    (day / "items.jsonl").write_text(json.dumps(it.to_dict(), ensure_ascii=False) + "\n", encoding="utf-8")
    kinds = {(s.kind, s.theme) for s in load_signals(tmp_path)}
    assert ("利好新闻", "油气") in kinds and ("利空新闻", "文旅") in kinds


def test_must_read_caps_one_story():
    from finhot.report import pick_top
    items = [news(t, score=9 - i * 0.1, category="海外") for i, t in enumerate(
        ["以媒称内塔尼亚胡与特朗普讨论重启对伊战争", "美军称霍尔木兹海峡由美军及伙伴掌控", "胡塞武装袭击沙特阿美总部",
         "新能源汽车增值税出口退税率将降至9%", "伊朗警告美国：再次侵略后果将延伸至地区外"])]
    top = pick_top(items, 4, per_story=2)
    assert [i.title[:4] for i in top] == ["以媒称内", "美军称霍", "新能源汽"]


def test_brief_is_due_once_per_edition():
    from finhot.brief import due
    p = Profile("me", hours=["08:20", "15:45"])
    assert due(p, at(16, 8, 25), {}) == "pre"
    assert due(p, at(16, 8, 25), {"me:pre": "2026-10-16"}) is None
    assert due(p, at(16, 15, 50), {"me:pre": "2026-10-16"}) == "close"
    assert due(p, at(18, 8, 25), {}) is None                              # 周日
    assert due(p, at(16, 12, 0), {}) is None                              # 过了 3 小时不补


def test_brief_window_follows_the_last_brief():
    from finhot.brief import window
    assert window("me", at(16, 8), None) == at(15, 8)                     # 第一份：24 小时
    history.record_shown([history.Shown("me", "brief:x", "", [], at(15, 16).isoformat(), channel="brief")])
    assert window("me", at(16, 8), None) == at(15, 16)                    # 接着上一份
    assert window("me", at(16, 8), 3) == at(16, 5)


def test_per_stock_announcement_queries_never_fall_back_to_the_whole_market():
    from finhot.fetchers.notices import fetch_cninfo, fetch_em_notice

    class NoNetwork(Session):
        def request(self, *a, **k):
            raise AssertionError("不该发请求")

    src = {"id": "cninfo", "name": "巨潮", "type": "cninfo", "max_pages": 120}
    ctx = FetchContext({**src, "options": {"codes": {"300750": ""}}}, NoNetwork(), at(15, 0))
    assert list(fetch_cninfo(ctx)) == []                                   # 没有 orgId：不查，交给东财
    ctx = FetchContext({**src, "type": "eastmoney_notice", "options": {"codes": {}}}, NoNetwork(), at(15, 0))
    assert list(fetch_em_notice(ctx)) == []
    assert announce.fetch(at(15, 0), {}) == ([], [])


def test_cninfo_dates_are_day_level():
    from finhot.fetchers.base import FetchResult, run_fetch
    from finhot.fetchers import notices

    src = {"id": "cninfo", "name": "巨潮", "type": "cninfo", "max_pages": 1}
    page = {"announcements": [{"secCode": "300750", "secName": "宁德时代", "announcementId": "1",
                               "announcementTitle": "业绩预告", "announcementTime": 1792080000000}], "hasMore": False}

    class Fake(Session):
        def request(self, *a, **k):
            class R:
                def raise_for_status(self): pass
                def json(self): return page
            return R()
    res = run_fetch(FetchContext(src, Fake(), at(16, 8, 30)))       # 窗口从 08:30 起，公告时间是当天 0 点
    assert len(res.items) == 1 and notices.parse_cninfo is not None
    em = ann("300750", "宁德时代", "业绩预告", published=at(16, 7, 5))
    em.source = "em_notice"
    merged = announce.merge_sources([res, FetchResult("em_notice", "东财", [em])])
    assert len(merged) == 1 and merged[0].source == "cninfo" and merged[0].published == at(16, 7, 5)


def test_foreign_companies_stay_company_news(monkeypatch):
    from finhot import entities
    from finhot.scoring import classify
    monkeypatch.setattr(entities, "book", lambda: BOOK)                    # 全市场清单（complete）里没有英伟达
    assert classify(news("小鹏汽车：第三季度交付量创新高", category="")) == "公司"
    assert classify(news("东百集团：终止收购", category="")) == "公司"


def test_delisted_and_old_bj_codes_do_not_shadow_listed_stocks():
    fresh = [{"code": "430017", "name": "星昊医药"}, {"code": "920017", "name": "星昊医药"},
             {"code": "000004", "name": "国华退"}, {"code": "000001", "name": "平安银行"}]
    stocks, _ = merge_sync([], fresh, {"920017": "化学制药", "000001": ""})
    by = {s.code: s.status for s in stocks}
    assert by == {"430017": "delisted", "920017": "listed", "000004": "delisted", "000001": "listed"}
    b = build_book({"stocks": [vars(s) for s in stocks]})
    assert [s.code for s in b.resolve("星昊医药")] == ["920017"]
    assert [m.code for m in Linker(b).link(news("星昊医药：获得药品注册证书"))] == ["920017"]


def test_short_and_common_names_need_company_context():
    b = build_book({"complete": True, "stocks": [
        {"code": "000838", "name": "*ST发展", "st": True}, {"code": "601099", "name": "太平洋"},
        {"code": "000528", "name": "柳工"}, {"code": "600030", "name": "中信证券", "industry": "证券"}]},
        {"generic": ["太平洋"]})
    lk = Linker(b)
    names = lambda t, c="": {m.name: m.role for m in lk.link(news(t, c))}  # noqa: E731
    assert names("经济发展势头良好") == {} and names("太平洋海啸预警中心发布预警") == {}
    assert names("柳工发布三季度业绩预告") == {"柳工": "主体"} and names("广西柳工机械厂区") == {}
    assert names("太平洋：前三季度净利润增长") == {"太平洋": "主体"}
    assert names("中信证券：2026年第四季度油轮龙头估值和利润有望创历史新高") == {"中信证券": "观点来源"}
