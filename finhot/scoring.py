"""不依赖 LLM 的规则分类与重要性打分。

LLM 不可用（没配 key / 超预算 / 接口挂了）时，日报完全靠这里的结果生成，
所以规则要保守：宁可少选，不要把噪音排到前面。关键词表可以随时调整。
"""

from __future__ import annotations

import re

from .models import NewsItem

CATEGORIES = ["政策", "宏观", "海外", "市场", "行业", "公司"]

# (类别, 正则)。按顺序匹配，先命中先得。
_CATEGORY_RULES: list[tuple[str, re.Pattern]] = [
    ("政策", re.compile(
        r"国务院|中共中央|政治局|国常会|证监会|金融监管总局|国家金融监督|发改委|发展改革委|财政部|工信部|工业和信息化部|"
        r"商务部|国资委|住建部|住房和城乡建设部|农业农村部|国家能源局|市场监管总局|网信办|外汇局|国家外汇管理局|"
        r"印发|指导意见|实施方案|管理办法|条例|十五五|规划纲要|新闻发布会|两会|政策|监管|上交所|深交所|北交所")),
    ("海外", re.compile(
        r"美联储|鲍威尔|欧洲央行|欧央行|日本央行|英国央行|美国|美股|纳斯达克|纳指|道指|标普|欧元区|欧盟|日本|韩国|英国|德国|法国|"
        r"俄罗斯|乌克兰|伊朗|以色列|中东|特朗普|白宫|华尔街|非农|美债|美元指数|OPEC|欧佩克")),
    ("宏观", re.compile(
        r"人民银行|央行|降准|降息|LPR|MLF|逆回购|公开市场|国债|专项债|社融|M1|M2|GDP|CPI|PPI|PMI|"
        r"统计局|经济数据|工业增加值|社会消费品零售|固定资产投资|进出口|外贸|外汇储备|人民币汇率|中间价|失业率|通胀")),
    ("市场", re.compile(
        r"收评|午评|收盘|开盘|沪指|深成指|创业板指|科创50|北证50|上证指数|A股|两市|成交额|涨停|跌停|连板|北向资金|南向资金|"
        r"主力资金|融资余额|龙虎榜|港股|恒生指数|恒指|ETF|期指|国债期货|大宗商品|黄金|原油|碳酸锂|铜价")),
    ("公司", re.compile(
        r"^[一-龥A-Za-z0-9]{2,10}[：:]|公告|回购|增持|减持|业绩|净利润|营收|中标|签署|签约|股东|董事长|"
        r"定增|募资|分红|停牌|复牌|收购|重组|IPO|上市|摘牌|退市|ST")),
]

# (加分, 正则)：命中即加分，取所有命中项之和，封顶
_BOOST_RULES: list[tuple[float, re.Pattern]] = [
    (2.5, re.compile(r"降准|降息|加息|LPR|政治局会议|国常会|国务院常务会议|中央经济工作会议|印花税|平准基金|汇金|"
                     r"特别国债|万亿|非农|议息|FOMC|利率决议|关税")),
    (1.5, re.compile(r"证监会|央行|人民银行|国务院|发改委|财政部|金融监管总局|美联储|鲍威尔|CPI|PPI|PMI|GDP|社融|"
                     r"重磅|首次|突发|紧急|制裁|战争|停火|暴涨|暴跌|熔断|IPO暂停|退市")),
    (1.0, re.compile(r"重组|并购|收购|回购|增持|举牌|业绩预增|业绩预告|大单|中标|订单|涨价|提价|减产|停产|"
                     r"新规|征求意见|试点|补贴|以旧换新|国产替代|芯片|半导体|人工智能|AI|算力|机器人|固态电池|创新药")),
]

# 噪音：盘中异动、个股涨跌播报、互动平台问答等，降分
_NOISE_RULES: list[tuple[float, re.Pattern]] = [
    (2.0, re.compile(r"盘中|快速拉升|快速下跌|直线拉升|异动|涨超\d|跌超\d|涨逾|跌逾|涨幅扩大|跌幅扩大|盘前涨|盘前跌|"
                     r"日内涨|日内跌|拉升|跳水|走高|走低|冲高|翻绿|翻红|触及涨停|封涨停|竞价")),
    (1.0, re.compile(r"互动平台|投资者提问|回答投资者|接受调研|调研纪要|股东户数|成立.{0,12}公司|注册资本")),
    (1.0, re.compile(r"龙虎榜|融资余额|主力资金.{0,4}净(流入|流出)")),
]


def classify(it: NewsItem) -> str:
    text = f"{it.title} {it.content[:120]}"
    if it.lang == "en":
        return "海外"
    for cat, pat in _CATEGORY_RULES:
        if pat.search(it.title if cat == "公司" else text):
            return cat
    return "行业"


def rule_score(it: NewsItem) -> float:
    text = f"{it.title} {it.content[:200]}"
    s = 3.0
    if it.important:
        s += 2.0
    if it.tier == "official":
        s += 1.5
    n_src = 1 + len(it.dup_sources)
    s += min(2.5, 0.8 * (n_src - 1))  # 多家同时报道 → 更重要
    s += (it.weight - 1.0)
    s += min(3.0, sum(w for w, p in _BOOST_RULES if p.search(text)))
    s -= sum(w for w, p in _NOISE_RULES if p.search(it.title))
    if len(it.content) < 25 and not it.important:
        s -= 1.0
    if it.lang == "en":
        s -= 0.5  # 英文源作补充，默认略降权
    return round(max(0.0, min(10.0, s)), 2)


def score_all(items: list[NewsItem]) -> list[NewsItem]:
    for it in items:
        it.category = classify(it)
        it.score = rule_score(it)
    items.sort(key=lambda x: (x.score, x.published), reverse=True)
    return items
