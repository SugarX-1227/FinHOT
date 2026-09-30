"""并发抓取所有启用的信源。"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from .fetchers import FetchContext, FetchResult, run_fetch
from .http import Session

log = logging.getLogger(__name__)


def collect(sources: list[dict], since: datetime, only: list[str] | None = None,
            max_pages: int | None = None, workers: int = 8) -> list[FetchResult]:
    enabled = [s for s in sources if s.get("enabled", True) and (not only or s["id"] in only)]

    def one(src: dict) -> FetchResult:
        ctx = FetchContext(source=src, session=Session(),
                           since=since, max_pages=max_pages or int(src.get("max_pages", 10)))
        res = run_fetch(ctx)
        log.info("%-14s %-4s %4d 条  %2d 页  %5.1fs %s", src["id"], "OK" if res.ok else "FAIL",
                 len(res.items), res.pages, res.seconds, res.error)
        return res

    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(one, enabled))
