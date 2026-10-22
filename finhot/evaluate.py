"""评估：个性化到底有没有用。

1. 反馈报告（python -m finhot eval --profile 我 --days 7）：给了多少、推了多少、延后 / 超上限多少；
   标了反馈的里面“有用 + 已知道”的占比（相关占比）、“不相关”的占比，按栏目、按理由类别分开；
   推送打开率拿不到（群机器人没有回执），只统计点了反馈链接的。
2. 盲标对比（--pool）：个性化排序和通用排序“谁更准”。直接比“我标为有用的条目”有选择偏差（你只看得到个性化排出来的），
   所以把每份简报里两种排序各自的前 N 条合并、打乱，生成一个标注页，标完导入（--labels），算两种排序各自的 precision@N。
3. 按公告类型的事件研究（python -m finhot backtest --announcements）：全市场同类公告之后个股相对中证 1000 的超额，
   样本比“一个人的几十只股票”大得多；结果是公开的统计，个人简报里引用“这类公告的历史表现”。
"""

from __future__ import annotations

import html
import json
import random
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from . import history
from .config import DATA_DIR, REPORTS_DIR
from .models import NewsItem
from .profile import private_dir
from .timeutil import now


# ------------------------------------------------------------------ 1. 反馈报告
def feedback_report(profile: str, days: int = 7, at: datetime | None = None) -> str:
    at = at or now()
    since = at - timedelta(days=days)
    rows = [r for r in history.shown(profile, since) if not r["id"].startswith("brief:")]
    labels = history.feedback(profile, since)
    briefs = [r for r in history.shown(profile, since, channel="brief") if r["id"].startswith("brief:")]
    by_channel = Counter(r["channel"] for r in rows)
    out = [f"# 个性化评估 · {profile} · 最近 {days} 天（{since:%m-%d} ~ {at:%m-%d}）", "",
           f"- 简报 {len(briefs)} 份，简报里给出 {by_channel.get('brief', 0)} 条；即时提醒发出 {by_channel.get('push', 0)} 条，"
           f"延后（免打扰 / 超上限）{by_channel.get('deferred', 0)} 条",
           f"- 有反馈的 {len(labels)} 条（推送打开率拿不到：群机器人没有已读回执，只能统计点了反馈链接的）", ""]
    if not labels:
        return "\n".join(out + ["_还没有反馈。简报和提醒里每条后面有“有用 · 不相关 · 已知道”链接（配了 FINHOT_FEEDBACK_URL 时），"
                                "也可以用 python -m finhot feedback add 记。_", ""])
    kind_of: dict[str, dict] = {}
    for r in rows:
        kind_of.setdefault(r["id"], r)

    def table(key: str, title: str) -> list[str]:
        groups: dict[str, list[str]] = defaultdict(list)
        for item_id, label in labels.items():
            if item_id in kind_of:
                groups[kind_of[item_id].get(key) or "（无）"].append(label)
        lines = [f"## {title}", "", "| 类别 | 反馈 | 相关占比 | 不相关 | 已知道 |", "|---|---:|---:|---:|---:|"]
        for k, ls in sorted(groups.items(), key=lambda kv: -len(kv[1])):
            n = len(ls)
            lines.append(f"| {k} | {n} | {100 * (ls.count('useful') + ls.count('known')) / n:.0f}% | "
                         f"{100 * ls.count('irrelevant') / n:.0f}% | {100 * ls.count('known') / n:.0f}% |")
        return lines + [""]

    all_labels = list(labels.values())
    n = len(all_labels)
    out += [f"**总体**：相关占比 {100 * (all_labels.count('useful') + all_labels.count('known')) / n:.0f}%，"
            f"不相关 {100 * all_labels.count('irrelevant') / n:.0f}%，已知道 {100 * all_labels.count('known') / n:.0f}%"
            f"（{n} 条）", ""]
    out += table("section", "按栏目") + table("reason_kind", "按理由类别（关联强度 × 点名方式）") + table("channel", "按渠道")
    from .personal import default_weights
    learned = history.learned_factors(profile, default_weights())
    if learned:
        out += ["## 反馈带来的权重修正", "", "| 理由类别 | 系数 |", "|---|---:|"]
        out += [f"| {k} | {v:.2f} |" for k, v in sorted(learned.items(), key=lambda kv: kv[1])] + [""]
    return "\n".join(out)


# ------------------------------------------------------------------ 2. 盲标对比
def build_pool(profile: str, days: int = 7, top: int = 10, at: datetime | None = None, seed: int = 7) -> list[dict]:
    """每份简报的候选里，个性化前 top 条和通用前 top 条合并、打乱。"""
    at = at or now()
    cands = history.load_candidates(profile, at - timedelta(days=days))
    by_brief: dict[str, list[dict]] = defaultdict(list)
    for r in cands:
        by_brief[r["brief"]].append(r)
    pool: dict[str, dict] = {}
    for brief, rows in by_brief.items():
        personal = [r for r in sorted(rows, key=lambda r: -r["personal"]) if r["personal"] > 0][:top]
        generic = sorted(rows, key=lambda r: -r["generic"])[:top]
        for rank_name, picked in (("personal", personal), ("generic", generic)):
            for r in picked:
                row = pool.setdefault(r["id"], {**{k: r[k] for k in ("id", "title", "url", "published", "reason")},
                                                "in": {}})
                row["in"].setdefault(brief, []).append(rank_name)
    rows = list(pool.values())
    random.Random(seed).shuffle(rows)
    return rows


def pool_page(rows: list[dict], profile: str) -> str:
    """离线标注页：每条点“和我有关 / 无关”，存在浏览器里，最后导出 JSON。不显示来自哪种排序。"""
    items = "".join(
        f"<li data-id='{html.escape(r['id'])}'><a href='{html.escape(r['url'] or '#')}' target=_blank>{html.escape(r['title'])}</a>"
        f"<div><label><input type=radio name='{html.escape(r['id'])}' value=useful>和我有关</label> "
        f"<label><input type=radio name='{html.escape(r['id'])}' value=irrelevant>无关</label></div></li>" for r in rows)
    return f"""<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>
<title>盲标 · {html.escape(profile)}</title>
<style>body{{font:15px/1.6 system-ui;max-width:46em;margin:auto;padding:1em}}li{{margin:.8em 0}}label{{margin-right:1em;color:#555}}</style>
<h1>哪些和你有关？</h1><p>共 {len(rows)} 条，顺序是打乱的，看不出来自哪种排序。标完点最下面的“导出”。</p>
<ol>{items}</ol><button onclick="exportLabels()">导出</button><pre id=out></pre>
<script>
const KEY='finhot-pool-{html.escape(profile)}';
let saved={{}};try{{saved=JSON.parse(localStorage.getItem(KEY)||'{{}}')}}catch(e){{}}
document.querySelectorAll('input[type=radio]').forEach(el=>{{if(saved[el.name]===el.value)el.checked=true;
el.addEventListener('change',()=>{{saved[el.name]=el.value;try{{localStorage.setItem(KEY,JSON.stringify(saved))}}catch(e){{}}}})}});
function exportLabels(){{const t=JSON.stringify(saved);document.getElementById('out').textContent=t;
const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([t],{{type:'application/json'}}));a.download='labels.json';a.click()}}
</script>"""


def write_pool(profile: str, days: int = 7, top: int = 10) -> tuple[Path, int]:
    rows = build_pool(profile, days, top)
    folder = private_dir() / "eval" / profile
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "pool.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    page = folder / "pool.html"
    page.write_text(pool_page(rows, profile), encoding="utf-8")
    return page, len(rows)


def compare(rows: list[dict], labels: dict[str, str]) -> dict[str, dict]:
    """两种排序各自的 precision@N：每份简报里它选出的条目中，标为“和我有关”的比例（只算标过的）。"""
    out = {}
    for name in ("personal", "generic"):
        hits = total = 0
        for r in rows:
            if r["id"] not in labels:
                continue
            times = sum(1 for kinds in r["in"].values() if name in kinds)
            total += times
            hits += times * (labels[r["id"]] == "useful")
        out[name] = {"labeled": total, "useful": hits, "precision": round(hits / total * 100, 1) if total else None}
    return out


# ------------------------------------------------------------------ 3. 按公告类型的事件研究
@dataclass
class Event:
    code: str
    kind: str
    at: datetime
    title: str


def load_events(data_dir: Path = DATA_DIR, since: date | None = None) -> list[Event]:
    """data/ 里存下的全市场公告（白名单类型），同一家公司同一天同一类型只算一次。"""
    seen: set[tuple[str, str, str]] = set()
    out = []
    for path in sorted(data_dir.glob("20??-??-??/announcements*.jsonl")):
        if since and path.parent.name < since.isoformat():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            it = NewsItem.from_dict(json.loads(line))
            if not it.codes or not it.tags:
                continue
            key = (it.codes[0], it.tags[0], it.published.date().isoformat())
            if key not in seen:
                seen.add(key)
                out.append(Event(it.codes[0], it.tags[0], it.published, it.content or it.title))
    return out


def event_returns(events: list[Event], prices: dict, benchmark: list, horizons=(1, 3)) -> list[dict]:
    """每个事件：反应口径（事件前最后一个收盘 → 第 k 个交易日收盘）和可交易口径（事件后第一个开盘 → 第 k 天收盘）的超额（%）。"""
    from .backtest import _window
    calendar = [b.day for b in benchmark]
    bench = {b.day: b for b in benchmark}
    out = []
    for e in events:
        bars = {b.day: b for b in prices.get(e.code) or []}
        base, entry = _window(e.at, calendar)
        row = {"code": e.code, "kind": e.kind, "at": e.at.isoformat(), "title": e.title[:60]}
        for k in horizons:
            row[f"reaction_{k}"] = row[f"tradable_{k}"] = None
            if base is not None and base + k < len(calendar):
                a, b = bars.get(calendar[base]), bars.get(calendar[base + k])
                if a and b and a.close:
                    m = bench[calendar[base + k]].close / bench[calendar[base]].close - 1
                    row[f"reaction_{k}"] = round((b.close / a.close - 1 - m) * 100, 2)
            if entry is not None and entry + k - 1 < len(calendar):
                a, b = bars.get(calendar[entry]), bars.get(calendar[entry + k - 1])
                if a and b and a.open:
                    m = bench[calendar[entry + k - 1]].close / bench[calendar[entry]].open - 1
                    row[f"tradable_{k}"] = round((b.close / a.open - 1 - m) * 100, 2)
        out.append(row)
    return out


def summarize_events(rows: list[dict], horizons=(1, 3)) -> list[dict]:
    out = []
    for kind in sorted({r["kind"] for r in rows}):
        group = [r for r in rows if r["kind"] == kind]
        row = {"kind": kind, "n": len(group)}
        for label in ("reaction", "tradable"):
            for k in horizons:
                vals = [r[f"{label}_{k}"] for r in group if r.get(f"{label}_{k}") is not None]
                row[f"{label}_{k}_n"] = len(vals)
                row[f"{label}_{k}_mean"] = round(statistics.mean(vals), 2) if vals else None
                row[f"{label}_{k}_up"] = round(sum(v > 0 for v in vals) / len(vals) * 100) if vals else None
        out.append(row)
    return sorted(out, key=lambda r: -r["n"])


def render_events(summary: list[dict], generated: datetime, errors: list[str], n_events: int) -> str:
    def cell(r, label, k):
        n = r[f"{label}_{k}_n"]
        return "—" if not n else f"{r[f'{label}_{k}_mean']:+.2f}% · {r[f'{label}_{k}_up']}%<br><sub>{n} 次</sub>"
    out = [f"# 公告类型的事件研究（{generated:%Y-%m-%d}）", "",
           "> 全市场按类型过滤后的公告（data/*/announcements*.jsonl），公告之后个股相对中证 1000 的超额收益。格子里是"
           "“平均超额 · 跑赢的比例”。反应口径：公告前最后一个收盘 → 第 N 个交易日收盘；可交易口径：公告后第一个开盘买入 → "
           "第 N 个交易日收盘。只是历史统计，样本少时波动很大，不构成投资建议。", "",
           f"共 {n_events} 个事件。", "",
           "| 公告类型 | 事件 | 反应 T+1 | 反应 T+3 | 可交易 T+1 | 可交易 T+3 |", "|---|---:|---|---|---|---|"]
    for r in summary:
        out.append(f"| {r['kind']} | {r['n']} | {cell(r, 'reaction', 1)} | {cell(r, 'reaction', 3)} | "
                   f"{cell(r, 'tradable', 1)} | {cell(r, 'tradable', 3)} |")
    if errors:
        out += ["", "<details><summary>说明</summary>", ""] + [f"- {e}" for e in errors[:20]] + ["", "</details>"]
    return "\n".join(out + [""])


def run_event_study(days: int = 90, data_dir: Path = DATA_DIR, out_dir: Path | None = None, source=None,
                    max_stocks: int = 400) -> Path:
    from .backtest import BENCHMARK, PriceSource, PricesUnavailable
    from .entities import secid
    generated = now()
    events = load_events(data_dir, generated.date() - timedelta(days=days))
    source = source or PriceSource()
    errors: list[str] = []
    rows: list[dict] = []
    if events:
        first = min(e.at for e in events).date() - timedelta(days=10)
        cutoff = generated.date() if generated.time().hour >= 15 else generated.date() - timedelta(days=1)
        try:
            benchmark = [b for b in source.bars(BENCHMARK[0], first) if b.day <= cutoff]
        except Exception as e:  # noqa: BLE001
            raise PricesUnavailable(f"{BENCHMARK[1]}日 K 取不到：{type(e).__name__}") from e
        codes = [c for c, _ in Counter(e.code for e in events).most_common(max_stocks)]
        if len(codes) < len({e.code for e in events}):
            errors.append(f"涉及 {len({e.code for e in events})} 只股票，只取了公告最多的 {len(codes)} 只的日 K")
        prices = {}
        misses = 0
        for code in codes:
            if misses >= 5:
                errors.append("个股日 K 连续 5 只取不到，其余没再试")
                break
            try:
                prices[code] = [b for b in source.bars(secid(code), first) if b.day <= cutoff]
                misses = 0
            except Exception as e:  # noqa: BLE001
                misses += 1
                errors.append(f"{code} 日 K 取不到：{type(e).__name__}")
        rows = event_returns([e for e in events if e.code in prices], prices, benchmark)
    summary = summarize_events(rows)
    reports = (out_dir or REPORTS_DIR) / "backtest"
    reports.mkdir(parents=True, exist_ok=True)
    path = reports / "announcements.md"
    path.write_text(render_events(summary, generated, errors, len(rows)), encoding="utf-8")
    ddir = (out_dir / "data" if out_dir else data_dir) / "backtest"
    ddir.mkdir(parents=True, exist_ok=True)
    (ddir / "announcements.json").write_text(json.dumps({"generated": generated.isoformat(), "rows": summary},
                                                        ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def event_stats(data_dir: Path = DATA_DIR) -> dict[str, dict]:
    """存下的事件研究结果：{公告类型: 汇总行}（个人简报和周报引用）。"""
    try:
        doc = json.loads((data_dir / "backtest" / "announcements.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {r["kind"]: r for r in doc.get("rows") or []}


# ------------------------------------------------------------------ 我的周报
def weekly_personal(profile, at: datetime | None = None, days: int = 7, data_dir: Path | None = None) -> str:
    """我的周报：你的自选本周发生了什么（给过你的条目按股票归拢）+ 关注题材的信号战绩 + 本周出现过的公告类型的历史表现。"""
    from .backtest import KINDS
    from .config import public_data_dir
    from .entities import book
    data_dir = data_dir or public_data_dir()
    at = at or now()
    since = at - timedelta(days=days)
    b = book()
    rows = [r for r in history.shown(profile.name, since) if not r["id"].startswith("brief:")]
    out = [f"# 我的周报 · {profile.name} · {since:%m-%d} ~ {at:%m-%d}", ""]
    by_code: dict[str, dict[str, dict]] = defaultdict(dict)
    for r in rows:
        if r.get("code") and r.get("section") in ("持仓", "自选", "关联公司"):
            by_code[r["code"]].setdefault(r["id"], r)
    out += ["## 你的自选本周发生了什么", ""]
    if not by_code:
        out += ["_本周没有给过你和持仓、自选直接有关的消息。_", ""]
    for code in [c for c in profile.codes if c in by_code] + [c for c in by_code if c not in profile.codes]:
        tag = profile.relation_of(code) or "关联"
        out.append(f"### {tag} {b.label(code)} · {len(by_code[code])} 条")
        for r in sorted(by_code[code].values(), key=lambda r: r["at"]):
            title = f"[{r['title']}]({r['url']})" if r.get("url") else r["title"]
            out.append(f"- {r['at'][5:10]} {title}" + ("（即时提醒）" if r["channel"] == "push" else ""))
        out.append("")
    # 关注题材的信号战绩（公开的回测结果里挑你的题材）
    outcomes = []
    path = data_dir / "backtest" / "outcomes.jsonl"
    if path.exists():
        outcomes = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    mine = [o for o in outcomes if o["signal"]["theme"] in profile.themes]
    if profile.themes:
        out += ["## 关注题材：这类信号过去灵不灵", "",
                "> 来自公开的信号战绩（reports/backtest/latest.md），只挑你关注的题材；“反应 T+1”含开盘跳空，"
                "“可交易 T+1”是第二天开盘买入的口径。样本少，仅供参考。", "",
                "| 题材 | 信号 | 次数 | 反应 T+1 | 可交易 T+1 |", "|---|---|---:|---:|---:|"]
        groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
        for o in mine:
            groups[(o["signal"]["theme"], o["signal"]["kind"])].append(o)
        if not groups:
            out.append("| — | 还没有这些题材的信号 | | | |")
        for (theme, kind), os_ in sorted(groups.items(), key=lambda kv: (kv[0][0], list(KINDS).index(kv[0][1])
                                                                         if kv[0][1] in KINDS else 99)):
            def mean(key):
                vals = [o[key].get("1") for o in os_ if o[key].get("1") is not None]
                return f"{statistics.mean(vals):+.2f}%（{len(vals)}）" if vals else "—"
            out.append(f"| {theme} | {kind} | {len(os_)} | {mean('reaction')} | {mean('tradable')} |")
        out.append("")
    stats = event_stats(data_dir)
    from .announce import classify_type
    ann_types = {classify_type(r["title"]).name for r in rows if str(r.get("reason_kind", "")).endswith("公告")}
    if stats:
        out += ["## 本周你收到的公告类型：全市场的历史表现", "", "| 类型 | 事件 | 反应 T+1 | 可交易 T+1 |", "|---|---:|---:|---:|"]
        for k, r in stats.items():
            if ann_types and k not in ann_types:
                continue
            f = lambda m, u, n: "—" if not n else f"{m:+.2f}%（跑赢 {u}%）"  # noqa: E731
            out.append(f"| {k} | {r['n']} | {f(r['reaction_1_mean'], r['reaction_1_up'], r['reaction_1_n'])} | "
                       f"{f(r['tradable_1_mean'], r['tradable_1_up'], r['tradable_1_n'])} |")
        out.append("")
    out += ["---", "", "*以上是与你有关的公开信息整理和历史统计，不构成任何投资建议。*", ""]
    return "\n".join(out)
