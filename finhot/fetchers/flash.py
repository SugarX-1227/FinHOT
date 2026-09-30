"""国内 7x24 快讯类信源。每个函数按时间倒序逐页 yield 一批 NewsItem。

接口均为各站网页端公开使用的 JSON 接口，站点改版时可能失效：
失效的信源会在 `python -m finhot sources` 健康检查和日报页脚中显示，不影响其他信源。
"""

from __future__ import annotations

import hashlib
import json
import time
import urllib.parse

from ..textutil import split_title, strip_html
from ..timeutil import from_ts, parse_local
from .base import FetchContext, register


# ---------------------------------------------------------------- 财联社
def _cls_sign(params: dict) -> str:
    q = urllib.parse.urlencode(sorted(params.items()))
    return hashlib.md5(hashlib.sha1(q.encode()).hexdigest().encode()).hexdigest()


def parse_cls(ctx: FetchContext, data: dict):
    items = []
    for d in data.get("data", {}).get("roll_data", []) or []:
        text = strip_html(d.get("content") or d.get("brief") or "")
        title, content = split_title(text, d.get("title", ""))
        items.append(ctx.make(
            title=title,
            content=content,
            url=f"https://www.cls.cn/detail/{d['id']}",
            published=from_ts(d["ctime"]),
            source_id=str(d["id"]),
            # level: A/B 为重要电报；bold/recommend 为加粗/推荐
            important=d.get("level") in ("A", "B") or bool(d.get("bold")) or bool(d.get("recommend")),
            tags=[s.get("subject_name", "") for s in (d.get("subjects") or []) if s.get("subject_name")][:5],
            stocks=[s.get("name", "") for s in (d.get("stock_list") or []) if s.get("name")][:8],
        ))
    return items


@register("cls")
def fetch_cls(ctx: FetchContext):
    last_time = int(time.time())
    for _ in range(ctx.max_pages):
        params = {
            "app": "CailianpressWeb", "category": "", "last_time": str(last_time),
            "os": "web", "refresh_type": "1", "rn": "20", "sv": "8.4.6",
        }
        params["sign"] = _cls_sign(params)
        data = ctx.session.get_json(
            "https://www.cls.cn/v1/roll/get_roll_list", params=params,
            headers={"Referer": "https://www.cls.cn/telegraph"},
        )
        if data.get("errno", 0) != 0:
            raise RuntimeError(f"cls errno={data.get('errno')} msg={data.get('msg')}")
        items = parse_cls(ctx, data)
        yield items
        if not items:
            return
        last_time = min(int(it.published.timestamp()) for it in items)


# ---------------------------------------------------------------- 华尔街见闻
def parse_wallstreetcn(ctx: FetchContext, data: dict):
    items = []
    for d in data.get("data", {}).get("items", []) or []:
        text = d.get("content_text") or strip_html(d.get("content", ""))
        more = strip_html(d.get("content_more", ""))
        if more:
            text = f"{text} {more}"
        title, content = split_title(text, d.get("title", ""))
        items.append(ctx.make(
            title=title,
            content=content,
            url=d.get("uri") or f"https://wallstreetcn.com/livenews/{d['id']}",
            published=from_ts(d["display_time"]),
            source_id=str(d["id"]),
            important=(d.get("score") or 1) >= 2,
            tags=[t.get("name", "") if isinstance(t, dict) else str(t) for t in (d.get("tags") or [])][:5],
            stocks=[s.get("name", "") for s in (d.get("symbols") or []) if isinstance(s, dict) and s.get("name")][:8],
        ))
    return items


@register("wallstreetcn")
def fetch_wallstreetcn(ctx: FetchContext):
    cursor = ""
    channel = ctx.options.get("channel", "global-channel")
    for _ in range(ctx.max_pages):
        params = {"channel": channel, "client": "pc", "limit": "100"}
        if cursor:
            params["cursor"] = cursor
        data = ctx.session.get_json("https://api-one-wscn.awtmt.com/apiv1/content/lives", params=params)
        yield parse_wallstreetcn(ctx, data)
        cursor = str(data.get("data", {}).get("next_cursor") or "")
        if not cursor:
            return


# ---------------------------------------------------------------- 金十数据
def parse_jin10(ctx: FetchContext, data: dict):
    items = []
    for d in data.get("data", []) or []:
        body = d.get("data") or {}
        if d.get("type") == 1:
            # 经济数据：拼成一句话
            name = body.get("name") or body.get("title") or ""
            if not name:
                continue
            text = f"{name} 公布值:{body.get('actual', '-')} 预期:{body.get('consensus', '-')} 前值:{body.get('previous', '-')}"
            title, content = text, text
        else:
            text = strip_html(body.get("content", ""))
            if not text or body.get("vip_title") or (d.get("extras") or {}).get("ad"):
                continue
            title, content = split_title(text, body.get("title", ""))
        items.append(ctx.make(
            title=title,
            content=content,
            url=body.get("source_link") or f"https://flash.jin10.com/detail/{d['id']}",
            published=parse_local(d["time"]),
            source_id=str(d["id"]),
            important=bool(d.get("important")),
            tags=[t.get("name", "") if isinstance(t, dict) else str(t) for t in (d.get("tags") or [])][:5],
        ))
    return items


@register("jin10")
def fetch_jin10(ctx: FetchContext):
    max_time = ""
    headers = {"x-app-id": "bVBF4FyRTn5NJF5n", "x-version": "1.0.0",
               "Referer": "https://www.jin10.com/", "Origin": "https://www.jin10.com"}
    for _ in range(ctx.max_pages):
        params = {"channel": "-8200", "vip": "1"}
        if max_time:
            params["max_time"] = max_time
        data = ctx.session.get_json("https://flash-api.jin10.com/get_flash_list", params=params, headers=headers)
        raw = data.get("data") or []
        yield parse_jin10(ctx, data)
        if not raw or raw[-1]["time"] == max_time:
            return
        max_time = raw[-1]["time"]


# ---------------------------------------------------------------- 东方财富
def parse_eastmoney(ctx: FetchContext, data: dict):
    items = []
    for d in (data.get("data") or {}).get("fastNewsList", []) or []:
        text = strip_html(d.get("summary", ""))
        title, content = split_title(text, d.get("title", ""))
        items.append(ctx.make(
            title=title,
            content=content,
            url=f"https://finance.eastmoney.com/a/{d['code']}.html",
            published=parse_local(d["showTime"]),
            source_id=str(d["code"]),
            important=bool(d.get("titleColor")),
        ))
    return items


@register("eastmoney")
def fetch_eastmoney(ctx: FetchContext):
    sort_end = ""
    for _ in range(ctx.max_pages):
        params = {"client": "web", "biz": "web_724", "fastColumn": "102", "sortEnd": sort_end,
                  "pageSize": "200", "req_trace": str(int(time.time() * 1000))}
        data = ctx.session.get_json("https://np-weblist.eastmoney.com/comm/web/getFastNewsList", params=params,
                                    headers={"Referer": "https://kuaixun.eastmoney.com/"})
        yield parse_eastmoney(ctx, data)
        sort_end = str((data.get("data") or {}).get("sortEnd") or "")
        if not sort_end:
            return


# ---------------------------------------------------------------- 新浪财经
def parse_sina(ctx: FetchContext, data: dict):
    items = []
    feed = data.get("result", {}).get("data", {}).get("feed", {}) or {}
    for d in feed.get("list", []) or []:
        text = strip_html(d.get("rich_text", ""))
        if not text:
            continue
        title, content = split_title(text)
        tags = [t.get("name", "") for t in (d.get("tag") or []) if t.get("name")]
        url = d.get("docurl") or ""
        try:
            ext = json.loads(d.get("ext") or "{}")
            url = ext.get("docurl") or url
            stocks = [s.get("key", "") for s in ext.get("stocks", []) if s.get("key")]
        except (ValueError, TypeError):
            stocks = []
        items.append(ctx.make(
            title=title,
            content=content,
            url=url or "https://finance.sina.com.cn/7x24/",
            published=parse_local(d["create_time"]),
            source_id=str(d["id"]),
            important=bool(d.get("is_focus")) or "焦点" in tags,
            tags=[t for t in tags if t != "其他"][:5],
            stocks=list(dict.fromkeys(stocks))[:8],
        ))
    return items


@register("sina")
def fetch_sina(ctx: FetchContext):
    for page in range(1, ctx.max_pages + 1):
        params = {"page": str(page), "page_size": "100", "zhibo_id": "152", "tag_id": "0",
                  "dire": "f", "dpc": "1", "type": "0"}
        data = ctx.session.get_json("https://zhibo.sina.com.cn/api/zhibo/feed", params=params,
                                    headers={"Referer": "https://finance.sina.com.cn/7x24/"})
        items = parse_sina(ctx, data)
        yield items
        if not items:
            return


# ---------------------------------------------------------------- 同花顺
def parse_ths(ctx: FetchContext, data: dict):
    items = []
    for d in (data.get("data") or {}).get("list", []) or []:
        title = strip_html(d.get("title", ""))
        content = strip_html(d.get("digest") or d.get("short") or title)
        items.append(ctx.make(
            title=title or split_title(content)[0],
            content=content,
            url=d.get("url") or "",
            published=from_ts(d["ctime"]),
            source_id=str(d.get("seq") or d["id"]),
            # color=2 / import=3 为加红重要资讯
            important=str(d.get("color")) == "2" or str(d.get("import", "0")) not in ("0", ""),
            tags=[t.get("name", "") for t in (d.get("tags") or []) if t.get("name")][:5],
            stocks=[s.get("name", "") for s in (d.get("stock") or []) if isinstance(s, dict) and s.get("name")][:8],
        ))
    return items


@register("ths")
def fetch_ths(ctx: FetchContext):
    for page in range(1, ctx.max_pages + 1):
        params = {"page": str(page), "tag": "", "track": "website", "pagesize": "100"}
        data = ctx.session.get_json("https://news.10jqka.com.cn/tapp/news/push/stock/", params=params,
                                    headers={"Referer": "https://news.10jqka.com.cn/realtimenews.html"})
        items = parse_ths(ctx, data)
        yield items
        if not items:
            return


# ---------------------------------------------------------------- 证券时报
def parse_stcn(ctx: FetchContext, data: dict):
    items = []
    for d in data.get("data", []) or []:
        text = strip_html(d.get("content", ""))
        title, content = split_title(text, d.get("title", ""))
        tags = []
        for group in d.get("tags") or []:
            for t in group if isinstance(group, list) else [group]:
                if isinstance(t, dict) and t.get("title") and not t.get("stock_code"):
                    tags.append(t["title"])
        url = d.get("url") or ""
        items.append(ctx.make(
            title=title,
            content=content,
            url=urllib.parse.urljoin("https://www.stcn.com/", url),
            published=from_ts(d.get("show_time") or d["time"]),
            source_id=str(d["id"]),
            important=bool(d.get("isRed") or d.get("red")),
            tags=tags[:5],
        ))
    return items


@register("stcn")
def fetch_stcn(ctx: FetchContext):
    page_time = ""
    for _ in range(ctx.max_pages):
        params = {"type": "kx"}
        if page_time:
            params["page_time"] = page_time
        data = ctx.session.get_json("https://www.stcn.com/article/list/kx.html", params=params,
                                    headers={"X-Requested-With": "XMLHttpRequest",
                                             "Referer": "https://www.stcn.com/article/list/kx.html"})
        items = parse_stcn(ctx, data)
        yield items
        raw = data.get("data") or []
        nxt = str(raw[-1].get("pageTime") or "") if raw else ""
        if not items or not nxt or nxt == page_time:
            return
        page_time = nxt


# ---------------------------------------------------------------- 第一财经
def parse_yicai(ctx: FetchContext, data):
    items = []
    for d in data if isinstance(data, list) else []:
        title = strip_html(d.get("LiveTitle") or d.get("NewsTitle") or "")
        content = strip_html(d.get("LiveContent") or title)
        if not title:
            title, content = split_title(content)
        items.append(ctx.make(
            title=title,
            content=content,
            url=urllib.parse.urljoin("https://www.yicai.com/", d.get("url") or f"/brief/{d['id']}.html"),
            published=parse_local(d["CreateDate"]),
            source_id=str(d.get("LiveID") or d.get("id")),
            important=bool(d.get("IsImportant") or d.get("important")),
        ))
    return items


@register("yicai")
def fetch_yicai(ctx: FetchContext):
    for page in range(1, ctx.max_pages + 1):
        r = ctx.session.get(f"https://www.yicai.com/api/ajax/getbrieflist?page={page}&pagesize=30",
                            headers={"Referer": "https://www.yicai.com/brief/"})
        r.raise_for_status()
        items = parse_yicai(ctx, r.json())
        yield items
        if not items:
            return
