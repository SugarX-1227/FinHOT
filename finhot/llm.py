"""可选的 LLM 增强：对高分候选条目做打分/摘要/A股板块映射，并生成盘前要点。

任何 OpenAI 兼容接口都可以用（DeepSeek、通义千问、Kimi、OpenAI…），通过环境变量配置：
  LLM_API_KEY    必填，不填则整个 LLM 步骤跳过，日报退化为规则版
  LLM_BASE_URL   默认 https://api.deepseek.com/v1
  LLM_MODEL      默认 deepseek-chat
  LLM_MAX_ITEMS  进入 LLM 的最大条目数（预算熔断），默认 80
  LLM_MAX_CALLS  单次运行最多调用次数（预算熔断），默认 10
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

import requests

from .config import PROMPTS_DIR, env
from .models import NewsItem
from .scoring import CATEGORIES

log = logging.getLogger(__name__)
BATCH = 15


@dataclass
class LLMClient:
    api_key: str
    base_url: str = "https://api.deepseek.com/v1"
    model: str = "deepseek-chat"
    max_calls: int = 10
    timeout: int = 120
    calls: int = 0
    usage: dict = field(default_factory=lambda: {"prompt_tokens": 0, "completion_tokens": 0})

    @classmethod
    def from_env(cls) -> "LLMClient | None":
        key = env("LLM_API_KEY")
        if not key:
            return None
        return cls(
            api_key=key,
            base_url=env("LLM_BASE_URL", "https://api.deepseek.com/v1").rstrip("/"),
            model=env("LLM_MODEL", "deepseek-chat"),
            max_calls=int(env("LLM_MAX_CALLS", "10")),
        )

    def chat(self, system: str, user: str, json_mode: bool = False, max_tokens: int = 4000) -> str:
        if self.calls >= self.max_calls:
            raise RuntimeError("LLM 调用次数达到预算上限")
        self.calls += 1
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": 0.2,
            "max_tokens": max_tokens,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        r = requests.post(f"{self.base_url}/chat/completions", json=body, timeout=self.timeout,
                          headers={"Authorization": f"Bearer {self.api_key}"})
        if r.status_code >= 400 and json_mode:
            # 部分兼容接口不支持 response_format，去掉重试一次
            body.pop("response_format", None)
            r = requests.post(f"{self.base_url}/chat/completions", json=body, timeout=self.timeout,
                              headers={"Authorization": f"Bearer {self.api_key}"})
        r.raise_for_status()
        data = r.json()
        for k in self.usage:
            self.usage[k] += int((data.get("usage") or {}).get(k, 0))
        return data["choices"][0]["message"]["content"]


def _load_prompt(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


def _parse_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
    try:
        return json.loads(text)
    except ValueError:
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            raise
        return json.loads(m.group(0))


def _apply(it: NewsItem, r: dict) -> None:
    src_text = f"{it.title}{it.content}"
    try:
        llm_score = max(0.0, min(10.0, float(r.get("score", it.score))))
    except (TypeError, ValueError):
        llm_score = it.score
    # 防幻觉：个股必须在原文中出现
    stocks = [s for s in (r.get("stocks") or []) if isinstance(s, str) and s and s in src_text][:3]
    it.llm = {
        "keep": bool(r.get("keep", True)),
        "title": str(r.get("title") or "").strip(),
        "summary": str(r.get("summary") or "").strip(),
        "direction": r.get("direction") if r.get("direction") in ("利好", "利空", "中性") else "中性",
        "sectors": [str(s) for s in (r.get("sectors") or [])][:3],
        "stocks": stocks,
        "horizon": str(r.get("horizon") or ""),
        "basis": str(r.get("basis") or "").strip(),
        "score": llm_score,
    }
    if r.get("category") in CATEGORIES:
        it.category = r["category"]
    it.score = round(0.35 * it.score + 0.65 * llm_score, 2)
    if not it.llm["keep"]:
        it.score = round(it.score * 0.3, 2)


def enrich(items: list[NewsItem], client: LLMClient, max_items: int | None = None) -> dict:
    """对排名靠前的条目做 LLM 分析，原地修改 items。返回运行统计。"""
    max_items = max_items or int(env("LLM_MAX_ITEMS", "80"))
    cands = items[:max_items]
    system = _load_prompt("analyze.md")
    done = failed = 0
    for i in range(0, len(cands), BATCH):
        batch = cands[i:i + BATCH]
        payload = [
            {"id": str(k), "title": it.title, "content": it.content[:400],
             "sources": [it.source_name, *it.dup_sources]}
            for k, it in enumerate(batch)
        ]
        try:
            out = _parse_json(client.chat(system, json.dumps(payload, ensure_ascii=False), json_mode=True))
            by_id = {str(r.get("id")): r for r in out.get("items", []) if isinstance(r, dict)}
            for k, it in enumerate(batch):
                if str(k) in by_id:
                    _apply(it, by_id[str(k)])
                    done += 1
        except Exception as e:  # noqa: BLE001 —— LLM 失败不影响日报生成
            failed += len(batch)
            log.warning("LLM 分析批次失败（%d 条保留规则打分）: %s", len(batch), e)
            if "预算上限" in str(e):
                break
    items.sort(key=lambda x: (x.score, x.published), reverse=True)
    return {"analyzed": done, "failed": failed}


def overview(items: list[NewsItem], market_rows: list[dict], client: LLMClient, top: int = 25) -> str:
    payload = {
        "news": [
            {"title": it.llm.get("title") or it.title,
             "summary": it.llm.get("summary") or it.content[:150],
             "category": it.category, "direction": it.llm.get("direction", ""),
             "sectors": it.llm.get("sectors", [])}
            for it in items[:top]
        ],
        "markets": [{"name": m["name"], "pct": m.get("pct")} for m in market_rows],
    }
    try:
        text = client.chat(_load_prompt("overview.md"), json.dumps(payload, ensure_ascii=False), max_tokens=800)
    except Exception as e:  # noqa: BLE001
        log.warning("LLM 盘前要点生成失败: %s", e)
        return ""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip().startswith(("-", "•", "*"))]
    return "\n".join("- " + ln.lstrip("-•* ").strip() for ln in lines[:6])
