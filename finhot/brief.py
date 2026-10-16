"""我的盘前 5 分钟 / 盘后版：只给和你有关的。

  python -m finhot brief --profile 我                    # 按当前时间出盘前或盘后版，存到个人数据目录、打印到终端
  python -m finhot brief --profile 我 --send             # 同时发到画像里配的飞书 / 企业微信群
  python -m finhot brief --profile 我 --no-market --no-site   # 不取行情、不读网站精选（离线调试）
  python -m finhot brief --profile 我 --weekly           # 我的周报：你的自选本周发生了什么 + 关注题材的信号战绩

内容（按栏目，栏内按排序分，见 finhot/personal.py）：
  持仓相关 → 自选观察 → 关联公司（关系库一层、同行业）→ 关注题材（新闻 × 资金状态 + 几条新闻）
  → 今天 / 明天与持仓、自选有关的日程（解禁、业绩披露、股东大会）→ 宏观压缩成一段。
每条都写“为什么给你”；不写利好利空、不给买卖建议，网站精选的“A股影响”改成“可能影响：…”的中性说法。

不配模型也能出：快讯用规则分，公告用类型表；配了网站（FINHOT_SITE_URL / FINHOT_ENGINE_URL）时，
网站精选过的条目带上网站的评分和摘要（模型在网站那边已经算过，这里不再调模型）。

只在本机或服务器上生成：输出在 FINHOT_PRIVATE_DIR 下；在 GitHub Actions 里直接拒绝（finhot/profile.py）。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from pathlib import Path

from . import announce, history
from .ashare import MarketSnapshot
from .config import env, load_sources
from .crosscheck import ThemeRow, cross_check
from .entities import Book, book as default_book
from .models import NewsItem
from .notify import feedback_links
from .personal import Pick, Ranker, default_weights, select, title_grams, weak_mentions
from .profile import Profile, private_dir, refuse_ci
from .themes import item_themes, story_of
from .timeutil import TZ, now

log = logging.getLogger(__name__)

EDITION_NAMES = {"pre": "盘前", "noon": "午间", "close": "盘后"}
DISCLAIMER = ("以上是与你的持仓、自选和关注题材有关的公开信息整理，由程序按规则自动筛选生成；“可能影响”只表示相关，"
              "不代表方向判断，不构成任何投资建议。")
MIN_HOURS, MAX_HOURS, DEFAULT_HOURS = 6, 72, 24


@dataclass
class BriefInput:
    """出一份简报要的全部材料（取数和排版分开，排版可以离线测）。"""
    news: list[NewsItem] = field(default_factory=list)
    announcements: list[NewsItem] = field(default_factory=list)
    snapshot: MarketSnapshot | None = None
    themes: list[ThemeRow] = field(default_factory=list)
    schedule: list[dict] = field(default_factory=list)   # [{date, code, kind, text}]
    errors: list[str] = field(default_factory=list)
    site_items: int = 0


# ------------------------------------------------------------------ 网站精选
_IMPACT = re.compile(r"^\s*(?:利好|利空|中性)\s*")


def neutral_reason(reason: str) -> str:
    """网站精选的“A股影响”（“利好 半导体、存储，依据……”）→ “可能影响：半导体、存储”。"""
    text = _IMPACT.sub("", reason or "")
    targets = re.split(r"[，,。；;]", text, maxsplit=1)[0].strip()
    return f"可能影响：{targets}" if targets else ""


def _norm_url(url: str) -> str:
    return re.sub(r"[?&]finhot_id=[^&]+", "", (url or "").strip()).rstrip("/")


def merge_site(news: list[NewsItem], selected: list[dict], since: datetime) -> int:
    """网站精选并进快讯：同一网址的带上网站评分和摘要；快讯里没有的（引擎自己订阅的 RSS 等）加进来。返回用上的条数。"""
    by_url = {_norm_url(it.url): it for it in news if it.url}
    n = 0
    for s in selected:
        published = s.get("publishedAt") or s.get("discoveredAt")
        if not published:
            continue
        at = datetime.fromisoformat(str(published).replace("Z", "+00:00")).astimezone(TZ)
        if at < since:
            continue
        extra = {"engine_score": float(s["score"]) if s.get("score") is not None else None,
                 "engine_reason": neutral_reason(str(s.get("reason") or "")), "summary": s.get("summary") or ""}
        it = by_url.get(_norm_url(str(s.get("url") or "")))
        if it is None:
            it = NewsItem(source="site", source_name=f"网站精选·{s.get('sourceName') or ''}".rstrip("·"),
                          title=str(s.get("title") or ""), content=str(s.get("summary") or ""), url=str(s.get("url") or ""),
                          published=at, source_id=str(s.get("articleId") or ""))
            from .scoring import classify, rule_score
            it.category, it.score = classify(it), rule_score(it)
            news.append(it)
        it.llm = {**it.llm, **{k: v for k, v in extra.items() if v not in (None, "")}}
        n += 1
    return n


# ------------------------------------------------------------------ 取数
def window(profile: str, at: datetime, hours: float | None) -> datetime:
    if hours:
        return at - timedelta(hours=hours)
    briefs = [datetime.fromisoformat(r["at"]) for r in history.shown(profile, channel="brief")]
    last = max(briefs) if briefs else at - timedelta(hours=DEFAULT_HOURS)
    return max(at - timedelta(hours=MAX_HOURS), min(last, at - timedelta(hours=MIN_HOURS)))


def related_codes(p: Profile, b: Book) -> list[str]:
    out = list(p.codes)
    for r in p.confirmed_relations():
        target = r.target if re.fullmatch(r"\d{6}", r.target) else next((s.code for s in b.resolve(r.target)), "")
        if target:
            out.append(target)
    return list(dict.fromkeys(out))


def gather(p: Profile, since: datetime, at: datetime, market: bool = True, site: bool = True,
           b: Book | None = None) -> BriefInput:
    """取材料：快讯（规则分）、网站精选、持仓自选和关联公司的公告、行情快照、日程。任何一项失败只记在 errors。"""
    from concurrent.futures import ThreadPoolExecutor

    from .collect import collect
    from .dedup import dedup
    from .scoring import score_all

    b = b or default_book()
    cfg = load_sources()
    inp = BriefInput()
    codes = related_codes(p, b)
    org = {c: (b.get(c).org_id if b.get(c) else "") for c in codes}

    def ann():
        return announce.fetch(since, org, announce.announcement_sources(cfg)) if codes else ([], [])

    def snap():
        from .ashare import fetch_snapshot
        return fetch_snapshot() if market and p.themes else None

    def schedule():
        return fetch_schedule(p.codes, at, b) if market and p.codes else []

    with ThreadPoolExecutor(max_workers=3) as pool:
        f_ann, f_snap, f_sched = pool.submit(ann), pool.submit(snap), pool.submit(schedule)
        results = collect(cfg["sources"], since)
        inp.news = score_all(dedup([it for r in results for it in r.items]))
        inp.errors += [f"{r.name}：{r.error}"[:120] for r in results if not r.ok]
        for name, fut in (("公告", f_ann), ("行情", f_snap), ("日程", f_sched)):
            try:
                val = fut.result()
            except Exception as e:  # noqa: BLE001 —— 单项失败不影响简报
                inp.errors.append(f"{name}：{type(e).__name__}: {e}"[:160])
                continue
            if name == "公告":
                inp.announcements, ann_results = val
                inp.errors += [f"{r.name}：{r.error}"[:120] for r in ann_results if not r.ok]
            elif name == "行情":
                inp.snapshot = val
            else:
                inp.schedule = val
    if inp.snapshot and inp.snapshot.ok:
        inp.themes = cross_check(inp.news, inp.snapshot, limit=99, keep_all=True)
    if site:
        from .site_api import Site
        url = env("FINHOT_SITE_URL") or env("FINHOT_ENGINE_URL")
        if url:
            try:
                inp.site_items = merge_site(inp.news, Site(url.rstrip("/")).selected(pages=3), since)
            except Exception as e:  # noqa: BLE001
                inp.errors.append(f"网站精选：{type(e).__name__}")
    return inp


def fetch_schedule(codes: list[str], at: datetime, b: Book, session=None) -> list[dict]:
    """持仓、自选未来 7 天的解禁和业绩预约披露（东方财富数据中心）。"""
    from .calendar_watch import _dc
    from .http import Session
    session = session or Session()
    d0, d1 = at.strftime("%Y-%m-%d"), (at + timedelta(days=7)).strftime("%Y-%m-%d")
    inlist = ",".join(f'"{c}"' for c in codes)
    out: list[dict] = []
    lifts = _dc(session, "RPT_LIFT_STAGE", f"(SECURITY_CODE in ({inlist}))(FREE_DATE>='{d0}')(FREE_DATE<='{d1}')",
                "FREE_DATE", page_size=100)
    for r in ((lifts.get("result") or {}).get("data")) or []:
        out.append({"date": (r.get("FREE_DATE") or "")[:10], "code": r.get("SECURITY_CODE"), "kind": "解禁",
                    "text": f"解禁 {round((r.get('LIFT_MARKET_CAP') or 0) / 1e4, 1)} 亿元，占流通股 {round(r.get('FREE_RATIO') or 0, 2)}%"})
    try:
        appoint = _dc(session, "RPT_PUBLIC_BS_APPOIN", f"(SECURITY_CODE in ({inlist}))", "REPORT_DATE", page_size=100)
        for r in ((appoint.get("result") or {}).get("data")) or []:
            day = (r.get("ACTUAL_PUBLISH_DATE") or r.get("FIRST_APPOINT_DATE") or "")[:10]
            if d0 <= day <= d1:
                out.append({"date": day, "code": r.get("SECURITY_CODE"), "kind": "披露",
                            "text": f"预约披露{(r.get('REPORT_TYPE_NAME') or '定期报告')}"})
    except Exception as e:  # noqa: BLE001 —— 预约披露是附带的
        log.info("预约披露取不到：%s", e)
    return sorted(out, key=lambda r: r["date"])


# ------------------------------------------------------------------ 排版
@dataclass
class Brief:
    profile: Profile
    edition: str
    at: datetime
    since: datetime
    sections: dict[str, list[Pick]]
    themes: list[ThemeRow]
    schedule: list[dict]
    macro: list[tuple[str, list[Pick]]]
    weak: dict[str, list[Pick]]
    stats: dict
    errors: list[str]
    markdown: str = ""

    @property
    def picks(self) -> list[Pick]:
        return [p for v in self.sections.values() for p in v] + [p for _, v in self.macro for p in v]


def build(p: Profile, inp: BriefInput, at: datetime, since: datetime, edition: str | None = None,
          b: Book | None = None, weights: dict | None = None, learned: dict | None = None,
          seen: tuple[set[str], list[set[str]]] | None = None) -> Brief:
    """排序、分栏、压缩宏观段（纯函数：不取数、不写文件）。"""
    w = weights or default_weights()
    b = b or default_book()
    ranker = Ranker(p, w, learned or {}, b)
    seen_ids, seen_grams = seen or (set(), [])
    picks: list[Pick] = []
    repeats = 0
    for it in [*inp.announcements, *inp.news]:
        pk = ranker.rank_one(it, at)
        if pk is None:
            continue
        # 同一件事给过了就不再给：同一条（id 一样）不给；字面很像的新闻也不给，公告除外（公告本身就是新进展）
        if it.uid in seen_ids or (not announce.is_announcement(it)
                                  and any(len(title_grams(it) & g) >= 0.6 * max(4, min(len(g), len(title_grams(it))))
                                          for g in seen_grams)):
            repeats += 1
            continue
        picks.append(pk)
    personal = [pk for pk in picks if pk.section != "宏观"]
    sections = select(personal, w)
    macro = compress_macro([pk for pk in picks if pk.section == "宏观"], int((w.get("sections") or {}).get("宏观", 5)))
    ed = edition or ("pre" if at.time() < time(12) else "close" if at.time() >= time(15) else "noon")
    rows = [r for r in inp.themes if r.theme in p.themes]
    for t in p.themes:
        if t not in {r.theme for r in rows}:
            rows.append(ThemeRow(t))
    stats = {"news": len(inp.news), "announcements": len(inp.announcements), "candidates": len(picks),
             "repeats": repeats, "site": inp.site_items}
    for r in rows:
        if not r.news_count:   # 行情里对不上板块的题材：新闻条数照样数
            r.news_count = sum(1 for it in inp.news if it.score >= 5 and r.theme in item_themes(it))
    br = Brief(p, ed, at, since, sections, rows, inp.schedule, macro, weak_mentions(personal, w), stats, inp.errors)
    br.markdown = render(br, b)
    return br


def compress_macro(picks: list[Pick], limit: int = 5) -> list[tuple[str, list[Pick]]]:
    """宏观压成几条主线：同一主线（sources/stories.yaml）或同一主题材的放一起，每条主线只写最重要的一句和条数。"""
    groups: dict[str, list[Pick]] = {}
    for pk in sorted(picks, key=lambda x: -x.importance):
        title = pk.item.llm.get("title") or pk.item.title
        key = story_of(title) or next(iter(pk.item.llm.get("sectors") or []), "") or title[:14]
        groups.setdefault(key, []).append(pk)
    ranked = sorted(groups.items(), key=lambda kv: -max(x.importance for x in kv[1]) - 0.3 * len(kv[1]))
    return ranked[:limit]


def _title(it: NewsItem) -> str:
    return (it.llm.get("title") or it.title).strip()


def _entry(pk: Pick, idx: int, profile: str) -> list[str]:
    it = pk.item
    srcs = it.source_name + (f" 等{1 + len(it.dup_sources)}家" if it.dup_sources else "")
    link = f" · [原文]({it.url})" if it.url else ""
    lines = [f"{idx}. **{_title(it)}**\\", f"   为什么给你：{pk.reason}\\"]
    extra = it.llm.get("engine_reason") or ""
    summary = it.llm.get("summary") or ""
    if summary and summary not in _title(it):
        lines.append(f"   {summary[:120]}\\")
    if extra:
        lines.append(f"   网站精选 · {extra}\\")
    fb = feedback_links(profile, pk.id)
    lines.append(f"   <sub>{it.published.astimezone(TZ):%m-%d %H:%M} · {srcs}{link}" + (f" · {fb}" if fb else "") + "</sub>")
    return lines


def _theme_line(r: ThemeRow) -> str:
    bits = [f"新闻 {r.news_count} 条"]
    if r.pct is not None:
        bits.append(f"板块 {r.pct:+.2f}%")
    if r.inflow is not None:
        bits.append(f"主力 {r.inflow:+.1f} 亿")
    if r.zt:
        bits.append(f"涨停 {r.zt} 家")
    return " · ".join(bits) + (f" → **{r.verdict}**" if r.verdict else "")


def render(br: Brief, b: Book) -> str:
    p = br.profile
    name = EDITION_NAMES.get(br.edition, br.edition)
    title = "我的盘前 5 分钟" if br.edition == "pre" else f"我的{name}简报"
    out = [f"# {title} · {br.at:%m-%d %H:%M}（{p.name}）", "",
           f"> 时间窗口 {br.since:%m-%d %H:%M} ~ {br.at:%m-%d %H:%M} · 持仓 {len(p.holdings)} 只 · 自选 {len(p.watch)} 只 · "
           f"关注题材 {'、'.join(p.themes) or '无'} · 快讯 {br.stats['news']} 条 · 公告 {br.stats['announcements']} 条"
           + (f" · 网站精选 {br.stats['site']} 条" if br.stats.get("site") else "")
           + (f" · 之前给过、略去 {br.stats['repeats']} 条" if br.stats.get("repeats") else ""), ""]
    labels = {"持仓": "持仓相关", "自选": "自选观察", "关联公司": "关联公司"}
    for sec, label in labels.items():
        if sec != "持仓" and not br.sections[sec] and not (br.weak.get(sec)):
            continue
        out += [f"## {label}", ""]
        rows = br.sections[sec]
        weak = br.weak.get(sec) or []
        if not rows:
            out += ["_这段时间持仓没有新公告，也没有以它为主体的新闻。_" if p.holdings else "_画像里还没有持仓。_", ""]
        for i, pk in enumerate(rows, 1):
            out += _entry(pk, i, p.name) + [""]
        if weak:
            names = "、".join(dict.fromkeys(pk.reason.split(" · ")[0].split(" ", 1)[1] for pk in weak))
            heads = "；".join(f"[{_title(pk.item)[:24]}]({pk.item.url})" if pk.item.url else _title(pk.item)[:24]
                             for pk in weak[:4])
            out += [f"<sub>另有 {len(weak)} 条只是顺带提到 {names}（正文、行情播报、机构观点里）：{heads}"
                    + ("……" if len(weak) > 4 else "") + "</sub>", ""]

    if p.themes:
        out += ["## 关注题材", ""]
        by_theme: dict[str, list[Pick]] = {}
        for pk in br.sections["关注题材"]:
            by_theme.setdefault(pk.theme, []).append(pk)
        for r in br.themes:
            out += [f"### {r.theme} · {_theme_line(r)}", ""]
            rows = by_theme.get(r.theme, [])
            if not rows:
                out += ["_这段时间没有这个题材的新消息。_", ""]
            for i, pk in enumerate(rows, 1):
                out += _entry(pk, i, p.name) + [""]

    if br.schedule:
        out += ["## 日程（与持仓、自选有关）", ""]
        for s in br.schedule:
            out.append(f"- {s['date'][5:]} · {b.label(s['code'])} · {s['text']}")
        out.append("")

    if br.macro:
        out += ["## 宏观一段", ""]
        parts = []
        for key, rows in br.macro:
            head = _title(rows[0].item)
            count = f"（{len(rows)} 条）" if len(rows) > 1 else ""
            parts.append(f"**{key}**{count}：{head}" if story_of(head) == key else f"{head}{count}")
        out += ["；".join(parts) + "。", ""]

    out += ["---", "", f"*{DISCLAIMER}*", ""]
    if br.errors:
        out += ["<details><summary>没取到的数据</summary>", ""] + [f"- {e}" for e in br.errors[:12]] + ["", "</details>", ""]
    return "\n".join(out)


# ------------------------------------------------------------------ 出一份
def run(p: Profile, at: datetime | None = None, hours: float | None = None, edition: str | None = None,
        market: bool = True, site: bool = True, send: bool = False, inp: BriefInput | None = None) -> Brief:
    refuse_ci()
    at = at or now()
    since = window(p.name, at, hours)
    inp = inp or gather(p, since, at, market=market, site=site)
    w = default_weights()
    br = build(p, inp, at, since, edition, weights=w, learned=history.learned_factors(p.name, w),
               seen=history.recent_grams(p.name, at, float(w.get("repeat_days", 3))))
    day = at.strftime("%Y-%m-%d")
    out = private_dir() / "briefs" / p.name / f"{day}-{br.edition}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(br.markdown, encoding="utf-8")
    history.save_candidates(p.name, day, br.edition, candidate_rows(p, inp, at))
    rows = [history.Shown(p.name, pk.id, _title(pk.item), sorted(title_grams(pk.item)), at.isoformat(), pk.section,
                          pk.reason_kind, "brief", pk.code, pk.item.url) for pk in br.picks]
    rows.append(history.Shown(p.name, f"brief:{day}-{br.edition}", "", [], at.isoformat(), channel="brief"))
    history.record_shown(rows)
    if send:
        from .notify import send as send_msg
        title = f"{'我的盘前 5 分钟' if br.edition == 'pre' else '我的' + EDITION_NAMES[br.edition] + '简报'} · {at:%m-%d}"
        send_msg(p, title, br.markdown.split("\n", 2)[2])
    log.info("简报：%s（%d 条）", out, len(br.picks))
    return br


def candidate_rows(p: Profile, inp: BriefInput, at: datetime) -> list[dict]:
    """全部候选（不只是进了简报的）：个人排序分和通用分都存下，评估时盲标对比两种排序。"""
    ranker = Ranker(p)
    rows = []
    for it in [*inp.announcements, *inp.news]:
        pk = ranker.rank_one(it, at)
        rows.append({"id": it.uid, "title": _title(it), "url": it.url, "published": it.published.isoformat(),
                     "generic": it.score, "personal": pk.rank if pk else 0.0, "section": pk.section if pk else "",
                     "reason": pk.reason if pk else "", "codes": it.codes})
    return rows


def due(p: Profile, at: datetime, done: dict[str, str]) -> str | None:
    """到点了要出的简报版次（工作日；同一版一天只出一次）。done：{画像:版次 → 日期}。"""
    at = at.astimezone(TZ)
    if at.weekday() >= 5:
        return None
    for ed, t in p.brief_times():
        key = f"{p.name}:{ed}"
        if at.time() >= t and done.get(key) != at.date().isoformat() and \
                (datetime.combine(at.date(), t, TZ) + timedelta(hours=3)) > at:
            return ed
    return None


def save_path(p: Profile, at: datetime, edition: str) -> Path:
    return private_dir() / "briefs" / p.name / f"{at:%Y-%m-%d}-{edition}.md"


def dump(br: Brief) -> str:
    return json.dumps({k: [pk.to_dict() for pk in v] for k, v in br.sections.items()}, ensure_ascii=False, indent=1)
