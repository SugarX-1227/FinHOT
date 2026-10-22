"""让模型起草一个新题材的知识层（topics/<名称>.yaml），人工校对后再用。

  python -m finhot topic draft 低空经济 [--universe 低空经济概念股]

有问财 key 时先取成分股主营和新闻研报作素材（约 3 次调用），没有就只用 FinHOT 本地快讯。
起草结果一律 reviewed: false，历史行情 verified: false，档案卡顶部会提示“未校对”。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

from ..config import DATA_DIR, PROMPTS_DIR
from ..timeutil import now
from ..wencai import STOCKS, Wencai, WencaiError
from . import local
from .fetch import Q_FUND, parse_members
from .kb import TOPICS_DIR, Topic, validate


def _context(name: str, universe: str, client: Wencai | None, data_dir: Path) -> dict:
    ctx: dict = {"members": [], "news": [], "reports": [], "finhot_news": []}
    probe = Topic(Path(f"{name}.yaml"), {"name": name, "local": {"keywords": re.escape(name)}})
    ctx["finhot_news"] = [f"{n.published[:10]} {n.title}" for n in local.news(probe, data_dir, days=30, min_score=6)][-30:]
    if client:
        try:
            ms = parse_members(None, client.query(Q_FUND.format(u=universe), STOCKS, 60, 1))
            ctx["members"] = [{"code": m.code, "name": m.name, "mcap_yi": m.mcap, "products": m.products[:60]}
                              for m in ms]
            ctx["news"] = [f"{h.published[:10]} {h.title}" for h in client.search(name, "news", 15)]
            ctx["reports"] = [f"{h.published[:10]} {h.title}" for h in client.search(name, "report", 10)]
        except WencaiError as e:
            ctx["wencai_error"] = str(e)
    return ctx


def draft(name: str, llm, client: Wencai | None = None, universe: str | None = None,
          data_dir: Path = DATA_DIR, topics_dir: Path = TOPICS_DIR, force: bool = False) -> Path:
    path = topics_dir / f"{name}.yaml"
    if path.exists() and not force:
        raise SystemExit(f"{path} 已存在（覆盖加 --force）")
    payload = {
        "name": name,
        "today": now().date().isoformat(),
        "example": (topics_dir / "机器人.yaml").read_text(encoding="utf-8"),
        "schema": (topics_dir / "README.md").read_text(encoding="utf-8"),
        **_context(name, universe or f"{name}概念股", client, data_dir),
    }
    system = (PROMPTS_DIR / "topic_draft.md").read_text(encoding="utf-8")
    text = llm.chat(system, json.dumps(payload, ensure_ascii=False), max_tokens=8000)
    text = re.sub(r"^```(?:yaml)?\s*|```\s*$", "", text.strip(), flags=re.M)
    data = yaml.safe_load(text) or {}
    data["name"], data["reviewed"] = name, False
    for h in data.get("history") or []:
        h["verified"] = False
    errs = validate(data)
    if errs:
        bad = topics_dir / f"{name}.draft-invalid.yaml"
        bad.write_text(text, encoding="utf-8")
        raise SystemExit(f"模型起草的 YAML 不完整（{'；'.join(errs)}），原文存在 {bad}")
    header = "# 模型起草，未校对。校对后把 reviewed 改成 true。字段说明见 topics/README.md。\n"
    path.write_text(header + yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=120), encoding="utf-8")
    return path
