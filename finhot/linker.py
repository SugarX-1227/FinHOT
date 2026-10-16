"""实体链接：一条新闻点到了哪些上市公司、各自是什么角色。

只认“名字出现了”不够：实测 10/10–10/14 提到“中信证券”的 15 条里，10 条是“中信证券：看好……”的研报观点，
其余是保荐人、承销商名单，没有一条在说中信证券自己。所以每次点名都判一个角色：

  主体       标题以“公司名：”开头（“东百集团：拟收购……”），或者标题里点名、说的就是它的事
  标题点名   标题里出现，但不是标题的主语（“长江证券：维持蓝思科技买入评级”里的蓝思科技）
  信源关联   正文里出现，信源（财联社等）也把它标成了关联个股
  正文提及   只在正文里出现
  行情名单   收评、异动里一串名字中的一个（“中国银行、中信银行、工商银行续创新高”）
  观点来源   券商研报、机构观点的发布方（“中信证券：金价底部或抬升”里的中信证券）
  中介       保荐人、承销商、托管人、财务顾问

角色的权重（个人简报里的“关联强度”要乘上它）在 sources/personal.yaml 的 role_weights。

匹配规则：
- 代码只认带括号或交易所前后缀的写法（“（600519）”“SZ300750”“600519.SH”），裸的六位数字多半是金额；
- 名字按最长匹配（“中信建投”不会被当成“中信……”），简称、去掉 ST 的简称、别称、曾用名都认；
- 本身是常用词或地名的简称（机器人、太平洋、金融街、天津港，见 aliases.yaml 的 generic）只有带代码、“简称：”开头，
  或后面紧跟“股份 / 公司 / 涨停 / 股价 / 公告”时才算；两个字的简称一律这样处理（还认“柳工发布”“闻泰拟”这种后面跟公司动作的）；
  *ST 股去掉前缀后不到 4 个字的不认（“*ST发展”的“发展”）；
- 一个名字对应几家公司（曾用名和现名撞了）时，现名优先，否则都不认。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .entities import Book, book as default_book
from .models import NewsItem

ROLES = ("主体", "标题点名", "信源关联", "正文提及", "行情名单", "观点来源", "中介")
_ROLE_ORDER = {r: i for i, r in enumerate(ROLES)}

_CODE = re.compile(r"(?:(?<=[（(])|(?<![A-Za-z0-9]))(?:(SH|SZ|BJ|sh|sz|bj)\.?)?(\d{6})(?:\.(SH|SZ|BJ|sh|sz|bj))?(?![\d])")
_OPINION = re.compile(r"看好|维持|首次覆盖|上调|下调|评级|目标价|预计|认为|建议|有望|或将|料将|观点|策略|展望|研判|关注|配置|"
                      r"仍将|底部|拐点|景气|空间|机会|主线|周期")
_BROKER = re.compile(r"(?:证券|建投|中金公司|期货|基金|资管|研究所|研究院)$")
_SOURCE_AFTER = re.compile(r"^(?:研报|研究|认为|表示|指出|预计|分析师|首席|策略|宏观|称|发布研报|最新研报)")
_INTERMEDIARY = re.compile(r"(?:保荐人?|保荐机构|承销商|主承销|联席|托管人|财务顾问|受托管理人|审计机构|律师事务所)"
                           r"[为是：:（(]?[^。；]{0,30}$")
_INTERMEDIARY_AFTER = re.compile(r"^[^。；，]{0,6}(?:担任|作为|为)?(?:保荐|承销|托管|财务顾问)")
_RECAP = re.compile(r"涨停|跌停|涨超|跌超|涨逾|跌逾|走强|走弱|拉升|跳水|领涨|领跌|跟涨|冲高|大涨|大跌|异动|续创|新高|新低|"
                    r"板块|概念股|集体|纷纷|等个股|等股")
_GENERIC_OK_AFTER = re.compile(r"^(?:（\d{6}|\(\d{6}|股份|集团|涨停|跌停|股价|公告|发布公告|董事|股东|：|:)")
_OWN_EVENT = re.compile(r"净利|营收|业绩|股东|董事|高管|回购|增持|减持|分红|发债|债券|处罚|罚单|立案|问询|获批|设立|子公司|"
                        r"收购|并购|重组|定增|募资|公告|聘任|辞职|诉讼|中标|合同|协议|发行股份|购买资产|审核|注册|停牌|复牌|"
                        r"摘牌|退市|经营|利润|收入|资本金|子公司|分公司|营业部")
# 收评、午评、异动这类行情播报：正文里点到的股票都是名单里的一个
_RECAP_TITLE = re.compile(r"^(?:收评|午评|早评|A股收盘|A股午盘|港股收盘|港股开盘|竞价|盘前|盘中|涨停分析|复盘)|收评|午评|"
                          r"概念|板块|异动|集体|涨停|跌停|走强|走弱|拉升|跳水")
# 机构观点打头的标题：“中泰证券：维持芒果超媒增持评级”（发布方不一定在股票清单里）
_OPINION_TITLE = re.compile(r"^[^：:，,。]{2,12}(?:证券|建投|中金公司|期货|研究所|研究院|基金|资管|银行|资本)[：:]")
# 两个字的简称（柳工、闻泰）后面跟着公司动作才算点到它（“柳工发布”“闻泰拟”），否则多半是常用词
_SHORT_OK_AFTER = re.compile(r"^(?:发布|公告|回应|宣布|拟|称|表示|董事|总裁|旗下|子公司|业绩|营收|净利|中标|获得|签署|签订|"
                             r"股份|集团|科技|控股|收购|回购|增持|减持)")
_SENTENCE = re.compile(r"[^。；;！!？?\n]+")


@dataclass
class Mention:
    code: str
    name: str          # 当前简称
    text: str          # 原文里的写法
    role: str
    in_title: bool

    def to_dict(self) -> dict:
        return {"code": self.code, "name": self.name, "text": self.text, "role": self.role, "in_title": self.in_title}


class Linker:
    def __init__(self, book: Book | None = None):
        self.book = book or default_book()
        by_first: dict[str, list[str]] = {}
        for name in self.book.names:
            by_first.setdefault(name[0], []).append(name)
        self._by_first = {k: sorted(v, key=len, reverse=True) for k, v in by_first.items()}

    # ---------------------------------------------------------- 找出名字
    def _spans(self, text: str) -> list[tuple[int, int, str, str]]:
        """(起, 止, 原文写法, 代码)，最长匹配、不重叠。"""
        out: list[tuple[int, int, str, str]] = []
        for m in _CODE.finditer(text):
            code = m.group(2)
            bracketed = m.start() > 0 and text[m.start() - 1] in "（("
            if (m.group(1) or m.group(3) or bracketed) and self.book.get(code):
                out.append((m.start(), m.end(), m.group(0), code))
        taken = [False] * len(text)
        for s, e, _, _ in out:
            for k in range(s, e):
                taken[k] = True
        i = 0
        while i < len(text):
            if taken[i]:
                i += 1
                continue
            hit = next((n for n in self._by_first.get(text[i], ()) if text.startswith(n, i)), None)
            if not hit:
                i += 1
                continue
            # 前面紧挨着中文的、像是更长词的一部分（如“中国卫星互联网”里的“中国卫星”）交给 generic 规则判断
            code = self._pick(hit, text, i)
            if code:
                out.append((i, i + len(hit), hit, code))
            i += len(hit)
        return sorted(out)

    def _pick(self, name: str, text: str, at: int) -> str:
        codes = self.book.names.get(name) or []
        if len(codes) > 1:   # 一名多码：在市的优先（北交所老代码、退市的同名公司），再看是不是现名
            listed = [c for c in codes if self.book.stocks[c].status == "listed"]
            codes = listed or codes
        if len(codes) > 1:
            current = [c for c in codes if self.book.stocks[c].name == name or self.book.stocks[c].bare == name]
            if len(current) != 1:
                return ""
            codes = current
        if name in self.book.generic or len(name) <= 2:
            after = text[at + len(name):]
            ok = _GENERIC_OK_AFTER.match(after) or (len(name) <= 2 and _SHORT_OK_AFTER.match(after))
            if not (at == 0 and after[:1] in "：:") and not ok:
                return ""
        return codes[0]

    # ---------------------------------------------------------- 判角色
    def _role(self, code: str, written: str, start: int, end: int, text: str, title_len: int,
              title_spans: list, source_stocks: set[str]) -> str:
        in_title = start < title_len
        name = self.book.stocks[code].name
        before, after = text[max(0, start - 40):start], text[end:end + 12]
        sentence = next((m.group(0) for m in _SENTENCE.finditer(text) if m.start() <= start < m.end()), "")
        if _INTERMEDIARY.search(before) or _INTERMEDIARY_AFTER.match(after):
            return "中介"
        prefix = in_title and re.match(r"^\s*[（(]?\d{0,6}[）)]?\s*$", text[:start]) is not None \
            and re.match(r"^(?:[（(][^）)]{0,12}[）)])?\s*[：:]", text[end:title_len]) is not None
        rest = text[end:title_len]
        broker = _BROKER.search(name) is not None or self.book.stocks[code].industry in ("证券", "证券Ⅱ", "多元金融")
        # 券商、机构打头的标题默认是观点，除非说的是它自己的经营（业绩、股东、处罚……）
        own = _OWN_EVENT.search(rest) and not _OPINION.search(rest)   # “利润有望创新高”说的是行业，不是它自己
        if broker and (prefix and not own or _SOURCE_AFTER.match(after)):
            return "观点来源"
        if prefix:
            return "主体"
        title = text[:title_len]
        others = sum(1 for s in title_spans if s[3] != code) if in_title else 0
        names_in_sentence = sentence.count("、") >= 2 and _RECAP.search(sentence)
        if names_in_sentence and not (in_title and others == 0):
            return "行情名单"
        if in_title:
            # 别家券商观点的对象（“长江证券：维持蓝思科技买入评级”）、行情播报里被点名的，算标题点名；其余就是在说它
            opinion_target = _OPINION_TITLE.match(title) is not None and not title.startswith(written)
            listed = title.count("、") >= 2 and ("、" in (text[start - 1:start] + text[end:end + 1]))
            return "标题点名" if opinion_target or listed or _RECAP.search(title) or _RECAP_TITLE.match(title) else "主体"
        if _RECAP_TITLE.search(title):
            return "行情名单"
        if name in source_stocks or written in source_stocks:
            return "信源关联"
        return "正文提及"

    def link(self, it: NewsItem) -> list[Mention]:
        title = (it.title or "").strip()
        content = it.content or ""
        if content.startswith(title):
            content = content[len(title):]
        text = f"{title}\n{content}"
        spans = self._spans(text)
        title_spans = [s for s in spans if s[0] < len(title)]
        source_stocks = set(it.stocks or [])
        best: dict[str, Mention] = {}
        for s, e, written, code in spans:
            role = self._role(code, written, s, e, text, len(title), title_spans, source_stocks)
            m = Mention(code, self.book.stocks[code].name, written, role, s < len(title))
            old = best.get(code)
            if old is None or _ROLE_ORDER[role] < _ROLE_ORDER[old.role]:
                best[code] = m
        return sorted(best.values(), key=lambda m: _ROLE_ORDER[m.role])


_default: Linker | None = None


def link(it: NewsItem) -> list[Mention]:
    global _default
    if _default is None or _default.book is not default_book():
        _default = Linker()
    return _default.link(it)
