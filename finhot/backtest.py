"""回测与战绩：拿 data/ 里当时存下的信号，看对应题材之后几个交易日的表现。

信号都来自日报生成那一刻存下的数据，没有用事后知识：
- 利好新闻 / 利空新闻：模型标了方向、模型分 ≥ 7 的新闻，按它点名的板块归到题材；
- 共振 / 资金先行 / 新闻热·资金冷：日报“新闻 × 资金”表的判断；
- 涨停主线：当天涨停 ≥ 3 家的题材（看第二天还能不能延续）。
同一天、同一题材、同一类信号只算一次（十条新闻说同一件事不该算十次）。

价格：东方财富行业 / 概念板块日 K，题材收益取它对应的几个板块的中位数；基准是中证 1000。
两种口径：
- 反应：信号前最后一个收盘 → 之后第 1/3/5 个交易日收盘。含开盘跳空，看消息有没有被市场认可；
- 可交易：信号之后第一个开盘买入 → 第 1/3/5 个交易日收盘。读者真正拿得到的部分。
超额 = 题材收益 − 基准收益。看多类信号超额 > 0 算对，利空超额 < 0 算对；“资金先行”等不预设方向的只统计不判对错。
"""

from __future__ import annotations

import json
import logging
import statistics
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path

from .config import DATA_DIR, REPORTS_DIR
from .http import Session
from .models import NewsItem
from .themes import board_themes, item_themes
from .timeutil import TZ, now

log = logging.getLogger(__name__)

HORIZONS = (1, 3, 5)
BENCHMARK = ("1.000852", "中证1000")
MIN_LLM_SCORE = 7.0
MAX_BOARDS = 3
OPEN, CLOSE = time(9, 30), time(15, 0)

# 信号类别 → 预设方向（True 看多、False 看空、None 不判对错）
KINDS = {"利好新闻": True, "利空新闻": False, "共振": True, "资金先行": None, "新闻热·资金冷": False, "涨停主线": True}


@dataclass
class Signal:
    kind: str
    at: datetime
    theme: str
    title: str
    count: int = 1     # 合并进来的同类信号条数


@dataclass
class Bar:
    day: date
    open: float
    close: float


@dataclass
class Outcome:
    signal: Signal
    boards: list[str] = field(default_factory=list)
    reaction: dict[int, float | None] = field(default_factory=dict)   # k → 超额（%）
    tradable: dict[int, float | None] = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["signal"]["at"] = self.signal.at.isoformat()
        return d


# ------------------------------------------------------------------ 信号
def _session_key(at: datetime) -> str:
    """把信号归到它第一次能影响的交易日（收盘后的算到下一天；周末节假日在算收益时再顺延）。"""
    d = at.astimezone(TZ)
    return (d.date() + timedelta(days=1)).isoformat() if d.time() >= CLOSE else d.date().isoformat()


def load_signals(data_dir: Path = DATA_DIR, since: date | None = None) -> list[Signal]:
    merged: dict[tuple[str, str, str], Signal] = {}

    def add(kind: str, at: datetime, theme: str, title: str) -> None:
        key = (kind, theme, _session_key(at))
        if key in merged:
            merged[key].count += 1
        else:
            merged[key] = Signal(kind, at, theme, title)

    for day_dir in sorted(p for p in data_dir.iterdir() if p.is_dir() and p.name[:2] == "20"):
        try:
            if since and date.fromisoformat(day_dir.name) < since:
                continue
        except ValueError:
            continue
        for path in sorted(day_dir.glob("items*.jsonl")):
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                it = NewsItem.from_dict(json.loads(line))
                llm = it.llm or {}
                if llm.get("direction") not in ("利好", "利空") or float(llm.get("score") or 0) < MIN_LLM_SCORE:
                    continue
                for theme in item_themes(it)[:2]:
                    add(f"{llm['direction']}新闻", it.published, theme, llm.get("title") or it.title)
        for path in sorted(day_dir.glob("meta*.json")):
            meta = json.loads(path.read_text(encoding="utf-8"))
            at = datetime.fromisoformat(meta["until"]) if meta.get("until") else None
            if not at:
                continue
            for r in meta.get("themes") or []:
                if r.get("verdict") in KINDS:
                    add(r["verdict"], at, r["theme"], f"新闻 {r.get('news', 0)} 条，涨停 {r.get('zt', 0)} 家")
        for path in sorted(day_dir.glob("limitup*.json")):
            lu = json.loads(path.read_text(encoding="utf-8"))
            if not lu.get("trade_date"):
                continue
            at = datetime.combine(date.fromisoformat(lu["trade_date"]), CLOSE, TZ)  # 收盘时才知道谁涨停
            counts: dict[str, list[str]] = defaultdict(list)
            for s in lu.get("stocks") or []:
                if s.get("themes"):
                    counts[s["themes"][0]].append(s["name"])
            for theme, names in counts.items():
                if len(names) >= 3:
                    add("涨停主线", at, theme, f"涨停 {len(names)} 家：{'、'.join(names[:4])}")
    return sorted(merged.values(), key=lambda s: s.at)


# ------------------------------------------------------------------ 价格
def parse_klines(data: dict) -> list[Bar]:
    out = []
    for k in ((data.get("data") or {}).get("klines")) or []:
        f = k.split(",")
        try:
            out.append(Bar(date.fromisoformat(f[0]), float(f[1]), float(f[2])))
        except (ValueError, IndexError):
            continue
    return out


def parse_board_list(data: dict, kind: str) -> list[dict]:
    return [{"code": d.get("f12", ""), "name": d.get("f14", ""), "kind": kind}
            for d in ((data.get("data") or {}).get("diff") or []) if d.get("f12")]


def theme_boards(themes: set[str], boards: list[dict]) -> dict[str, list[dict]]:
    """每个题材对应的板块：先用行业板块，没有再用概念板块，最多 MAX_BOARDS 个。"""
    out: dict[str, list[dict]] = {}
    for theme in themes:
        ind = [b for b in boards if b["kind"] == "industry" and theme in board_themes(b["name"])]
        con = [b for b in boards if b["kind"] == "concept" and theme in board_themes(b["name"])]
        picked = (ind or con)[:MAX_BOARDS]
        if picked:
            out[theme] = picked
    return out


class PriceSource:
    """东方财富日 K（push2his，带备用域名）与板块列表。"""

    def __init__(self, session: Session | None = None):
        from .ashare import PUSH2_HOSTS, PUSH2HIS_HOSTS, push2_json
        self.session = session or Session()
        self._json, self._hosts, self._his = push2_json, PUSH2_HOSTS, PUSH2HIS_HOSTS

    def boards(self) -> list[dict]:
        out = []
        for fs, kind in (("m:90+t:2", "industry"), ("m:90+t:3", "concept")):
            for pn in range(1, 8):
                data = self._json(self.session, "/api/qt/clist/get", {
                    "pn": str(pn), "pz": "100", "po": "1", "np": "1", "fltt": "2", "invt": "2", "fid": "f3",
                    "fs": fs, "fields": "f12,f14"}, hosts=self._hosts)
                rows = parse_board_list(data, kind)
                out += rows
                if len(rows) < 100:
                    break
        return out

    def bars(self, secid: str, start: date) -> list[Bar]:
        data = self._json(self.session, "/api/qt/stock/kline/get", {
            "secid": secid, "fields1": "f1,f2,f3", "fields2": "f51,f52,f53", "klt": "101", "fqt": "0",
            "beg": start.strftime("%Y%m%d"), "end": "20500101", "ut": "fa5fd1943c7b386f172d6893dbfba10b"},
            hosts=self._his)
        return parse_klines(data)


# ------------------------------------------------------------------ 计算
def _window(at: datetime, calendar: list[date]) -> tuple[int | None, int | None]:
    """(反应口径的基准收盘下标, 可交易口径的买入交易日下标)。"""
    d, t = at.astimezone(TZ).date(), at.astimezone(TZ).time()
    base = None
    for i, day in enumerate(calendar):
        if day < d or (day == d and t >= CLOSE):
            base = i
    entry = next((i for i, day in enumerate(calendar) if day > d or (day == d and t < OPEN)), None)
    return base, entry


def _median_return(series: list[dict[date, Bar]], start: date, start_open: bool, end: date) -> float | None:
    vals = []
    for bars in series:
        a, b = bars.get(start), bars.get(end)
        if a and b:
            base = a.open if start_open else a.close
            if base:
                vals.append(b.close / base - 1)
    return statistics.median(vals) if vals else None


def evaluate(signals: list[Signal], prices: dict[str, list[Bar]], benchmark: list[Bar],
             mapping: dict[str, list[dict]]) -> list[Outcome]:
    calendar = [b.day for b in benchmark]
    bench = {b.day: b for b in benchmark}
    series = {code: {b.day: b for b in bars} for code, bars in prices.items()}
    out = []
    for s in signals:
        boards = mapping.get(s.theme) or []
        sel = [series[b["code"]] for b in boards if b["code"] in series]
        o = Outcome(s, boards=[b["name"] for b in boards])
        base, entry = _window(s.at, calendar)
        for k in HORIZONS:
            o.reaction[k] = o.tradable[k] = None
            if sel and base is not None and base + k < len(calendar):
                r = _median_return(sel, calendar[base], False, calendar[base + k])
                m = bench[calendar[base + k]].close / bench[calendar[base]].close - 1
                o.reaction[k] = None if r is None else round((r - m) * 100, 2)
            if sel and entry is not None and entry + k - 1 < len(calendar):
                end = calendar[entry + k - 1]
                r = _median_return(sel, calendar[entry], True, end)
                m = bench[end].close / bench[calendar[entry]].open - 1
                o.tradable[k] = None if r is None else round((r - m) * 100, 2)
        out.append(o)
    return out


def summarize(outcomes: list[Outcome]) -> list[dict]:
    """按信号类别汇总：样本数、平均超额、胜率（对的比例）。"""
    rows = []
    for kind, bullish in KINDS.items():
        group = [o for o in outcomes if o.signal.kind == kind]
        if not group:
            continue
        row = {"kind": kind, "n": len(group), "bullish": bullish}
        for label, attr in (("reaction", "reaction"), ("tradable", "tradable")):
            for k in HORIZONS:
                vals = [getattr(o, attr)[k] for o in group if getattr(o, attr).get(k) is not None]
                row[f"{label}_{k}_n"] = len(vals)
                row[f"{label}_{k}_mean"] = round(statistics.mean(vals), 2) if vals else None
                if bullish is not None and vals:
                    hits = sum(1 for v in vals if (v > 0) == bullish)
                    row[f"{label}_{k}_win"] = round(hits / len(vals) * 100)
                else:
                    row[f"{label}_{k}_win"] = None
        rows.append(row)
    return rows


# ------------------------------------------------------------------ 报告
def _cell(mean, win, n) -> str:
    if not n:
        return "—"
    s = f"{mean:+.2f}%"
    return s + (f" · {win}%" if win is not None else "") + f"<br><sub>{n} 次</sub>"


def render(outcomes: list[Outcome], summary: list[dict], generated: datetime, start: date | None,
           errors: list[str]) -> str:
    span = f"{start.isoformat()} 起" if start else "全部"
    out = [f"# 信号战绩（{generated:%Y-%m-%d}）", "",
           f"> 统计 {span}日报存下的信号，对应题材（几个东方财富板块的中位数）相对中证 1000 的超额收益。"
           "格子里是“平均超额 · 胜率”，胜率=方向说对的比例；“资金先行”不预设方向，只看平均超额。"
           "样本少时数字波动很大，仅供参考，不构成投资建议。", ""]
    if not outcomes:
        return "\n".join(out + ["_还没有可评估的信号。_", ""])
    for label, attr, note in (("反应", "reaction", "信号前最后一个收盘 → 第 N 个交易日收盘，含开盘跳空"),
                              ("可交易", "tradable", "信号后第一个开盘买入 → 第 N 个交易日收盘")):
        out += [f"## {label}口径", "", f"> {note}。", "",
                "| 信号 | 样本 | T+1 | T+3 | T+5 |", "|---|---:|---|---|---|"]
        for r in summary:
            cells = [_cell(r[f"{attr}_{k}_mean"], r[f"{attr}_{k}_win"], r[f"{attr}_{k}_n"]) for k in HORIZONS]
            out.append(f"| {r['kind']} | {r['n']} | " + " | ".join(cells) + " |")
        out.append("")

    by_theme: dict[str, list[float]] = defaultdict(list)
    for o in outcomes:
        if o.signal.kind == "利好新闻" and o.reaction.get(3) is not None:
            by_theme[o.signal.theme].append(o.reaction[3])
    ranked = sorted(((t, statistics.mean(v), len(v)) for t, v in by_theme.items() if len(v) >= 2), key=lambda x: -x[1])
    if ranked:
        out += ["## 利好新闻：哪些题材兑现得好（反应口径 T+3，至少 2 次）", "",
                "| 题材 | 平均超额 | 次数 |", "|---|---:|---:|"]
        out += [f"| {t} | {m:+.2f}% | {n} |" for t, m, n in ranked[:8]]
        if len(ranked) > 8:
            out += [f"| … | | |"] + [f"| {t} | {m:+.2f}% | {n} |" for t, m, n in ranked[-3:]]
        out.append("")

    recent = sorted(outcomes, key=lambda o: o.signal.at, reverse=True)[:15]
    out += ["## 最近的信号", "", "| 时间 | 信号 | 题材 | 内容 | 反应 T+1 | T+3 | 可交易 T+1 |", "|---|---|---|---|---:|---:|---:|"]
    for o in recent:
        f = lambda v: "—" if v is None else f"{v:+.2f}%"  # noqa: E731
        title = o.signal.title.replace("|", "/")[:32] + (f"（{o.signal.count} 条）" if o.signal.count > 1 else "")
        out.append(f"| {o.signal.at.astimezone(TZ):%m-%d %H:%M} | {o.signal.kind} | {o.signal.theme} | {title} | "
                   f"{f(o.reaction.get(1))} | {f(o.reaction.get(3))} | {f(o.tradable.get(1))} |")
    out.append("")
    unmapped = sorted({o.signal.theme for o in outcomes if not o.boards})
    if unmapped or errors:
        out += ["<details><summary>说明</summary>", ""]
        if unmapped:
            out.append(f"- 没有对应板块、未计入：{'、'.join(unmapped)}")
        out += [f"- {e}" for e in errors]
        out += ["", "</details>", ""]
    return "\n".join(out)


def run_backtest(days: int = 90, out_dir: Path | None = None, data_dir: Path = DATA_DIR,
                 source: PriceSource | None = None) -> Path:
    generated = now()
    start = generated.date() - timedelta(days=days)
    signals = load_signals(data_dir, since=start)
    source = source or PriceSource()
    errors: list[str] = []
    outcomes: list[Outcome] = []
    if signals:
        first = min(s.at for s in signals).date() - timedelta(days=10)
        benchmark = source.bars(BENCHMARK[0], first)
        mapping = theme_boards({s.theme for s in signals}, source.boards())
        prices: dict[str, list[Bar]] = {}
        for code in sorted({b["code"] for bs in mapping.values() for b in bs}):
            try:
                prices[code] = source.bars(f"90.{code}", first)
            except Exception as e:  # noqa: BLE001 —— 单个板块失败不影响整体
                errors.append(f"板块 {code} 日 K 获取失败：{type(e).__name__}")
        outcomes = evaluate(signals, prices, benchmark, mapping)
    summary = summarize(outcomes)
    md = render(outcomes, summary, generated, start, errors)

    reports = (out_dir or REPORTS_DIR) / "backtest"
    reports.mkdir(parents=True, exist_ok=True)
    path = reports / "latest.md"
    path.write_text(md, encoding="utf-8")
    ddir = (out_dir / "data" if out_dir else data_dir) / "backtest"
    ddir.mkdir(parents=True, exist_ok=True)
    (ddir / "outcomes.jsonl").write_text("".join(json.dumps(o.to_dict(), ensure_ascii=False) + "\n" for o in outcomes),
                                         encoding="utf-8")
    (ddir / "summary.json").write_text(json.dumps({"generated": generated.isoformat(), "rows": summary},
                                                  ensure_ascii=False, indent=1), encoding="utf-8")
    log.info("回测：%d 个信号，报告 %s", len(outcomes), path)
    return path
