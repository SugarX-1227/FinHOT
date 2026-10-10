"""把快讯持续推给网站引擎（web/，`POST /api/ingest/items`）。

每一轮：
1. 抓各快讯源最新的几页（只要上一轮之后的），合进最近几个小时的缓冲；
2. 对缓冲聚簇、按规则打分（和日报同一套 dedup / scoring）；
3. 分数够的簇，把簇里每家信源的那一条推给引擎：引擎里每家原始信源是一个信源，
   同一件事被几家报道，引擎才能算出多源热度。分数不够的先留在缓冲里，
   后面有更多信源跟进、分数涨上来了还会推。

规则分只用来控制送进模型的量（每条推过去的快讯，引擎都要调几次模型），
真正的精选由引擎的模型评分决定。

状态（缓冲、已推送的条目）存在 data/push-state.json，重启后接着来。

环境变量：
  FINHOT_ENGINE_URL         引擎 API 地址，默认 http://127.0.0.1:3001
  INGEST_TOKEN              和引擎 .env 里的同一个值
  FINHOT_PUSH_MIN_SCORE     簇的规则分达到多少才推，默认 6
  FINHOT_PUSH_MAX_PER_EVENT 一个簇最多推几家信源，默认 4
  FINHOT_PUSH_INTERVAL      两轮之间隔多少秒，默认 90
  FINHOT_PUSH_SOURCES       只推这些信源（逗号分隔），默认 sources.yaml 里所有非 RSS 的信源
                            （RSS 信源由引擎自己订阅）
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import requests

from .collect import collect
from .config import DATA_DIR, env, load_sources
from .dedup import clusters, merge
from .models import NewsItem
from .scoring import classify, rule_score
from .timeutil import TZ, now

log = logging.getLogger(__name__)

STATE_FILE = DATA_DIR / "push-state.json"
BUFFER_HOURS = 3        # 缓冲保留多久：同一件事的跟进报道一般在这段时间内到齐
PUSHED_DAYS = 2         # 已推送记录保留多久
FIRST_LOOKBACK_MIN = 30  # 第一次运行往回抓多久
OVERLAP_MIN = 5         # 每轮比上一轮多往回抓一点，防止边界漏条
MAX_PAGES = 3           # 每轮每个信源最多翻几页
BATCH = 50              # 引擎每次最多收 50 条


@dataclass
class PushConfig:
    engine_url: str = "http://127.0.0.1:3001"
    token: str = ""
    min_score: float = 6.0
    max_per_event: int = 4
    interval: float = 90.0
    sources: list[str] | None = None

    @classmethod
    def from_env(cls) -> "PushConfig":
        only = env("FINHOT_PUSH_SOURCES")
        return cls(
            engine_url=env("FINHOT_ENGINE_URL", cls.engine_url).rstrip("/"),
            token=env("INGEST_TOKEN"),
            min_score=float(env("FINHOT_PUSH_MIN_SCORE", str(cls.min_score))),
            max_per_event=int(env("FINHOT_PUSH_MAX_PER_EVENT", str(cls.max_per_event))),
            interval=float(env("FINHOT_PUSH_INTERVAL", str(cls.interval))),
            sources=[s.strip() for s in only.split(",") if s.strip()] if only else None,
        )


@dataclass
class PushState:
    buffer: dict[str, NewsItem] = field(default_factory=dict)
    pushed: dict[str, str] = field(default_factory=dict)   # uid → 推送时间
    last_fetch: datetime | None = None
    path: Path = STATE_FILE

    @classmethod
    def load(cls, path: Path = STATE_FILE) -> "PushState":
        if not path.exists():
            return cls(path=path)
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            log.warning("推送状态文件读不了，从头开始: %s", e)
            return cls(path=path)
        items = [NewsItem.from_dict(x) for x in d.get("buffer", [])]
        last = d.get("last_fetch")
        return cls(buffer={it.uid: it for it in items}, pushed=dict(d.get("pushed", {})),
                   last_fetch=datetime.fromisoformat(last) if last else None, path=path)

    def save(self) -> None:
        path = self.path
        path.parent.mkdir(parents=True, exist_ok=True)
        d = {
            "last_fetch": self.last_fetch.isoformat() if self.last_fetch else None,
            "pushed": self.pushed,
            "buffer": [it.to_dict() for it in self.buffer.values()],
        }
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)

    def add(self, items: list[NewsItem]) -> int:
        fresh = [it for it in items if it.uid not in self.buffer]
        for it in fresh:
            self.buffer[it.uid] = it
        return len(fresh)

    def prune(self, at: datetime) -> None:
        cut = at - timedelta(hours=BUFFER_HOURS)
        self.buffer = {k: v for k, v in self.buffer.items() if v.published >= cut}
        keep = (at - timedelta(days=PUSHED_DAYS)).isoformat()
        self.pushed = {k: v for k, v in self.pushed.items() if v >= keep}


def push_sources(cfg: PushConfig, all_sources: list[dict]) -> list[dict]:
    """要推的信源：指定了就用指定的，否则是所有启用的非 RSS 信源。"""
    enabled = [s for s in all_sources if s.get("enabled", True)]
    if cfg.sources:
        return [s for s in enabled if s["id"] in cfg.sources]
    return [s for s in enabled if s.get("type") != "rss"]


def select(state: PushState, cfg: PushConfig) -> list[tuple[NewsItem, float]]:
    """缓冲里该推、还没推的条目，以及它所在簇的规则分。"""
    copies = [replace(it) for it in state.buffer.values() if it.url]  # merge 会改动代表条目，用副本；没有网址的推不了
    out: list[tuple[NewsItem, float]] = []
    for group in clusters(copies):
        members = list(group)
        rep = merge(group)
        rep.category = classify(rep)
        score = rule_score(rep)
        if score < cfg.min_score:
            continue
        # 每家信源只推最早的一条；代表条目优先，其余按时间先后，最多 max_per_event 家
        by_source: dict[str, NewsItem] = {}
        for m in sorted(members, key=lambda m: (m.uid != rep.uid, m.published)):
            by_source.setdefault(m.source, m)
        for m in list(by_source.values())[:cfg.max_per_event]:
            if m.uid not in state.pushed:
                out.append((state.buffer[m.uid], score))
    return out


def _url(it: NewsItem) -> str:
    """引擎按网址判重。只给了直播页地址的快讯（新浪等）补上条目 id，免得几百条被当成同一条。"""
    url = (it.url or "").strip()
    if url and it.source_id and (url.rstrip("/").count("/") <= 3 or url.rstrip("/").endswith("7x24")):
        return f"{url}{'&' if '?' in url else '?'}finhot_id={it.source}-{it.source_id}"
    return url


def payload(it: NewsItem, score: float) -> dict[str, Any]:
    return {
        "title": it.title[:300],
        "url": _url(it),
        "publishedAt": it.published.astimezone(TZ).isoformat(),
        "body": it.content if it.content and it.content != it.title else it.title,
        "raw": {"finhot": {"uid": it.uid, "ruleScore": score, "important": it.important,
                           "tags": it.tags, "stocks": it.stocks}},
    }


class EngineError(Exception):
    pass


class Pusher:
    def __init__(self, cfg: PushConfig, session: requests.Session | None = None):
        self.cfg = cfg
        self.session = session or requests.Session()
        self.session.trust_env = False  # 引擎在本机或同一个 Docker 网络里，不走代理

    def post(self, source_id: str, source_name: str, items: list[dict]) -> requests.Response:
        return self.session.post(
            f"{self.cfg.engine_url}/api/ingest/items",
            json={"sourceId": source_id, "sourceName": source_name, "items": items},
            headers={"Authorization": f"Bearer {self.cfg.token}"},
            timeout=30,
        )

    def push(self, chosen: list[tuple[NewsItem, float]], state: PushState) -> dict[str, int]:
        """按信源分批推送；推成功（或信源已暂停）的记入 state.pushed。遇到限流就停，下一轮再推。"""
        stats = {"pushed": 0, "created": 0, "skipped": 0, "failed": 0}
        by_source: dict[str, list[tuple[NewsItem, float]]] = {}
        for it, score in chosen:
            by_source.setdefault(it.source, []).append((it, score))
        batches = []
        for source_id, rows in by_source.items():
            rows.sort(key=lambda r: r[0].published)
            batches += [(source_id, rows[i:i + BATCH]) for i in range(0, len(rows), BATCH)]
        stamp = now().isoformat()
        for n, (source_id, batch) in enumerate(batches):
            try:
                res = self.post(source_id, batch[0][0].source_name, [payload(it, s) for it, s in batch])
            except requests.RequestException as e:
                log.warning("推送 %s 失败，下一轮再试: %s", source_id, e)
                stats["failed"] += len(batch)
                continue
            if res.status_code == 200:
                stats["pushed"] += len(batch)
                try:
                    stats["created"] += int(res.json().get("created", 0))
                except (ValueError, AttributeError, TypeError):
                    pass
            elif res.status_code == 409:
                log.warning("信源 %s 在网站后台暂停了，这批不再推送", source_id)
                stats["skipped"] += len(batch)
            elif res.status_code == 429:
                log.warning("推送被限流（INGEST_RATE_LIMIT），剩下的下一轮再推")
                stats["failed"] += sum(len(b) for _, b in batches[n:])
                return stats
            elif res.status_code == 401:
                raise EngineError("引擎拒绝了推送（401）：检查 INGEST_TOKEN 和引擎 .env 里的是否一致、是否至少 16 位")
            else:
                log.warning("推送 %s 返回 %s：%s", source_id, res.status_code, res.text[:200])
                stats["failed"] += len(batch)
                continue
            for it, _ in batch:
                state.pushed[it.uid] = stamp
        return stats


def run_once(cfg: PushConfig, state: PushState, pusher: Pusher | None, sources: list[dict],
             dry_run: bool = False) -> dict[str, int]:
    at = now()
    since = (state.last_fetch - timedelta(minutes=OVERLAP_MIN)) if state.last_fetch \
        else at - timedelta(minutes=FIRST_LOOKBACK_MIN)
    since = max(since, at - timedelta(hours=BUFFER_HOURS))
    results = collect(sources, since=since, max_pages=MAX_PAGES)
    fetched = [it for r in results for it in r.items]
    added = state.add(fetched)
    state.last_fetch = at
    state.prune(at)
    chosen = select(state, cfg)
    stats = {"fetched": len(fetched), "new": added, "buffer": len(state.buffer), "chosen": len(chosen)}
    if dry_run:
        for it, score in chosen:
            log.info("[%.1f] %-12s %s", score, it.source, it.title[:60])
    elif chosen and pusher:
        stats.update(pusher.push(chosen, state))
    state.save()
    return stats


def loop(cfg: PushConfig, once: bool = False, dry_run: bool = False) -> int:
    if not dry_run and len(cfg.token) < 16:
        log.error("没有设置 INGEST_TOKEN（至少 16 位，和引擎 .env 里的一样）")
        return 2
    sources = push_sources(cfg, load_sources()["sources"])
    if not sources:
        log.error("没有可推送的信源（检查 FINHOT_PUSH_SOURCES）")
        return 2
    log.info("推送到 %s：%s；规则分 ≥ %.1f，每件事最多 %d 家，每 %.0f 秒一轮",
             cfg.engine_url, ",".join(s["id"] for s in sources), cfg.min_score, cfg.max_per_event, cfg.interval)
    state = PushState.load()
    pusher = None if dry_run else Pusher(cfg)
    while True:
        started = time.time()
        try:
            stats = run_once(cfg, state, pusher, sources, dry_run=dry_run)
            log.info("本轮 %s", " ".join(f"{k}={v}" for k, v in stats.items()))
        except EngineError as e:
            log.error("%s", e)
            return 1
        except Exception:  # noqa: BLE001 —— 常驻进程：单轮出错记日志，下一轮继续
            log.exception("本轮推送出错，下一轮继续")
        if once:
            return 0
        time.sleep(max(5.0, cfg.interval - (time.time() - started)))
