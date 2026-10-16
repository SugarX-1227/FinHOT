"""个人排序：哪些消息和“你”有关、为什么给你、排在哪。

排序分 = 通用重要性 × 关联强度 × 点名方式 × 新鲜度 × 反馈修正，权重都在 sources/personal.yaml。
- 通用重要性：公告看类型表（sources/announcements.yaml），快讯用日报同一套分数（配了模型的是模型分，
  网站精选过的取网站评分），0~10；
- 关联强度：持仓 > 自选 > 关系库里的关联公司（一层）> 关注题材 > 同行业 > 宏观；
- 点名方式：新闻里这家公司是主体、标题点名、正文提及、行情名单、观点来源……（finhot/linker.py）；
- 新鲜度：按发布了多久打折；同一件事几天内给过就不再给（finhot/history.py）；
- 反馈修正：按你对各类理由标的“有用 / 不相关”调（finhot/feedback.py）。

简报不按一个总分混排，而是分栏（持仓 / 自选 / 关联公司 / 关注题材 / 宏观），栏内按排序分；
即时提醒用排序分和公告类型的 push 判断。

“为什么给你”只引用原文和关系库里的事实：持仓里的哪只股票、它在这条新闻里是什么角色、公告是什么类型、
关系是谁维护的。不写利好利空，不写预测。
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache

import yaml

from .announce import is_announcement, type_of
from .config import ROOT
from .entities import Book, book as default_book
from .linker import Linker, Mention
from .models import NewsItem
from .profile import Profile, Relation
from .textutil import normalize
from .themes import item_themes

PERSONAL_FILE = ROOT / "sources" / "personal.yaml"
SECTIONS = ("持仓", "自选", "关联公司", "关注题材", "宏观")
MACRO_CATEGORIES = ("政策", "宏观", "海外")


@lru_cache(maxsize=1)
def default_weights() -> dict:
    return yaml.safe_load(PERSONAL_FILE.read_text(encoding="utf-8")) or {}


@dataclass
class Pick:
    item: NewsItem
    section: str
    reason: str                    # 给读者看的“为什么给你”
    reason_kind: str               # 用来按反馈调权重的类别，如“持仓·公告”“持仓·主体”“关注题材”
    importance: float
    relevance: float
    role_weight: float = 1.0
    freshness: float = 1.0
    learned: float = 1.0
    code: str = ""
    theme: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def rank(self) -> float:
        return round(self.importance * self.relevance * self.role_weight * self.freshness * self.learned, 3)

    @property
    def id(self) -> str:
        return self.item.uid

    def to_dict(self) -> dict:
        it = self.item
        return {"id": self.id, "section": self.section, "reason": self.reason, "reason_kind": self.reason_kind,
                "rank": self.rank, "importance": self.importance, "relevance": self.relevance,
                "role_weight": self.role_weight, "freshness": self.freshness, "learned": self.learned,
                "code": self.code, "theme": self.theme, "title": it.llm.get("title") or it.title, "url": it.url,
                "published": it.published.isoformat(), "source": it.source_name, "generic_score": it.score}


def importance(it: NewsItem) -> float:
    if is_announcement(it):
        return type_of(it).importance
    engine = it.llm.get("engine_score")
    return max(it.score, float(engine) / 10) if engine is not None else it.score


def freshness(it: NewsItem, at: datetime, w: dict) -> float:
    age = max(0.0, (at - it.published).total_seconds() / 3600)
    for step in w.get("freshness") or []:
        if age <= float(step["hours"]):
            return float(step["factor"])
    return 0.3


def _role_label(m: Mention) -> str:
    return {"主体": "新闻主体", "标题点名": "标题点名", "信源关联": "正文提及（信源标注关联）", "正文提及": "正文提及",
            "行情名单": "行情播报点名", "观点来源": "机构观点的发布方", "中介": "作为中介机构出现"}[m.role]


class Ranker:
    def __init__(self, profile: Profile, weights: dict | None = None, learned: dict[str, float] | None = None,
                 book: Book | None = None, linker: Linker | None = None):
        self.p = profile
        self.w = weights or default_weights()
        self.learned = learned or {}
        self.book = book or default_book()
        self.linker = linker or Linker(self.book)
        self.rel_w = self.w.get("relevance") or {}
        self.role_w = self.w.get("role_weights") or {}
        # 关系库：关联公司代码 → [(持仓/自选代码, 关系)]；只用已确认的、只走一层
        self.related: dict[str, list[Relation]] = {}
        for r in profile.confirmed_relations():
            target = r.target if re.fullmatch(r"\d{6}", r.target) else next(
                (s.code for s in self.book.resolve(r.target)), "")
            if target and r.code in profile.codes:
                self.related.setdefault(target, []).append(r)
        self.industries = {self.book.get(c).industry: c for c in profile.codes
                           if self.book.get(c) and self.book.get(c).industry}
        # 画像里的别称（“迪王”）：只认带在画像里的这几只
        self.aliases = {a: c for c, names in profile.aliases.items() for a in names}

    def _name(self, code: str) -> str:
        s = self.book.get(code)
        return s.name if s else code

    def _pick(self, it: NewsItem, section: str, reason: str, kind: str, relevance: float, at: datetime,
              role_weight: float = 1.0, code: str = "", theme: str = "") -> Pick:
        return Pick(it, section, reason, kind, round(importance(it), 2), relevance, role_weight, freshness(it, at, self.w),
                    self.learned.get(kind, 1.0), code, theme)

    def _mentions(self, it: NewsItem) -> list[Mention]:
        out = self.linker.link(it)
        known = {m.code for m in out}
        title = it.title or ""
        for alias, code in self.aliases.items():
            if code not in known and alias in f"{title}{it.content}":
                out.append(Mention(code, self._name(code), alias, "主体" if alias in title else "正文提及", alias in title))
        return out

    def rank_one(self, it: NewsItem, at: datetime) -> Pick | None:
        """一条消息给这个人的最佳理由（没有关系就是 None）。"""
        if is_announcement(it):
            code = it.codes[0]
            t = type_of(it)
            if not t.whitelist:
                return None
            rel = self.p.relation_of(code)
            if rel:
                return self._pick(it, rel, f"{rel} {self._name(code)} · 公告（{t.name}）", f"{rel}·公告",
                                  self.rel_w.get(rel, 1.0), at, code=code)
            for r in self.related.get(code, []):
                return self._pick(it, "关联公司", f"{self.p.relation_of(r.code)} {self._name(r.code)} 的{r.kind} · "
                                  f"{self._name(code)} 公告（{t.name}）· 关系来源：{r.source}", f"关联公司·{r.kind}",
                                  self.rel_w.get("关联公司", 0.5), at, code=code)
            return None

        best: Pick | None = None

        def consider(p: Pick) -> None:
            nonlocal best
            if best is None or p.rank > best.rank:
                best = p

        for m in self._mentions(it):
            rel = self.p.relation_of(m.code)
            rw = float(self.role_w.get(m.role, 0.3))
            if rel:
                consider(self._pick(it, rel, f"{rel} {m.name} · {_role_label(m)}", f"{rel}·{m.role}",
                                    self.rel_w.get(rel, 1.0), at, rw, m.code))
            elif m.code in self.related and m.role in ("主体", "标题点名"):
                r = self.related[m.code][0]
                consider(self._pick(it, "关联公司", f"{self.p.relation_of(r.code)} {self._name(r.code)} 的{r.kind} · "
                                    f"{m.name}{_role_label(m)} · 关系来源：{r.source}", f"关联公司·{r.kind}",
                                    self.rel_w.get("关联公司", 0.5), at, rw, m.code))
            elif m.role == "主体" and self.book.get(m.code) and self.book.get(m.code).industry in self.industries:
                peer = self.industries[self.book.get(m.code).industry]
                consider(self._pick(it, "关联公司", f"{self.p.relation_of(peer)} {self._name(peer)} 同行业"
                                    f"（{self.book.get(m.code).industry}）· {m.name}新闻主体", "同行业",
                                    self.rel_w.get("同行业", 0.35), at, rw, m.code))
        themes = [t for t in item_themes(it) if t in self.p.themes]
        if themes:
            consider(self._pick(it, "关注题材", f"关注题材 {themes[0]}", "关注题材", self.rel_w.get("关注题材", 0.45), at,
                                theme=themes[0]))
        if best is None and it.category in MACRO_CATEGORIES:
            best = self._pick(it, "宏观", "宏观与政策（对所有人都一样）", "宏观", self.rel_w.get("宏观", 0.2), at)
        return best


def title_grams(it: NewsItem) -> set[str]:
    t = normalize(it.llm.get("title") or it.title)
    return {t[i:i + 2] for i in range(len(t) - 1)}


def similar(a: set[str], b: set[str], threshold: float = 0.6) -> bool:
    denom = min(len(a), len(b))
    return denom >= 4 and len(a & b) / denom >= threshold


def select(picks: list[Pick], w: dict | None = None) -> dict[str, list[Pick]]:
    """分栏，栏内按排序分；同一栏里字面很像的只留一条；低于 min_rank 的不要（宏观除外）。"""
    w = w or default_weights()
    caps = w.get("sections") or {}
    floor = float(w.get("min_rank", 0.0))
    out: dict[str, list[Pick]] = {s: [] for s in SECTIONS}
    per_theme: dict[str, int] = {}
    for p in sorted(picks, key=lambda p: (-p.rank, -p.item.published.timestamp())):
        if p.section != "宏观" and (p.rank < floor or is_weak(p, w)):
            continue
        sec = out[p.section]
        grams = title_grams(p.item)
        if any(similar(grams, title_grams(q.item)) for q in sec):
            continue
        if p.section == "关注题材":
            if per_theme.get(p.theme, 0) >= int(caps.get("关注题材", 3)):
                continue
            per_theme[p.theme] = per_theme.get(p.theme, 0) + 1
        elif len(sec) >= int(caps.get(p.section, 8)):
            continue
        sec.append(p)
    return out


def is_weak(p: Pick, w: dict | None = None) -> bool:
    """持仓 / 自选里只是顺带提到（正文提及、行情名单、机构观点……）的。"""
    w = w or default_weights()
    role = p.reason_kind.split("·", 1)[1] if "·" in p.reason_kind else ""
    return p.section in ("持仓", "自选") and role in set(w.get("weak_roles") or [])


def weak_mentions(picks: list[Pick], w: dict | None = None) -> dict[str, list[Pick]]:
    """持仓 / 自选里顺带提到的，按栏目分，同一条只算一次。"""
    out: dict[str, list[Pick]] = {"持仓": [], "自选": []}
    seen: set[str] = set()
    for p in sorted(picks, key=lambda p: -p.rank):
        if is_weak(p, w) and p.id not in seen:
            seen.add(p.id)
            out[p.section].append(p)
    return out


def push_worthy(p: Pick, w: dict | None = None) -> bool:
    """即时提醒：持仓 / 自选的公告看类型表的 push；新闻看排序分。"""
    w = w or default_weights()
    if p.section not in ("持仓", "自选") or is_weak(p, w):
        return False
    if is_announcement(p.item):
        return type_of(p.item).push
    return p.rank >= float((w.get("push") or {}).get("min_rank", 5.0))


def sigmoid_learned(useful: int, irrelevant: int, bounds=(0.5, 1.5)) -> float:
    """按反馈算的修正系数：有用多就 >1，不相关多就 <1（拉普拉斯平滑，限制在 bounds 内）。"""
    rate = (useful + 1) / (useful + irrelevant + 2)
    lo, hi = bounds
    return round(lo + (hi - lo) / (1 + math.exp(-6 * (rate - 0.5))), 3)
