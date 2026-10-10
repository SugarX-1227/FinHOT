"""跨信源去重/聚簇。

同一事件往往被 5~8 家快讯同时报道。做法：
1. 规范化正文前 N 个字（去掉"财联社X月X日电"等前缀、标点）；
2. 中文取字符 2-gram、英文取单词作为特征集合；
3. "领头条目"聚类：按时间顺序遍历，与已有簇的领头条目足够相似（重叠系数 ≥ 阈值、时间相近）就并入，
   否则自成一簇。只和领头条目比较，避免 A~B~C 链式传递把不相关的新闻串成一个大簇；
4. 每簇选一条代表（信源标记重要 > 官方 > 信源权重高 > 正文更长），记录其余信源。
"""

from __future__ import annotations

import re
from collections import defaultdict

from .models import NewsItem
from .textutil import normalize

WINDOW_CHARS = 80
THRESHOLD = 0.55
MAX_HOURS = 12
MAX_DF = 200  # 出现过于频繁的特征不参与找候选


def _features(it: NewsItem) -> set[str]:
    if it.lang == "en":
        words = re.findall(r"[a-z0-9]+", it.title.lower())
        return {w for w in words if len(w) > 2}
    text = normalize(f"{it.title}{it.content}")[:WINDOW_CHARS]
    return {text[i:i + 2] for i in range(len(text) - 1)}


def _similar(a: set[str], b: set[str], threshold: float) -> bool:
    denom = min(len(a), len(b))
    return denom >= 4 and len(a & b) / denom >= threshold


_NUM_RE = re.compile(r"\d+(?:\.\d+)?")


def _numbers_compatible(a: str, b: str) -> bool:
    """两条标题都带多个数字、且数字集合差异很大时，视为不同事件。

    防止模板化快讯被误合并，如"美国9月CPI 公布值:3.6 预期:3.5"与"美国9月核心CPI 公布值:3.1 预期:3.0"。
    只有一边有数字（如"1.2万亿"与"12000亿"写法不同）时不判断。
    """
    na, nb = set(_NUM_RE.findall(a)), set(_NUM_RE.findall(b))
    if len(na) < 2 or len(nb) < 2:
        return True
    return len(na & nb) / len(na | nb) >= 0.5


def _rep_key(it: NewsItem):
    return (it.important, it.tier == "official", it.weight, len(it.content))


def dedup(items: list[NewsItem], threshold: float = THRESHOLD) -> list[NewsItem]:
    # 先按 uid 精确去重（多次运行合并时会有完全相同的条目）
    uniq: dict[str, NewsItem] = {}
    for it in items:
        uniq.setdefault(it.uid, it)
    items = sorted(uniq.values(), key=lambda x: x.published)

    feats = [_features(it) for it in items]
    df: dict[str, int] = defaultdict(int)
    for fs in feats:
        for f in fs:
            df[f] += 1

    leaders: list[int] = []                       # 每个簇的领头条目下标
    members: dict[int, list[int]] = {}
    index: dict[str, list[int]] = defaultdict(list)  # 特征 → 领头条目

    for i, fs in enumerate(feats):
        best, best_sim = -1, 0.0
        if len(fs) >= 4:
            cand: dict[int, int] = defaultdict(int)
            for f in fs:
                if df[f] <= MAX_DF:
                    for L in index[f]:
                        cand[L] += 1
            for L, c in cand.items():
                if c < 2:
                    continue
                if (items[i].published - items[L].published).total_seconds() > MAX_HOURS * 3600:
                    continue
                if _similar(fs, feats[L], threshold) and _numbers_compatible(items[i].title, items[L].title):
                    sim = len(fs & feats[L]) / min(len(fs), len(feats[L]))
                    if sim > best_sim:
                        best, best_sim = L, sim
        if best >= 0:
            members[best].append(i)
        else:
            leaders.append(i)
            members[i] = [i]
            for f in fs:
                if df[f] <= MAX_DF:
                    index[f].append(i)

    out = []
    for L in leaders:
        group = [items[k] for k in members[L]]
        rep = max(group, key=_rep_key)
        others = [m for m in group if m is not rep]
        # 再次去重（如周报合并多天数据）时保留各条已有的聚簇信息
        rep.dup_count = sum(max(1, m.dup_count) for m in group)
        prior = {s for m in group for s in m.dup_sources}
        rep.dup_sources = sorted(({m.source_name for m in others} | prior) - {rep.source_name})
        imp = {m.source: m.imp_weight for m in group if m.important}
        rep.imp_votes = round(max(sum(imp.values()), max(m.imp_votes for m in group)), 2)
        rep.important = rep.imp_votes > 0
        rep.published = min(m.published for m in group)  # 取最早报道时间
        rep.tags = list(dict.fromkeys(t for m in group for t in m.tags))[:6]
        rep.stocks = list(dict.fromkeys(s for m in group for s in m.stocks))[:8]
        if any(m.tier == "official" for m in group):
            rep.tier = "official"
        rep.imp_sources = sorted(set(imp) | {s for m in group for s in m.imp_sources})
        out.append(rep)
    out.sort(key=lambda x: x.published, reverse=True)
    return out
