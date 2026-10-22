"""关系库：持仓 / 自选的上下游、客户、供应商、合作方、竞品。

不让模型凭记忆归纳（模型不联网，“来源”只能编；A 股年报的前五大客户也大多匿名）。这里只从真实的原文里找：
新闻和公告里同一句话同时点到你的股票和另一家上市公司、又有“供货 / 订单 / 定点 / 合作 / 竞争”这类词，
就生成一条候选关系，附原文链接、原句、置信度和生成时间，状态是“待审核”。

- 待审核的不参与排序；在画像文件里把 status 改成“已确认”（或者自己写一条 source: 人工维护）才用；
- 再次归纳不会覆盖已有的条目（同一对公司、同一种关系只留一条），人工改过的永远不动；
- 只走一层：关联公司的关联公司不算。

  python -m finhot profile relations 我 --days 30    # 从 data/ 最近 30 天的快讯和公告里找候选，并进画像
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path

from .config import public_data_dir
from .entities import Book, book as default_book
from .linker import Linker
from .models import NewsItem
from .profile import Profile, Relation, now_stamp

_SENTENCE = re.compile(r"[^。；;！!？?\n]+")
# (模板, 关系, 置信度)。{A} 是句子里先出现的公司，{B} 是后出现的；关系说的是“B 是 A 的什么”
_TEMPLATES: list[tuple[re.Pattern, str, str, float]] = [
    (re.compile(r"{A}.{0,8}(?:向|为|给){B}.{0,10}(?:供货|供应|配套|提供)"), "B是A的", "客户", 0.7),
    (re.compile(r"{A}.{0,10}(?:进入|成为){B}.{0,8}(?:供应链|供应商|合格供应商)"), "B是A的", "客户", 0.7),
    (re.compile(r"{A}.{0,10}(?:获得?|收到|斩获|中标){B}.{0,10}(?:订单|定点|项目|合同)"), "B是A的", "客户", 0.65),
    (re.compile(r"{A}.{0,10}(?:向|从){B}.{0,8}(?:采购|购买)"), "B是A的", "供应商", 0.65),
    (re.compile(r"{A}.{0,12}(?:与|和|同){B}.{0,12}(?:签署|签订|达成|开展|深化).{0,12}(?:合作|协议)"), "B是A的", "合作", 0.6),
    (re.compile(r"{A}.{0,12}(?:与|和){B}.{0,10}(?:竞争|竞品|对标|抢夺)"), "B是A的", "竞品", 0.5),
]
_REVERSE = {"客户": "供应商", "供应商": "客户", "合作": "合作", "竞品": "竞品"}


def _relations_in(sentence: str, a: str, b: str) -> list[tuple[str, float]]:
    """句子里 b 是 a 的什么（可能没有）。"""
    out = []
    for pat, _, kind, conf in _TEMPLATES:
        if re.search(pat.pattern.replace("{A}", re.escape(a)).replace("{B}", re.escape(b)), sentence):
            out.append((kind, conf))
    return out


def suggest(p: Profile, items: list[NewsItem], b: Book | None = None, at: datetime | None = None) -> list[Relation]:
    """从给定的快讯 / 公告里找画像股票的候选关系（待审核）。"""
    b = b or default_book()
    linker = Linker(b)
    stamp = (at.isoformat(timespec="seconds") if at else now_stamp())
    mine = set(p.codes)
    found: dict[tuple[str, str, str], Relation] = {}
    for it in items:
        text = f"{it.title}。{it.content}"
        mentions = linker.link(it)
        codes = {m.code for m in mentions}
        if not codes & mine or len(codes) < 2:
            continue
        written = {m.code: {m.text, b.get(m.code).name, b.get(m.code).bare} for m in mentions}
        for sentence in _SENTENCE.findall(text):
            for h in codes & mine:
                for x in codes - {h}:
                    for hn in written[h]:
                        for xn in written[x]:
                            if hn not in sentence or xn not in sentence:
                                continue
                            # x 是 h 的什么：h 在前时直接读；x 在前时读“h 是 x 的什么”再反过来
                            rels = _relations_in(sentence, hn, xn) or [
                                (_REVERSE[k], c) for k, c in _relations_in(sentence, xn, hn)]
                            for kind, conf in rels:
                                key = (h, x, kind)
                                if key not in found or found[key].confidence < conf:
                                    found[key] = Relation(h, x, kind, source=it.url or it.source_name, confidence=conf,
                                                          status="待审核", generated=stamp, quote=sentence.strip()[:120])
    return list(found.values())


def load_items(days: int, until: datetime, data_dir: Path | None = None) -> list[NewsItem]:
    """data/ 里最近几天的快讯和公告（公开数据）。"""
    data_dir = data_dir or public_data_dir()
    out: dict[str, NewsItem] = {}
    for d in range(days + 1):
        folder = data_dir / (until - timedelta(days=d)).strftime("%Y-%m-%d")
        for path in sorted(folder.glob("items*.jsonl")) + sorted(folder.glob("announcements*.jsonl")):
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    it = NewsItem.from_dict(json.loads(line))
                    out.setdefault(it.uid, it)
    return list(out.values())
