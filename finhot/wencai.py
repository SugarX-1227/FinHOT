"""问财 OpenAPI（同花顺 SkillHub）客户端。

环境变量（和问财官方技能的约定一致）：
  IWENCAI_API_KEY      必填。登录 https://www.iwencai.com/skillhub ，点任意一个官方技能卡片就能看到
  IWENCAI_BASE_URL     默认 https://openapi.iwencai.com
  IWENCAI_MAX_CALLS    单次运行最多真实调用多少次（预算熔断，命中缓存不算），默认 80
  IWENCAI_CACHE_HOURS  同一问句多少小时内直接用缓存，默认 12（同一个交易日盘后反复跑不重复花额度）
  IWENCAI_SKILL_VERSION / IWENCAI_SEARCH_SKILL_VERSION  技能版本头，默认 1.0.0 / 1.0.1（检索类低于 1.0.1 会被拒）

两类接口（请求头带 X-Claw-* 协议头，服务端会校验技能 id 和版本）：
  POST /v1/query2data            结构化数据：选股、选板块。返回 columns + datas + code_count（总条数）
  POST /v1/comprehensive/search  文本检索：新闻 news / 公告 announcement / 研报 report

问财的列名带日期，如“总市值[20261021]”“区间涨跌幅:前复权[20260923-20261021]”，所以取列一律按关键词匹配
（Table.col），不写死列名。每次真实调用记一行到 data/topics/wencai-usage.jsonl，`python -m finhot topic usage`
汇总，用来摸清自己的额度。密钥只从环境变量读，不写日志、不进缓存文件。
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

import requests

from .config import DATA_DIR, env
from .timeutil import TZ, now

log = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://openapi.iwencai.com"
STOCKS = "hithink-astock-selector"     # 问财选A股
SECTORS = "hithink-sector-selector"    # 问财选板块
INDEX = "hithink-index-query"          # 指数数据查询
SEARCH_SKILLS = {"news": "news-search", "announcement": "announcement-search", "report": "report-search"}
QUOTA_WORDS = re.compile(r"额度|次数|余额|quota|limit exceeded|rate limit|上限|频繁", re.I)
DATE_RANGE = re.compile(r"\[(\d{8})-(\d{8})\]")


class WencaiError(RuntimeError):
    """单个问句失败（不影响其他问句）。"""


class WencaiFatal(WencaiError):
    """继续调用也没用：密钥无效、额度用完、预算熔断。调用方应停止本次运行里的所有问财请求。"""


# ---------------------------------------------------------------- 解析工具

def num(v: Any) -> float | None:
    """问财的数值可能是数字、字符串、带 % / 亿 / 万 的字符串，或 '--'。"""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(",", "")
    if not s or s in {"-", "--", "None", "null", "nan"}:
        return None
    mult = 1.0
    if s.endswith("%"):
        s = s[:-1]
    if s.endswith("亿"):
        s, mult = s[:-1], 1e8
    elif s.endswith("万"):
        s, mult = s[:-1], 1e4
    try:
        return float(s) * mult
    except ValueError:
        return None


def code6(v: Any) -> str:
    """'300024.SZ' / 'SZ300024' / 300024 → '300024'；港股 '09880.HK' → '09880'。取不到返回原值。"""
    s = str(v or "")
    m = re.search(r"\d{6}", s) or re.search(r"\d{4,5}", s)
    return m.group(0) if m else s


def base_name(col: str) -> str:
    """去掉列名里的日期与口径：'区间涨跌幅:前复权[20260923-20261021]' → '区间涨跌幅'。"""
    return re.split(r"[\[:(（]", col, maxsplit=1)[0].strip()


def span_days(col: str) -> int | None:
    m = DATE_RANGE.search(col)
    if not m:
        return None
    a, b = (datetime.strptime(x, "%Y%m%d") for x in m.groups())
    return (b - a).days


@dataclass
class Table:
    query: str
    columns: list[str] = field(default_factory=list)
    rows: list[dict] = field(default_factory=list)
    total: int = 0          # code_count：符合条件的总条数（翻页前）
    cached: bool = False

    def col(self, *keys: str, exclude: tuple[str, ...] = ()) -> str | None:
        """第一个同时包含全部关键词、且不含排除词的列名。"""
        for c in self.columns:
            if all(k in c for k in keys) and not any(x in c for x in exclude):
                return c
        return None

    def range_cols(self, *keys: str) -> list[str]:
        """带日期区间的列，按区间从短到长排（同一问句里要了 20 日和 60 日涨幅时用来区分）。"""
        cols = [c for c in self.columns if all(k in c for k in keys) and span_days(c) is not None]
        return sorted(cols, key=lambda c: span_days(c) or 0)

    def merge(self, other: "Table") -> None:
        for c in other.columns:
            if c not in self.columns:
                self.columns.append(c)
        self.rows.extend(other.rows)
        self.cached = self.cached and other.cached


@dataclass
class Hit:
    title: str
    summary: str
    url: str
    published: str
    channel: str


def parse_table(query: str, payload: dict) -> Table:
    cols = []
    for c in payload.get("columns") or []:
        if isinstance(c, dict):
            name = str(c.get("key") or c.get("index_name") or "").strip()
            if name:
                cols.append(name)
        elif isinstance(c, str):
            cols.append(c)
    rows = [r for r in (payload.get("datas") or []) if isinstance(r, dict)]
    for r in rows:  # 有的返回没有 columns，从数据里补
        for k in r:
            if k not in cols:
                cols.append(k)
    total = int(num(payload.get("code_count")) or len(rows))
    return Table(query, cols, rows, total)


def parse_hits(payload: dict, channel: str) -> list[Hit]:
    records = payload.get("data") or []
    if isinstance(records, dict):
        records = records.get("list") or records.get("records") or records.get("data") or []
    hits = []
    for r in records:
        if not isinstance(r, dict):
            continue
        title = str(r.get("title") or r.get("name") or "").strip()
        if not title:
            continue
        hits.append(Hit(
            title=re.sub(r"</?em>", "", title),
            summary=re.sub(r"\s+", " ", str(r.get("summary") or r.get("content") or ""))[:300],
            url=str(r.get("url") or ""),
            published=str(r.get("publish_time") or r.get("time") or r.get("date") or "")[:19],
            channel=str(r.get("channel") or channel),
        ))
    return hits


# ---------------------------------------------------------------- 客户端

@dataclass
class Wencai:
    api_key: str
    base_url: str = DEFAULT_BASE_URL
    max_calls: int = 80
    cache_hours: float = 12
    data_dir: Path = DATA_DIR
    query_version: str = "1.0.0"
    search_version: str = "1.0.1"
    timeout: float = 30
    session: requests.Session | None = None
    sleep: Callable[[float], None] = time.sleep
    calls: int = 0
    cache_hits: int = 0
    auth_failures: int = 0

    def __post_init__(self) -> None:
        if self.session is None:
            self.session = requests.Session()
            # 本机挂了代理软件时，国内接口走代理反而超时（和 LLM_TRUST_ENV 同一个开关）
            if env("IWENCAI_TRUST_ENV", "1") == "0":
                self.session.trust_env = False

    @classmethod
    def from_env(cls, data_dir: Path = DATA_DIR) -> "Wencai | None":
        key = env("IWENCAI_API_KEY")
        if not key:
            return None
        return cls(
            api_key=key,
            base_url=env("IWENCAI_BASE_URL", DEFAULT_BASE_URL).rstrip("/"),
            max_calls=int(env("IWENCAI_MAX_CALLS", "80")),
            cache_hours=float(env("IWENCAI_CACHE_HOURS", "12")),
            data_dir=data_dir,
            query_version=env("IWENCAI_SKILL_VERSION", "1.0.0"),
            search_version=env("IWENCAI_SEARCH_SKILL_VERSION", "1.0.1"),
        )

    # ---- 对外接口

    def query(self, query: str, skill: str = STOCKS, limit: int = 50, page: int = 1) -> Table:
        body = {"query": query, "page": str(page), "limit": str(limit), "is_cache": "1", "expand_index": "true"}
        payload, cached = self._post("/v1/query2data", body, skill, self.query_version)
        t = parse_table(query, payload)
        t.cached = cached
        return t

    def query_all(self, query: str, skill: str = STOCKS, limit: int = 100, max_pages: int = 3) -> Table:
        """翻页取全（最多 max_pages 页）。"""
        t = self.query(query, skill, limit, 1)
        page = 1
        while len(t.rows) < t.total and page < max_pages:
            page += 1
            nxt = self.query(query, skill, limit, page)
            if not nxt.rows:
                break
            t.merge(nxt)
        return t

    def count(self, query: str, skill: str = STOCKS) -> int:
        """只要总条数（code_count），每页 1 条，最省。"""
        return self.query(query, skill, limit=1).total

    def search(self, query: str, channel: str = "news", size: int = 10) -> list[Hit]:
        body = {"query": query, "channels": [channel], "app_id": "AIME_SKILL", "size": str(size)}
        payload, _ = self._post("/v1/comprehensive/search", body, SEARCH_SKILLS[channel], self.search_version)
        return parse_hits(payload, channel)

    # ---- 内部

    def _cache_file(self, path: str, body: dict, skill: str) -> Path:
        key = hashlib.sha1(json.dumps([path, skill, body], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        return self.data_dir / "topics" / "cache" / f"{key[:20]}.json"

    def _headers(self, skill: str, version: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "X-Claw-Call-Type": "normal",
            "X-Claw-Skill-Id": skill,
            "X-Claw-Skill-Version": version,
            "X-Claw-Plugin-Id": "none",
            "X-Claw-Plugin-Version": "none",
            "X-Claw-Trace-Id": secrets.token_hex(32),
        }

    def _ledger(self, skill: str, query: str, status: str, rows: int, ms: int) -> None:
        path = self.data_dir / "topics" / "wencai-usage.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"at": now().isoformat(timespec="seconds"), "skill": skill, "query": query[:120],
                                "status": status, "rows": rows, "ms": ms}, ensure_ascii=False) + "\n")

    def _post(self, path: str, body: dict, skill: str, version: str) -> tuple[dict, bool]:
        cache = self._cache_file(path, body, skill)
        if cache.exists():
            try:
                saved = json.loads(cache.read_text(encoding="utf-8"))
                if now() - datetime.fromisoformat(saved["at"]) < timedelta(hours=self.cache_hours):
                    self.cache_hits += 1
                    return saved["payload"], True
            except (ValueError, KeyError):
                pass
        if self.calls >= self.max_calls:
            raise WencaiFatal(f"问财调用达到本次预算上限 {self.max_calls} 次（IWENCAI_MAX_CALLS）")
        query = str(body.get("query", ""))
        last = ""
        for attempt in range(3):
            self.calls += 1
            t0 = time.monotonic()
            try:
                r = self.session.post(self.base_url + path, json=body, headers=self._headers(skill, version),
                                      timeout=self.timeout)
            except requests.RequestException as e:
                last = f"网络错误 {type(e).__name__}"
                self._ledger(skill, query, last, 0, int((time.monotonic() - t0) * 1000))
                self.sleep(2 * (attempt + 1))
                continue
            ms = int((time.monotonic() - t0) * 1000)
            text = r.text[:300]
            if r.status_code in (401, 403):
                self._ledger(skill, query, f"HTTP {r.status_code}", 0, ms)
                self.auth_failures += 1
                if QUOTA_WORDS.search(text):
                    raise WencaiFatal(f"问财额度不足或被限流：{text}")
                if self.auth_failures >= 3:
                    raise WencaiFatal(f"问财连续 {self.auth_failures} 次拒绝访问（密钥无效或技能未开通）：{text}")
                raise WencaiError(f"[{skill}] HTTP {r.status_code}：{text}")
            if r.status_code == 429 or r.status_code >= 500:
                last = f"HTTP {r.status_code}：{text}"
                self._ledger(skill, query, f"HTTP {r.status_code}", 0, ms)
                if r.status_code == 429 and QUOTA_WORDS.search(text) and "频繁" not in text:
                    raise WencaiFatal(f"问财额度不足：{text}")
                self.sleep(3 * (attempt + 1))
                continue
            try:
                payload = r.json()
            except ValueError:
                self._ledger(skill, query, "非 JSON", 0, ms)
                raise WencaiError(f"[{skill}] 返回的不是 JSON：{text}")
            code = payload.get("status_code", payload.get("code"))
            if payload.get("success") is False or code not in (0, "0", None, 200):
                msg = str(payload.get("status_msg") or payload.get("message") or payload.get("msg") or code)
                self._ledger(skill, query, f"错误 {code}：{msg[:60]}", 0, ms)
                if QUOTA_WORDS.search(msg):
                    raise WencaiFatal(f"问财额度不足或被限流：{msg}")
                raise WencaiError(f"[{skill}] {msg}")
            self.auth_failures = 0
            rows = len(payload.get("datas") or payload.get("data") or [])
            self._ledger(skill, query, "ok", rows, ms)
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps({"at": now().isoformat(), "payload": payload}, ensure_ascii=False),
                             encoding="utf-8")
            return payload, False
        raise WencaiError(f"[{skill}] 重试 3 次仍失败：{last}")


def usage(data_dir: Path = DATA_DIR, days: int = 7) -> list[dict]:
    """按天汇总本地记录的问财调用：真实调用次数、成功、失败、各技能次数。"""
    path = data_dir / "topics" / "wencai-usage.jsonl"
    if not path.exists():
        return []
    since = (now() - timedelta(days=days)).date().isoformat()
    by_day: dict[str, dict] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        day = r["at"][:10]
        if day < since:
            continue
        d = by_day.setdefault(day, {"day": day, "calls": 0, "ok": 0, "failed": 0, "skills": {}})
        d["calls"] += 1
        d["ok" if r.get("status") == "ok" else "failed"] += 1
        d["skills"][r.get("skill", "")] = d["skills"].get(r.get("skill", ""), 0) + 1
    return [by_day[k] for k in sorted(by_day)]


__all__ = ["Wencai", "WencaiError", "WencaiFatal", "Table", "Hit", "num", "code6", "base_name", "span_days",
           "parse_table", "parse_hits", "usage", "STOCKS", "SECTORS", "INDEX", "TZ"]
