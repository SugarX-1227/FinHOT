"""官方与 RSS 类信源（单页，无翻页）。"""

from __future__ import annotations

import calendar
from datetime import datetime, timezone

import feedparser

from ..textutil import strip_html
from ..timeutil import TZ, now, parse_local
from .base import FetchContext, register


@register("govcn")
def fetch_govcn(ctx: FetchContext):
    """中国政府网"最新政策"JSON：只有日期没有时刻，统一记为当天 00:00。"""
    data = ctx.session.get_json(ctx.source["url"])
    items = []
    for d in data[:60]:
        title = (d.get("TITLE") or "").strip()
        if not title:
            continue
        items.append(ctx.make(
            title=title,
            content=(d.get("SUB_TITLE") or title).strip(),
            url=d.get("URL", ""),
            published=parse_local(d["DOCRELPUBTIME"]),
            source_id=d.get("URL", ""),
            tags=["国务院政策"],
        ))
    # 政策发布一般当天就被快讯转载，这里放宽到前一天，避免 00:00 的时间戳被窗口截掉
    for it in items:
        if it.published.date() >= ctx.since.date():
            it.published = max(it.published, ctx.since)
    yield items


@register("rss")
def fetch_rss(ctx: FetchContext):
    resp = ctx.session.get(ctx.source["url"])
    resp.raise_for_status()
    feed = feedparser.parse(resp.content)
    items = []
    for e in feed.entries:
        st = e.get("published_parsed") or e.get("updated_parsed")
        published = (
            datetime.fromtimestamp(calendar.timegm(st), tz=timezone.utc).astimezone(TZ) if st else now()
        )
        title = strip_html(e.get("title", ""))
        if not title:
            continue
        items.append(ctx.make(
            title=title,
            content=strip_html(e.get("summary", "")) or title,
            url=e.get("link", ""),
            published=published,
            source_id=e.get("id") or e.get("link", ""),
            tags=[t.get("term", "") for t in e.get("tags", []) if t.get("term")][:5],
        ))
    items.sort(key=lambda x: x.published, reverse=True)
    yield items
