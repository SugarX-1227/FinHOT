"""金标样本：从 data/ 里抽一批快讯，生成标注页，导出网站引擎评测用的 gold.jsonl。

  python -m finhot gold --n 200          # 抽样，写 data/gold/candidates.jsonl 和 data/gold/label.html
  python -m finhot gold --labels x.json  # 把标注结果（{caseId: "select"|"reject"|"either"}）合成 gold.jsonl

标注页可以直接用浏览器打开（结果存在这台电脑的浏览器里），也可以发布成 claude.ai 的页面（结果存在页面的数据库里）。
两种方式都能导出 gold.jsonl，交给 web/scripts/eval-selection.ts 校准精选门槛（见 web/docs/selection.md）。

抽样原则（web/docs/selection.md 的建议）：多放难例。按日报规则分分档，门槛附近（推送门槛 6 分上下）抽得最多；
每档里按类别轮流抽，避免全是同一类；同一件事在几期数据里重复出现的只留一条。
"""

from __future__ import annotations

import json
import random
from collections import defaultdict
from dataclasses import replace
from pathlib import Path
from typing import Any

from .config import DATA_DIR, ROOT
from .dedup import clusters, merge
from .models import NewsItem
from .timeutil import TZ

GOLD_DIR = DATA_DIR / "gold"
TEMPLATE = Path(__file__).with_name("gold_label.html")
ENGINE_SOURCES = ROOT / "web" / "industry" / "sources.json"

# (下限, 上限, 占比)：门槛附近最多，明显该选的和明显不够格的少一些
BANDS = [(0.0, 5.0, 0.15), (5.0, 6.0, 0.25), (6.0, 7.0, 0.30), (7.0, 8.0, 0.18), (8.0, 99.0, 0.12)]
HOLDOUT = 0.25


def load_pool(data_dir: Path = DATA_DIR) -> list[NewsItem]:
    """data/ 里各期保存的条目，同一件事只留一条（保留最高的规则分）。"""
    items: list[NewsItem] = []
    for path in sorted(data_dir.glob("*/items-*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                items.append(NewsItem.from_dict(json.loads(line)))
    out = []
    for group in clusters([replace(it) for it in items]):
        best = max(group, key=lambda m: m.score)
        rep = merge(group)
        rep.score = best.score
        out.append(rep)
    return out


def sample(pool: list[NewsItem], n: int = 200, seed: int = 20261010) -> list[NewsItem]:
    rng = random.Random(seed)
    picked: list[NewsItem] = []
    for lo, hi, share in BANDS:
        band = [it for it in pool if lo <= it.score < hi and it.content]
        by_cat: dict[str, list[NewsItem]] = defaultdict(list)
        for it in band:
            by_cat[it.category or "其他"].append(it)
        for v in by_cat.values():
            rng.shuffle(v)
        want = round(n * share)
        cats = sorted(by_cat)
        while want > 0 and any(by_cat.values()):
            for c in cats:
                if want > 0 and by_cat[c]:
                    picked.append(by_cat[c].pop())
                    want -= 1
    # 某档不够时从剩下的里补齐
    rest = [it for it in pool if it.content and it not in picked]
    rng.shuffle(rest)
    picked += rest[: max(0, n - len(picked))]
    picked = picked[:n]
    rng.shuffle(picked)
    return picked


def _engine_sources(path: Path = ENGINE_SOURCES) -> dict[str, dict[str, Any]]:
    try:
        return {s["id"]: s for s in json.loads(path.read_text(encoding="utf-8"))["sources"]}
    except (OSError, ValueError, KeyError):
        return {}


def to_case(it: NewsItem, split: str, engine: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """引擎 gold.jsonl 的一行（还没有 gold 字段）。信源分级按引擎里的配置：快讯源 T2，政府网 / 美联储 T1。"""
    src = engine.get(it.source) or engine.get(f"rss-{it.source}") or {}
    tier = src.get("tier") or ("T1" if it.tier == "official" else "T2")
    return {
        "caseId": f"finhot-{it.uid}",
        "material": {
            "title": it.title,
            "originalTitle": None,
            "publishedAt": it.published.astimezone(TZ).isoformat(timespec="seconds"),
            "sourceName": it.source_name,
            "bodyZh": None,
            "bodyOriginal": it.content,
        },
        "sourceFacts": {
            "sourceKind": src.get("kind") or "external",
            "sourceTier": tier,
            "firstParty": tier == "T1",
            "language": it.lang,
        },
        "samplingContext": {"benchmarkSplit": split, "samplingStratum": it.category or "其他"},
    }


def build_cases(items: list[NewsItem], seed: int = 20261010) -> list[dict[str, Any]]:
    rng = random.Random(seed + 1)
    engine = _engine_sources()
    holdout = set(rng.sample(range(len(items)), round(len(items) * HOLDOUT)))
    return [to_case(it, "holdout" if i in holdout else "development", engine) for i, it in enumerate(items)]


def render_page(cases: list[dict[str, Any]], template: Path = TEMPLATE, standalone: bool = True) -> str:
    """标注页。standalone 时补上完整的 HTML 外壳，可以直接用浏览器打开；发布到 claude.ai 时不要外壳（平台会加）。"""
    data = json.dumps(cases, ensure_ascii=False).replace("</", "<\\/")
    page = template.read_text(encoding="utf-8")
    assert "/*__CASES__*/[]" in page
    page = page.replace("/*__CASES__*/[]", data)
    if not standalone:
        return page
    return ('<!doctype html>\n<html lang="zh-CN"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
            "<style>body{margin:0}[hidden]{display:none!important}</style></head><body>\n"
            f"{page}\n</body></html>\n")


def with_labels(cases: list[dict[str, Any]], labels: dict[str, str]) -> list[dict[str, Any]]:
    """把标注合进样本；没标的、标注值不认识的跳过。"""
    out = []
    for c in cases:
        decision = labels.get(c["caseId"])
        if decision in ("select", "reject", "either"):
            out.append({**c, "gold": {"decision": decision}})
    return out


def write_jsonl(rows: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
