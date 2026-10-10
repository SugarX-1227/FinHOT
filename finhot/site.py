"""静态网站生成：把 reports/ 下的日报、周报渲染成 HTML，供 GitHub Pages 发布。

输出（默认 _site/）：
  index.html            最新日报 + 近期列表
  archive.html          全部日报（按月分组）
  daily/<name>.html     每期日报
  weekly/index.html     周报列表
  weekly/<YYYY-Www>.html
  feed.xml              RSS 订阅
Markdown 用 markdown-it-py（CommonMark + 表格）渲染，效果与 GitHub 上看到的一致。
"""

from __future__ import annotations

import html
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from email.utils import format_datetime
from pathlib import Path

from markdown_it import MarkdownIt

from .config import REPORTS_DIR
from .timeutil import TZ, now

NAME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})(-noon|-close)?$")
WEEK_RE = re.compile(r"^(\d{4})-W(\d{2})$")
EDITION = {"": ("盘前", 7), "-noon": ("午间", 12), "-close": ("盘后", 16)}
WEEKDAY = "一二三四五六日"


@dataclass
class Page:
    kind: str          # daily | weekly
    name: str          # 文件名（不含扩展名）
    title: str
    md: str
    when: datetime     # 用于排序与 RSS
    label: str         # 列表里显示的短标签

    @property
    def href(self) -> str:
        return f"{self.kind}/{self.name}.html"


def _md() -> MarkdownIt:
    md = MarkdownIt("commonmark", {"html": True, "linkify": False, "typographer": False}).enable("table")
    md.add_render_rule("table_open", lambda *a, **k: '<div class="table-wrap"><table>\n')
    md.add_render_rule("table_close", lambda *a, **k: "</table></div>\n")

    def link_open(self, tokens, idx, options, env):
        href = tokens[idx].attrGet("href") or ""
        if href.startswith("http"):
            tokens[idx].attrSet("target", "_blank")
            tokens[idx].attrSet("rel", "noopener")
        return self.renderToken(tokens, idx, options, env)

    md.add_render_rule("link_open", link_open)
    return md


def _render(md: MarkdownIt, text: str) -> str:
    out = md.render(text)
    # A 股习惯：红涨绿跌
    out = out.replace("🔺", '<span class="up">▲</span>').replace("🔻", '<span class="down">▼</span>')
    return out


def _generated_at(md: str, fallback: datetime) -> datetime:
    m = re.search(r"生成(?:时间|于) ?(\d{4}-\d{2}-\d{2} \d{2}:\d{2})", md)
    if m:
        return datetime.strptime(m.group(1), "%Y-%m-%d %H:%M").replace(tzinfo=TZ)
    return fallback


def load_pages(reports_dir: Path = REPORTS_DIR) -> tuple[list[Page], list[Page]]:
    daily, weekly = [], []
    for f in sorted(reports_dir.glob("*.md")):
        m = NAME_RE.match(f.stem)
        if not m:
            continue  # latest.md 等
        text = f.read_text(encoding="utf-8")
        title = text.splitlines()[0].lstrip("# ").strip() if text.strip() else f.stem
        ed, hour = EDITION[m.group(2) or ""]
        d = datetime.strptime(m.group(1), "%Y-%m-%d").replace(hour=hour, tzinfo=TZ)
        label = f"{d:%m-%d} 周{WEEKDAY[d.weekday()]} · {ed}"
        daily.append(Page("daily", f.stem, title, text, _generated_at(text, d), label))
    for f in sorted((reports_dir / "weekly").glob("*.md")):
        m = WEEK_RE.match(f.stem)
        if not m:
            continue
        text = f.read_text(encoding="utf-8")
        title = text.splitlines()[0].lstrip("# ").strip()
        d = datetime.fromisocalendar(int(m.group(1)), int(m.group(2)), 6).replace(hour=9, tzinfo=TZ)
        weekly.append(Page("weekly", f.stem, title, text, _generated_at(text, d), f"{m.group(1)} 第{int(m.group(2))}周"))
    # 同一天多版按版次排序；整体按日期倒序
    daily.sort(key=lambda p: (p.name[:10], {"": 0, "-noon": 1, "-close": 2}[p.name[10:]]), reverse=True)
    weekly.sort(key=lambda p: p.name, reverse=True)
    return daily, weekly


CSS = """
:root{--bg:#f7f7f5;--card:#fff;--fg:#1d1d1f;--muted:#6b6b70;--line:#e6e6e3;--accent:#c8102e;--accent-soft:#fbeaec;
--up:#d0021b;--down:#0a8a3a;--code:#f2f2ef;--shadow:0 1px 2px rgba(0,0,0,.04),0 4px 16px rgba(0,0,0,.04)}
@media (prefers-color-scheme:dark){:root{--bg:#121214;--card:#1b1b1e;--fg:#e9e9ec;--muted:#9a9aa2;--line:#2c2c31;
--accent:#ff5a6e;--accent-soft:#3a1e24;--up:#ff5a5a;--down:#2fc46b;--code:#26262b;--shadow:none}}
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.75 -apple-system,BlinkMacSystemFont,"PingFang SC",
"Hiragino Sans GB","Microsoft YaHei","Noto Sans CJK SC",sans-serif}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
header.top{position:sticky;top:0;z-index:5;background:color-mix(in srgb,var(--bg) 88%,transparent);
backdrop-filter:blur(8px);border-bottom:1px solid var(--line)}
.bar{max-width:880px;margin:0 auto;padding:10px 16px;display:flex;align-items:center;gap:18px;flex-wrap:wrap}
.brand{font-weight:800;font-size:18px;color:var(--fg);letter-spacing:.5px}.brand b{color:var(--accent)}
nav a{color:var(--muted);margin-right:14px;font-size:15px}nav a.on{color:var(--fg);font-weight:600}
main{max-width:880px;margin:0 auto;padding:20px 16px 60px}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:22px 26px;box-shadow:var(--shadow)}
@media (max-width:640px){.card{padding:16px 14px;border-radius:10px}body{font-size:15px}}
h1{font-size:24px;line-height:1.4;margin:0 0 6px}
.card h2{font-size:19px;margin:28px 0 12px;padding-left:10px;border-left:4px solid var(--accent)}
.card h1+blockquote,.card>blockquote:first-of-type{color:var(--muted)}
blockquote{margin:8px 0;padding:6px 12px;border-left:3px solid var(--line);color:var(--muted);background:transparent}
li>blockquote{margin:4px 0}
ol,ul{padding-left:1.4em}li{margin:4px 0}li p{margin:4px 0}
sub{color:var(--muted);font-size:12.5px;vertical-align:baseline}
.table-wrap{overflow-x:auto;margin:10px 0;-webkit-overflow-scrolling:touch}
table{border-collapse:collapse;width:100%;font-size:14px;white-space:nowrap}
th,td{padding:6px 10px;border-bottom:1px solid var(--line);text-align:left}
td:last-child{white-space:normal;min-width:200px}th{color:var(--muted);font-weight:600}
td[style*="right"],th[style*="right"]{font-variant-numeric:tabular-nums}
code{background:var(--code);padding:1px 5px;border-radius:4px;font-size:.9em}
details{margin:12px 0;color:var(--muted)}summary{cursor:pointer}
hr{border:0;border-top:1px solid var(--line);margin:24px 0}
.up{color:var(--up);font-size:.8em}.down{color:var(--down);font-size:.8em}
.pager{display:flex;justify-content:space-between;gap:12px;margin:18px 2px;font-size:14px}
.crumb{font-size:13px;color:var(--muted);margin:0 2px 10px}
.list{list-style:none;padding:0;margin:0}.list li{display:flex;gap:10px;align-items:baseline;padding:7px 0;
border-bottom:1px dashed var(--line)}.list li:last-child{border:0}
.chip{display:inline-block;font-size:12px;padding:1px 8px;border-radius:999px;background:var(--accent-soft);color:var(--accent)}
.month{margin:22px 0 6px;font-size:16px;color:var(--muted)}
.days{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:8px}
.day{border:1px solid var(--line);border-radius:10px;padding:8px 10px;background:var(--card)}
.day b{display:block;font-size:14px}.day a{font-size:13px;margin-right:8px}
.section-title{font-size:15px;color:var(--muted);margin:26px 2px 10px}
footer{max-width:880px;margin:0 auto;padding:0 16px 40px;color:var(--muted);font-size:13px}
"""


def _layout(title: str, body: str, active: str, depth: int) -> str:
    root = "../" * depth
    nav = "".join(
        f'<a href="{root}{href}" class="{"on" if key == active else ""}">{name}</a>'
        for key, href, name in (("index", "index.html", "最新"), ("archive", "archive.html", "往期日报"),
                                ("weekly", "weekly/index.html", "周报"), ("feed", "feed.xml", "RSS")))
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title>
<link rel="alternate" type="application/rss+xml" title="FinHOT" href="{root}feed.xml">
<style>{CSS}</style></head>
<body><header class="top"><div class="bar"><a class="brand" href="{root}index.html">Fin<b>HOT</b></a><nav>{nav}</nav></div></header>
<main>{body}</main>
<footer>FinHOT · 财经热点日报（A股交易参考）· 内容为公开信息整理与自动生成，不构成任何投资建议。</footer>
</body></html>
"""


def _page_html(md: MarkdownIt, p: Page, prev: Page | None, nxt: Page | None) -> str:
    pager = '<div class="pager">'
    pager += f'<a href="{prev.name}.html">← {html.escape(prev.label)}</a>' if prev else "<span></span>"
    pager += f'<a href="{nxt.name}.html">{html.escape(nxt.label)} →</a>' if nxt else "<span></span>"
    pager += "</div>"
    body = f'{pager}<article class="card">{_render(md, p.md)}</article>{pager}'
    active = "weekly" if p.kind == "weekly" else "archive"
    return _layout(p.title, body, active, depth=1)


def _summary_md(text: str) -> str:
    """RSS 摘要：优先"要点速览/本周要点"，否则取"今日必读/十大新闻"前 5 条标题。"""
    m = re.search(r"^## (?:要点速览|本周要点|盘前要点)\n+(.*?)(?=^## |\Z)", text, re.S | re.M)
    if m:
        return m.group(1).strip()
    titles = re.findall(r"^\d+\. \*\*(.+?)\*\*", text, re.M)[:5]
    return "\n".join(f"- {t}" for t in titles)


def build_site(out: Path, reports_dir: Path = REPORTS_DIR, base_url: str = "") -> Path:
    md = _md()
    daily, weekly = load_pages(reports_dir)
    if out.exists():
        shutil.rmtree(out)
    (out / "daily").mkdir(parents=True)
    (out / "weekly").mkdir(parents=True)

    for pages in (daily, weekly):
        for i, p in enumerate(pages):
            older = pages[i + 1] if i + 1 < len(pages) else None
            newer = pages[i - 1] if i > 0 else None
            (out / p.kind / f"{p.name}.html").write_text(_page_html(md, p, older, newer), encoding="utf-8")

    # 首页：最新日报 + 近期列表
    recent = "".join(f'<li><a href="{p.href}">{html.escape(p.label)}</a><span class="chip">日报</span></li>'
                     for p in daily[1:11])
    wk = "".join(f'<li><a href="{p.href}">{html.escape(p.label)}</a><span class="chip">周报</span></li>'
                 for p in weekly[:3])
    if daily:
        latest = daily[0]
        body = (f'<p class="crumb">最新一期 · <a href="{latest.href}">{html.escape(latest.label)}</a></p>'
                f'<article class="card">{_render(md, latest.md)}</article>')
    else:
        body = '<article class="card"><p>还没有日报。</p></article>'
    if wk or recent:
        body += f'<div class="section-title">近期</div><div class="card"><ul class="list">{wk}{recent}</ul></div>'
    (out / "index.html").write_text(_layout("FinHOT · 财经热点日报", body, "index", 0), encoding="utf-8")

    # 往期：按月分组的日历卡片
    by_month: dict[str, dict[str, list[Page]]] = {}
    for p in daily:
        by_month.setdefault(p.name[:7], {}).setdefault(p.name[:10], []).append(p)
    parts = ['<h1>往期日报</h1>']
    for month in sorted(by_month, reverse=True):
        y, mth = month.split("-")
        parts.append(f'<div class="month">{y} 年 {int(mth)} 月</div><div class="days">')
        for day in sorted(by_month[month], reverse=True):
            d = datetime.strptime(day, "%Y-%m-%d")
            links = "".join(f'<a href="{p.href}">{EDITION[p.name[10:]][0]}</a>'
                            for p in sorted(by_month[month][day], key=lambda x: x.name))
            parts.append(f'<div class="day"><b>{d:%m-%d} 周{WEEKDAY[d.weekday()]}</b>{links}</div>')
        parts.append("</div>")
    (out / "archive.html").write_text(_layout("往期日报 · FinHOT", "".join(parts), "archive", 0), encoding="utf-8")

    items = "".join(f'<li><a href="{p.name}.html">{html.escape(p.title)}</a></li>' for p in weekly) \
        or "<li>还没有周报。</li>"
    (out / "weekly" / "index.html").write_text(
        _layout("周报 · FinHOT", f'<h1>周报</h1><div class="card"><ul class="list">{items}</ul></div>', "weekly", 1),
        encoding="utf-8")

    # RSS
    base = base_url.rstrip("/")
    entries = sorted([*daily[:30], *weekly[:8]], key=lambda p: p.when, reverse=True)
    rss_items = []
    for p in entries:
        link = f"{base}/{p.href}" if base else p.href
        desc = html.escape(_render(md, _summary_md(p.md)))
        rss_items.append(f"<item><title>{html.escape(p.title)}</title><link>{link}</link><guid>{link}</guid>"
                         f"<pubDate>{format_datetime(p.when)}</pubDate><description>{desc}</description></item>")
    home = f"{base}/index.html" if base else "index.html"
    (out / "feed.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>'
        f"<title>FinHOT 财经热点日报</title><link>{home}</link><description>A股交易参考：财经热点日报与周报</description>"
        f"<language>zh-cn</language><lastBuildDate>{format_datetime(now())}</lastBuildDate>"
        + "".join(rss_items) + "</channel></rss>", encoding="utf-8")
    (out / ".nojekyll").write_text("", encoding="utf-8")
    return out
