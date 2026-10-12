"""涨停原因归因：每只涨停股为什么涨停，附上依据。

依据按可信度从高到低：
1. 公司消息：标题点名这家公司的新闻（业绩、中标、重组、获批、澄清……）；
2. 盘面播报：收评、异动里点名它的那句话（“影视传媒板块爆发，上海电影、……涨停”），从句子里读出板块；
3. 同花顺涨停原因（能取到时）：专业整理的题材标签，如“固态电池+业绩增长”；
4. 题材：所属行业对应的题材，当天这个题材有几条新闻、几家涨停；领涨某个概念板块也记上。

规则先给出原因和可信度（高 / 中 / 低）；配了 LLM 时再请模型按依据写一句话原因，写出依据里没有的公司就不用。
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from .ashare import MarketSnapshot
from .crosscheck import ThemeRow
from .models import NewsItem
from .themes import board_themes, themes_in

log = logging.getLogger(__name__)

# 公司新闻分两类：催化（业绩、订单、重组、获批……）是涨停的原因；提示（澄清、异常波动、问询……）是公司在给炒作降温，
# 原因还得从题材里找，提示附在后面
_NOTICE = re.compile(
    r"澄清|未涉及|不涉及|未直接涉及|不存在|无.{0,6}合作|尚处|较小|有限|异常波动|问询|关注函|监管函|风险提示|停牌核查|传闻|终止|"
    r"不具备|没有.{0,6}业务")
_CATALYST = re.compile(
    r"业绩|预增|预盈|扭亏|净利润|营收|快报|中标|合同|订单|签署|签订|重组|收购|并购|增持|回购|举牌|获批|批准|临床|上市申请|"
    r"发布|推出|量产|投产|涨价|提价|定增|募资|控制权|实控人|战略合作|入股|分红|送转|摘帽|中报|三季报|年报|突破|首发|首款")
# 行情播报：一句话点名多只股票
_RECAP = re.compile(r"涨停|板块|概念|走强|拉升|爆发|大涨|异动|集体|连板|封板|涨超|领涨")
# 句子里的板块名：“影视传媒板块爆发”“固态电池概念股走强”“算力方向”
_BOARD_PHRASE = re.compile(r"([一-龥A-Za-z0-9、]{2,12}?)(?:板块|概念股|概念|方向|产业链|赛道)")
_SENTENCE = re.compile(r"[^。；;！!？?\n]+")
_CLAUSE = re.compile(r"[^，,：:]+")
_PHRASE_STOP = re.compile(r"^(?:其中|今日|午后|早盘|盘中|尾盘|随后|个股|市场|两市|沪深|A股|此外|同时|另外)")


@dataclass
class Evidence:
    kind: str          # 公司 / 盘面 / 同花顺 / 题材 / 板块
    text: str
    source: str = ""
    url: str = ""


@dataclass
class LimitUp:
    code: str
    name: str
    boards: int = 1
    first_time: str = ""
    industry: str = ""
    stat: str = ""
    reason: str = ""
    confidence: str = "低"   # 高 / 中 / 低
    themes: list[str] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)
    ths_reason: str = ""
    written_by: str = "规则"  # 规则 / 模型

    def to_dict(self) -> dict:
        return asdict(self)


def _sentences_with(name: str, text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE.findall(text) if name in s]


def _clause_context(name: str, sentence: str, names: set[str] = frozenset()) -> str:
    """句子里描述这只股票的那一段：从它所在的分句往前，接到最近一个点了板块或题材的分句（最多六句）。
    收评的写法一般是“X 概念走强，A、B 涨停，C 跟涨”，前面最近的板块就是它的板块。"""
    clauses = [c.strip() for c in _CLAUSE.findall(sentence) if c.strip()]
    for i, c in enumerate(clauses):
        if name not in c:
            continue
        for j in range(i, max(-1, i - 7), -1):
            bare = clauses[j]
            for n in names | {name}:  # 股票名里的“黄金”“传媒”不算板块
                bare = bare.replace(n, "")
            if _board_phrase(bare) or themes_in(bare):
                return "，".join(clauses[j:i + 1])
        return c
    return sentence


def _board_phrase(sentence: str) -> str:
    """句子里点到的板块，如“影视传媒板块爆发” → “影视传媒”。取不出就返回空串。"""
    for m in _BOARD_PHRASE.finditer(sentence):
        phrase = re.sub(r"^.*?[，,：:（(]", "", m.group(1)).strip("、")
        phrase = _PHRASE_STOP.sub("", phrase)
        if 2 <= len(phrase) <= 9 and not re.fullmatch(r"[\d.%、]+", phrase):
            return phrase
    return ""


def _gist(title: str, name: str, limit: int = 30) -> str:
    """公司新闻标题去掉“公司名：”前缀，截短成原因（在空格或标点处截断）。"""
    t = re.sub(rf"^[^：:]{{0,12}}{re.escape(name)}[^：:]{{0,6}}[：:]\s*", "", title).strip() or title
    if len(t) <= limit:
        return t
    cut = max(t.rfind(ch, 0, limit) for ch in " ，,、；;")
    return t[:cut] if cut >= limit // 2 else t[:limit] + "…"


def _kind(title: str) -> str:
    """公司新闻的类别：催化 / 提示 / 其他。"""
    if _NOTICE.search(title):
        return "提示"
    return "催化" if _CATALYST.search(title) else "其他"


def _company_evidence(name: str, items: list[NewsItem]) -> list[Evidence]:
    """标题点名这家公司的新闻，催化在前；同一标题只留一条。"""
    out, seen = [], set()
    for it in items:
        title = it.llm.get("title") or it.title
        if (name in it.title or name in title) and title not in seen:
            seen.add(title)
            out.append(Evidence("公司", title, it.source_name, it.url))
    order = {"催化": 0, "提示": 1, "其他": 2}
    out.sort(key=lambda e: order[_kind(e.text)])
    return out


def _recap_evidence(name: str, items: list[NewsItem], zt_names: set[str]) -> list[Evidence]:
    """行情播报里点名这只股票的句子（同一句里还点了别的涨停股、或有“涨停/板块”这类词）。"""
    out, seen = [], set()
    for it in items:
        if name in it.title:
            continue
        for s in _sentences_with(name, it.content):
            others = sum(1 for n in zt_names if n != name and n in s)
            context = _clause_context(name, s, zt_names)
            if (others or _RECAP.search(s)) and context not in seen:
                seen.add(context)
                out.append(Evidence("盘面", context[:120], it.source_name, it.url))
    return out


def attribute(snap: MarketSnapshot, items: list[NewsItem], themes: list[ThemeRow] | None = None,
              ths_reasons: dict[str, str] | None = None) -> list[LimitUp]:
    """给快照里的每只涨停股找原因。items 是用来找依据的新闻（越全越好，含前一两天的）。"""
    ths_reasons = ths_reasons if ths_reasons is not None else snap.zt_reasons
    theme_rows = {r.theme: r for r in (themes or [])}
    zt_names = {s["name"] for s in snap.zt_stocks}
    leaders = {b.leader: b for b in sorted(snap.concepts, key=lambda b: -b.pct) if b.leader and b.pct > 0}

    rows: list[LimitUp] = []
    for s in snap.zt_stocks:
        r = LimitUp(code=s["code"], name=s["name"], boards=s.get("boards", 1), first_time=s.get("first_time", ""),
                    industry=s.get("industry", ""), stat=s.get("stat", ""), ths_reason=ths_reasons.get(s["code"], ""))
        company = _company_evidence(r.name, items)
        recap = _recap_evidence(r.name, items, zt_names)
        r.evidence += company[:2] + recap[:2]
        if r.ths_reason:
            r.evidence.append(Evidence("同花顺", r.ths_reason))
        board = leaders.get(r.name)
        if board:
            suffix = "" if board.name.endswith("概念") else "概念"
            r.evidence.append(Evidence("板块", f"领涨“{board.name}”{suffix}（{board.pct:+.2f}%）"))

        found = themes_in(" ".join([r.ths_reason, *(e.text.replace(r.name, "") for e in recap[:2])]))
        r.themes = list(dict.fromkeys([*found, *board_themes(r.industry)]))[:3]
        rows.append(r)

    # 同题材涨停家数：同一天扎堆涨停的题材更可能是主线
    peers = Counter(t for r in rows for t in r.themes[:1])
    for r in rows:
        company = [e for e in r.evidence if e.kind == "公司"]
        catalyst = next((e for e in company if _kind(e.text) == "催化"), None)
        notice = next((e for e in company if _kind(e.text) == "提示"), None)
        other = next((e for e in company if _kind(e.text) == "其他"), None)
        recap = [e for e in r.evidence if e.kind == "盘面"]
        phrase = next((p for p in (_board_phrase(e.text.replace(r.name, "")) for e in recap) if p), "")
        if not phrase and recap:
            phrase = next((t for t in (themes_in(e.text.replace(r.name, "")) for e in recap) for t in t), "")
        concept = next((e.text.split("“")[1].split("”")[0] for e in r.evidence if e.kind == "板块"), "")
        theme = r.themes[0] if r.themes else ""
        tr = theme_rows.get(theme)
        if tr and tr.news:
            n = tr.news[0]
            r.evidence.append(Evidence("题材", f"{theme}：{(n.llm.get('title') or n.title)[:40]}", n.source_name, n.url))

        if r.ths_reason:
            r.reason, r.confidence = r.ths_reason, "高" if catalyst else "中"
        elif catalyst:
            r.reason, r.confidence = _gist(catalyst.text, r.name), "高"
        elif phrase:
            r.reason, r.confidence = f"{phrase}走强", "中"
        elif concept:
            r.reason, r.confidence = f"{concept}{'' if concept.endswith('概念') else '概念'}领涨", "中"
        elif other:
            r.reason, r.confidence = _gist(other.text, r.name), "中"
        elif theme and peers[theme] >= 3:
            r.reason, r.confidence = f"{theme}题材（同题材涨停 {peers[theme]} 家）", "低"
        elif theme:
            r.reason, r.confidence = f"{theme}题材", "低"
        else:
            r.reason, r.confidence = r.industry or "原因不明", "低"
        if notice and not catalyst:
            r.reason += f"（公司提示：{_gist(notice.text, r.name, 18)}）"
    rows.sort(key=lambda r: (-r.boards, r.first_time))
    return rows


def coverage(rows: list[LimitUp]) -> float:
    """有实际依据（公司消息、盘面播报、同花顺原因或领涨概念）的涨停股占比。"""
    if not rows:
        return 0.0
    ok = sum(1 for r in rows if any(e.kind in ("公司", "盘面", "同花顺", "板块") for e in r.evidence))
    return round(ok / len(rows) * 100, 1)


def recent_items(data_dir: Path, until: datetime, days: int = 2) -> list[NewsItem]:
    """data/ 里最近几天保存的新闻（找依据用：公司公告常在前一天晚上）。"""
    out: list[NewsItem] = []
    for d in range(days + 1):
        day = (until - timedelta(days=d)).strftime("%Y-%m-%d")
        for path in sorted((data_dir / day).glob("items*.jsonl")):
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    out.append(NewsItem.from_dict(json.loads(line)))
    return out


# ------------------------------------------------------------------ 可选：模型写一句话原因
_PROMPT = """你是 A 股复盘编辑。下面是今天每只涨停股和程序找到的依据（新闻标题、盘面播报原句、题材标签）。
为每只股票写一句涨停原因，12~28 个字，像“固态电池量产预期+三季报预增”“澄清与华为合作后仍封板，炒作华为概念”。
规则：
- 只能用给出的依据，不能编造依据里没有的事件、数字或公司；
- 依据只有行业 / 题材时，写成“××题材跟涨”这类保守说法；
- 不预测后市，不写“建议”“看好”。
只输出 JSON：{"原因": {"股票代码": "一句话原因", ...}}"""


def write_reasons(rows: list[LimitUp], client, limit: int = 60) -> int:
    """请模型按依据写原因；校验不通过的保留规则原因。返回改写的条数。"""
    picked = rows[:limit]
    if not picked:
        return 0
    payload = [{"代码": r.code, "股票": r.name, "连板": r.boards, "行业": r.industry,
                "依据": [f"[{e.kind}] {e.text}" for e in r.evidence[:5]] or [f"[行业] {r.industry}"]} for r in picked]
    try:
        text = client.chat(_PROMPT, json.dumps(payload, ensure_ascii=False), json_mode=True, max_tokens=6000)
        data = json.loads(text[text.find("{"): text.rfind("}") + 1])
        reasons = data.get("原因") or data
    except Exception as e:  # noqa: BLE001
        log.warning("模型写涨停原因失败，保留规则原因: %s", e)
        return 0
    others = {r.name for r in rows}
    n = 0
    for r in picked:
        text = str(reasons.get(r.code) or "").strip()
        evidence = " ".join(e.text for e in r.evidence) + r.industry + r.ths_reason
        foreign = [o for o in others if o != r.name and o in text and o not in evidence]
        if 6 <= len(text) <= 40 and not foreign and not re.search(r"建议|看好|买入|卖出", text):
            r.reason, r.written_by = text, "模型"
            n += 1
    return n
