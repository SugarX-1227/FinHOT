"""跨信源去重/聚簇。

同一事件往往被 5~8 家快讯同时报道。做法：
1. 规范化正文前 N 个字（去掉"财联社X月X日电"等前缀、标点）；
2. 中文取字符 2-gram、英文取单词作为特征集合；
3. 用倒排索引找候选对，重叠系数 |A∩B| / min(|A|,|B|) ≥ 阈值且发布时间相近 → 合并为一簇；
4. 每簇选一条代表（信源标记重要 > 信源权重高 > 正文更长），记录其余信源。
"""

from __future__ import annotations

import re
from collections import defaultdict

from .models import NewsItem
from .textutil import normalize

WINDOW_CHARS = 80
THRESHOLD = 0.55
MAX_HOURS = 12
MAX_DF = 150  # 出现过于频繁的特征不参与找候选


def _features(it: NewsItem) -> set[str]:
    if it.lang == "en":
        words = re.findall(r"[a-z0-9]+", f"{it.title}".lower())
        return {w for w in words if len(w) > 2}
    text = normalize(f"{it.title}{it.content}")[:WINDOW_CHARS]
    return {text[i:i + 2] for i in range(len(text) - 1)}


class _UF:
    def __init__(self, n: int):
        self.p = list(range(n))

    def find(self, x: int) -> int:
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[max(ra, rb)] = min(ra, rb)


def _rep_key(it: NewsItem):
    return (it.important, it.tier == "official", it.weight, len(it.content))


def dedup(items: list[NewsItem], threshold: float = THRESHOLD) -> list[NewsItem]:
    # 先按 uid 精确去重（多次运行合并时会有完全相同的条目）
    uniq: dict[str, NewsItem] = {}
    for it in items:
        uniq.setdefault(it.uid, it)
    items = list(uniq.values())

    feats = [_features(it) for it in items]
    index: dict[str, list[int]] = defaultdict(list)
    for i, fs in enumerate(feats):
        for f in fs:
            index[f].append(i)

    uf = _UF(len(items))
    for i, fs in enumerate(feats):
        if len(fs) < 4:
            continue
        shared: dict[int, int] = defaultdict(int)
        for f in fs:
            posting = index[f]
            if len(posting) > MAX_DF:
                continue
            for j in posting:
                if j > i:
                    shared[j] += 1
        for j, c in shared.items():
            denom = min(len(fs), len(feats[j]))
            if c < 2 or denom < 4:
                continue
            # 倒排阶段跳过了高频特征，这里用完整集合计算重叠系数
            if len(fs & feats[j]) / denom < threshold:
                continue
            if abs((items[i].published - items[j].published).total_seconds()) > MAX_HOURS * 3600:
                continue
            uf.union(i, j)

    clusters: dict[int, list[NewsItem]] = defaultdict(list)
    for i, it in enumerate(items):
        clusters[uf.find(i)].append(it)

    out = []
    for members in clusters.values():
        rep = max(members, key=_rep_key)
        others = [m for m in members if m is not rep]
        rep.dup_count = len(members)
        rep.dup_sources = sorted({m.source_name for m in others} - {rep.source_name})
        rep.important = any(m.important for m in members)
        rep.published = min(m.published for m in members)  # 取最早报道时间
        rep.tags = list(dict.fromkeys(t for m in members for t in m.tags))[:6]
        rep.stocks = list(dict.fromkeys(s for m in members for s in m.stocks))[:8]
        if any(m.tier == "official" for m in members):
            rep.tier = "official"
        out.append(rep)
    out.sort(key=lambda x: x.published, reverse=True)
    return out
