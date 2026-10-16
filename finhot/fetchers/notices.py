"""上市公司公告类信源（个股一手源）：巨潮资讯（证监会指定披露平台）为主，东方财富公告中心备用。

和快讯源一样按页 yield NewsItem（新→旧），单源失败隔离；但它们不在 sources.yaml 的 sources 里，
而在 announcements 里：全市场一天几千条公告不进日报的快讯流，由 finhot/announce.py 单独取、按类型过滤后再用。

每条 NewsItem：title 是“简称：公告标题”，codes 是股票代码，tags 第一项是公告类型（sources/announcements.yaml），
tier 是 official。options.codes（{代码: orgId}）给定时只查这些公司（巨潮按公司查要 orgId；东方财富用代码）。
"""

from __future__ import annotations

import re

from ..textutil import strip_html
from ..timeutil import from_ts, now, parse_local
from .base import FetchContext, register

CNINFO_QUERY = "http://www.cninfo.com.cn/new/hisAnnouncement/query"
CNINFO_DETAIL = "http://www.cninfo.com.cn/new/disclosure/detail?stockCode={code}&announcementId={aid}&orgId={org}&announcementTime={day}"
EM_NOTICE = "https://np-anotice-stock.eastmoney.com/api/security/ann"
EM_DETAIL = "https://data.eastmoney.com/notices/detail/{code}/{art}.html"


def _clean_title(title: str, name: str) -> str:
    t = strip_html(title or "").strip()
    t = re.sub(rf"^{re.escape(name)}\s*[：:]?\s*", "", t) if name else t
    return t


def _item(ctx: FetchContext, code: str, name: str, title: str, url: str, published, sid: str):
    from ..announce import classify_type
    kind = classify_type(title)
    return ctx.make(title=f"{name}：{title}" if name else title, content=title, url=url, published=published,
                    source_id=sid, codes=[code], stocks=[name] if name else [], tags=[kind.name], tier="official")


# ---------------------------------------------------------------- 巨潮资讯
def parse_cninfo(ctx: FetchContext, data: dict):
    items = []
    for a in data.get("announcements") or []:
        code = str(a.get("secCode") or "").strip()
        if not re.fullmatch(r"\d{6}", code):
            continue
        name = re.sub(r"\s+", "", str(a.get("secName") or ""))
        published = from_ts(a["announcementTime"])
        aid = str(a.get("announcementId") or "")
        url = CNINFO_DETAIL.format(code=code, aid=aid, org=a.get("orgId") or "", day=published.strftime("%Y-%m-%d"))
        items.append(_item(ctx, code, name, _clean_title(a.get("announcementTitle", ""), name), url, published, aid))
    items.sort(key=lambda it: it.published, reverse=True)
    return items


@register("cninfo")
def fetch_cninfo(ctx: FetchContext):
    """按时间窗口分页取全市场（或 options.codes 指定的公司）公告。巨潮每页最多 30 条。"""
    opts = ctx.options
    day_range = f"{ctx.since:%Y-%m-%d}~{now():%Y-%m-%d}"
    stocks = opts.get("codes")
    if stocks is not None:
        # 按公司查要巨潮的 orgId；没有 orgId 的（种子清单）交给东方财富按代码查，绝不退回成全市场翻页
        targets, pages = [f"{c},{org}" for c, org in stocks.items() if org], min(ctx.max_pages, 3)
    else:
        targets, pages = [""], ctx.max_pages
    for stock in targets:
        for column in opts.get("columns") or ["szse"]:
            for page in range(1, pages + 1):
                form = {"pageNum": str(page), "pageSize": "30", "column": column, "tabName": "fulltext", "plate": "",
                        "stock": stock, "searchkey": "", "secid": "", "category": "", "trade": "", "seDate": day_range,
                        "sortName": "", "sortType": "", "isHLtitle": "true"}
                r = ctx.session.post(CNINFO_QUERY, data=form, headers={
                    "Referer": "http://www.cninfo.com.cn/new/commonUrl/pageOfSearch?url=disclosure/list/search",
                    "X-Requested-With": "XMLHttpRequest"})
                r.raise_for_status()
                data = r.json()
                items = parse_cninfo(ctx, data)
                yield items
                if not data.get("hasMore") or not items or items[-1].published < ctx.since:
                    break


# ---------------------------------------------------------------- 东方财富公告中心（备用）
def parse_em_notice(ctx: FetchContext, data: dict):
    items = []
    for a in ((data.get("data") or {}).get("list")) or []:
        codes = a.get("codes") or []
        if not codes:
            continue
        c = codes[0]
        code, name = str(c.get("stock_code") or ""), re.sub(r"\s+", "", str(c.get("short_name") or ""))
        if not re.fullmatch(r"\d{6}", code):
            continue
        when = a.get("display_time") or a.get("notice_date") or ""
        published = parse_local(when[:19])
        art = str(a.get("art_code") or "")
        items.append(_item(ctx, code, name, _clean_title(a.get("title", ""), name), EM_DETAIL.format(code=code, art=art),
                           published, art))
    items.sort(key=lambda it: it.published, reverse=True)
    return items


@register("eastmoney_notice")
def fetch_em_notice(ctx: FetchContext):
    codes = ctx.options.get("codes")
    if codes is not None and not codes:
        return
    for page in range(1, ctx.max_pages + 1):
        params = {"sr": "-1", "page_size": "100", "page_index": str(page), "ann_type": "A", "client_source": "web",
                  "f_node": "0", "s_node": "0"}
        if codes:
            params["stock_list"] = ",".join(codes)
        data = ctx.session.get_json(EM_NOTICE, params=params, headers={"Referer": "https://data.eastmoney.com/notices/"})
        items = parse_em_notice(ctx, data)
        yield items
        if not items:
            return
