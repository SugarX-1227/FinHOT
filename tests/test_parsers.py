"""各信源解析器的离线测试。样例数据取自 2026-09 各接口真实返回（已裁剪）。"""

from datetime import datetime

from finhot.fetchers.base import FetchContext
from finhot.fetchers import flash
from finhot.http import Session
from finhot.market import parse_quotes
from finhot.timeutil import TZ


def ctx(sid="x", name="X"):
    return FetchContext(source={"id": sid, "name": name, "type": sid}, session=Session(),
                        since=datetime(2026, 9, 29, tzinfo=TZ))


def test_cls():
    data = {"errno": 0, "data": {"roll_data": [{
        "level": "B", "content": "【摩根资管：全球资金持续助推美国AI热潮】财联社9月30日电，摩根资管表示，尽管……",
        "brief": "", "id": 2496332, "ctime": 1790759086, "title": "摩根资管：全球资金持续助推美国AI热潮",
        "bold": 0, "recommend": 0, "subjects": [{"subject_name": "AI"}], "stock_list": [],
    }]}}
    [it] = flash.parse_cls(ctx("cls"), data)
    assert it.title == "摩根资管：全球资金持续助推美国AI热潮"
    assert it.important and it.url == "https://www.cls.cn/detail/2496332"
    assert it.published.strftime("%m-%d %H:%M") == "09-30 17:04"
    assert it.tags == ["AI"]


def test_wallstreetcn():
    data = {"code": 20000, "data": {"next_cursor": "1790748002", "items": [
        {"content": "<p>中国人民银行：将开展12000亿元买断式逆回购操作。</p>", "content_more": "",
         "content_text": "中国人民银行：将开展12000亿元买断式逆回购操作。", "display_time": 1790758955,
         "id": 3172818, "score": 2, "symbols": [], "tags": [], "title": "",
         "uri": "https://wallstreetcn.com/livenews/3172818"}]}}
    [it] = flash.parse_wallstreetcn(ctx(), data)
    assert it.important and it.title.startswith("中国人民银行")
    assert it.url.endswith("3172818")


def test_jin10_skips_empty_and_parses_title():
    data = {"status": 200, "data": [
        {"data": {"content": "【荷兰政府计划从2028年起引入资本利得税】金十数据9月30日讯，荷兰政府计划……", "title": ""},
         "id": "20260930170245276800", "important": 1, "time": "2026-09-30 17:02:45", "type": 0, "tags": []},
        {"data": {"content": "", "title": ""}, "id": "2", "important": 0, "time": "2026-09-30 17:01:00", "type": 0},
    ]}
    items = flash.parse_jin10(ctx(), data)
    assert len(items) == 1
    assert items[0].title == "荷兰政府计划从2028年起引入资本利得税" and items[0].important


def test_eastmoney():
    data = {"code": "1", "data": {"sortEnd": "1", "fastNewsList": [{
        "summary": "【东吴证券：发行股份购买资产事项中止审核】东吴证券(601555.SH)公告称……",
        "code": "202609303887740418", "titleColor": 0, "showTime": "2026-09-30 16:58:24",
        "title": "东吴证券：发行股份购买资产事项中止审核"}]}}
    [it] = flash.parse_eastmoney(ctx(), data)
    assert it.url == "https://finance.eastmoney.com/a/202609303887740418.html"
    assert it.published.hour == 16 and not it.important


def test_sina_ext_docurl_and_stocks():
    data = {"result": {"data": {"feed": {"list": [{
        "id": 5120597, "rich_text": "香港交易所信息显示，贝莱德在长飞光纤光缆H股的持股比例升至6.93%。",
        "create_time": "2026-09-30 17:03:17", "is_focus": 0, "tag": [{"id": "8", "name": "其他"}],
        "ext": '{"stocks":[{"key":"香港交易所"},{"key":"贝莱德"}],"docurl":"https://finance.sina.com.cn/7x24/doc-x.shtml"}',
        "docurl": "https://finance.sina.cn/x"}]}}}}
    [it] = flash.parse_sina(ctx(), data)
    assert it.url == "https://finance.sina.com.cn/7x24/doc-x.shtml"
    assert it.stocks == ["香港交易所", "贝莱德"] and it.tags == []


def test_ths_important_color():
    data = {"code": "200", "data": {"list": [
        {"id": "1", "seq": "680414954", "title": "中国人民银行：将开展1.2万亿元买断式逆回购", "digest": "……",
         "url": "https://news.10jqka.com.cn/20260930/c680414954.shtml", "color": "2", "import": "3",
         "ctime": "1790758875", "tags": [{"name": "A股"}], "stock": []},
        {"id": "2", "seq": "2", "title": "普通资讯", "digest": "内容", "url": "", "color": "1", "import": "0",
         "ctime": "1790758800", "tags": [], "stock": []}]}}
    a, b = flash.parse_ths(ctx(), data)
    assert a.important and not b.important


def test_stcn_and_yicai():
    stcn = {"state": 1, "data": [{"id": "4204288", "url": "/article/detail/4204288.html", "title": "恒坤新材：TEOS产能利用率50%以上",
                                  "time": 1790758677000, "show_time": "1790758677", "pageTime": "4204288", "isRed": 0,
                                  "content": "人民财讯9月30日电，……",
                                  "tags": [[{"title": "TEOS"}], [{"title": "恒坤新材", "stock_code": "sh688727"}]]}]}
    [it] = flash.parse_stcn(ctx(), stcn)
    assert it.url == "https://www.stcn.com/article/detail/4204288.html" and it.tags == ["TEOS"]

    yicai = [{"CreateDate": "2026-09-30T16:38:05", "IsImportant": True, "LiveContent": "9月30日，A股共计56只个股涨停。",
              "LiveID": 103383084, "LiveTitle": "连板股追踪丨A股今日共56只个股涨停", "url": "/brief/103383084.html", "id": 103383084}]
    [it] = flash.parse_yicai(ctx(), yicai)
    assert it.important and it.url == "https://www.yicai.com/brief/103383084.html"
    assert flash.parse_yicai(ctx(), "error string") == []


def test_quotes_order_and_names():
    markets = [{"secid": "100.SPX", "name": "标普500"}, {"secid": "100.DJIA", "name": "道琼斯"}]
    data = {"data": {"diff": [
        {"f2": 51349.92, "f3": -0.26, "f4": -131.59, "f12": "DJIA", "f14": "道琼斯", "f124": 1790711986},
        {"f2": 7670.84, "f3": -0.17, "f4": -12.85, "f12": "SPX", "f14": "标普500", "f124": 1790711986}]}}
    rows = parse_quotes(data, markets)
    assert [r["code"] for r in rows] == ["SPX", "DJIA"]
