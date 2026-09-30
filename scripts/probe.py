"""临时分析脚本：统计各源"重要"标记比例，打印排名，便于校准打分规则。"""
import collections, logging
from datetime import timedelta
from finhot.collect import collect
from finhot.config import load_sources
from finhot.dedup import dedup
from finhot.scoring import score_all
from finhot.timeutil import now

logging.basicConfig(level=logging.WARNING)
res = collect(load_sources()["sources"], now() - timedelta(hours=12))
raw = [it for r in res for it in r.items]
for r in res:
    imp = sum(it.important for it in r.items)
    print(f"{r.source_id:14} n={len(r.items):4} important={imp:4} ({imp / max(1, len(r.items)):.0%})")
items = score_all(dedup(raw))
print("clusters", len(items), "dup>1", sum(it.dup_count > 1 for it in items), "important", sum(it.important for it in items))
print("dup_count hist", sorted(collections.Counter(min(it.dup_count, 8) for it in items).items()))
print("cat hist", collections.Counter(it.category for it in items).most_common())
print("score hist", sorted(collections.Counter(int(it.score) for it in items).items()))
for it in items[:120]:
    print(f"{it.score:4.1f} {'!' if it.important else ' '} d{it.dup_count} [{it.category}] {it.source}: {it.title[:70]}")
print("---- random mid (score 4-6) ----")
for it in [x for x in items if 4 <= x.score < 6][:60]:
    print(f"{it.score:4.1f} {'!' if it.important else ' '} d{it.dup_count} [{it.category}] {it.source}: {it.title[:70]}")
