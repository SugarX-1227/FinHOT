"""命令行入口。

  python -m finhot run                 # 生成日报（窗口：上一份日报截止 → 现在；无历史则 24 小时）
  python -m finhot run --hours 12 --no-llm --edition close
  python -m finhot auto                # 定时任务入口：判断该出哪一版，已出过则跳过；周六顺带出周报
  python -m finhot weekly              # 生成本周周报
  python -m finhot site                # 生成静态网站到 _site/
  python -m finhot market              # 在终端打印 A 股盘面快照与今日关注
  python -m finhot sources             # 信源健康检查（每个源只抓一页）
  python -m finhot collect --hours 2   # 只采集并打印，不生成日报
  python -m finhot push                # 常驻：每 90 秒把快讯推给网站引擎（web/），见 finhot/push.py
  python -m finhot push --once --dry-run   # 只跑一轮、打印会推哪些，不真的推
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import timedelta
from pathlib import Path

from .collect import collect
from .config import DATA_DIR, load_sources
from .dedup import dedup
from .scoring import score_all
from .timeutil import now


def _only(v: str | None) -> list[str] | None:
    return [s.strip() for s in v.split(",") if s.strip()] if v else None


def cmd_sources(args) -> int:
    cfg = load_sources()
    results = collect(cfg["sources"], now() - timedelta(hours=args.hours), only=_only(args.only), max_pages=1)
    print(f"\n{'信源':<14}{'状态':<6}{'条数':>5}  {'耗时':>6}  样例 / 错误")
    for r in results:
        sample = r.items[0] if r.items else None
        detail = r.error or (f"[{sample.published:%m-%d %H:%M}] {sample.title[:40]}" if sample else "（窗口内无数据）")
        print(f"{r.source_id:<14}{'OK' if r.ok else 'FAIL':<6}{len(r.items):>5}  {r.seconds:>5.1f}s  {detail}")
    ok = sum(r.ok for r in results)
    print(f"\n{ok}/{len(results)} 个信源正常")
    return 0 if ok else 1


def cmd_collect(args) -> int:
    cfg = load_sources()
    results = collect(cfg["sources"], now() - timedelta(hours=args.hours), only=_only(args.only))
    raw = [it for r in results for it in r.items]
    items = score_all(dedup(raw))
    print(f"\n原始 {len(raw)} 条 → 去重后 {len(items)} 条。得分前 {args.top}：")
    for it in items[: args.top]:
        extra = f" (+{len(it.dup_sources)}源)" if it.dup_sources else ""
        print(f"{it.score:5.1f} [{it.category}] {it.published:%m-%d %H:%M} {it.source_name}{extra}：{it.title[:60]}")
    if args.save:
        path = DATA_DIR / "raw" / f"{now():%Y%m%d-%H%M%S}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            for it in items:
                f.write(json.dumps(it.to_dict(), ensure_ascii=False) + "\n")
        print(f"已保存 {path}")
    return 0


def cmd_run(args) -> int:
    from .pipeline import run
    path = run(hours=args.hours, use_llm=not args.no_llm, only=_only(args.only), keep=args.keep,
               out_dir=Path(args.out) if args.out else None, edition=args.edition, market=not args.no_market)
    print(path)
    return 0


def cmd_auto(args) -> int:
    """定时任务入口。GitHub Actions 定时触发常延迟数小时，所以每个时段放多个触发点，由这里去重。"""
    from .editions import scheduled_slot
    from .pipeline import run
    from .weekly import run_weekly, weekly_exists

    t = now()
    did, failed = False, False
    ed = scheduled_slot(t)
    if ed:
        print(f"[auto] {t:%m-%d %H:%M} 生成{ed.name}")
        print(run(use_llm=not args.no_llm, edition=ed.key))
        did = True
    # 周六/周日：本周周报还没出就出一份。周报失败不能影响已生成的日报被提交，所以单独兜住
    if t.weekday() >= 5 and t.hour >= 6 and not weekly_exists(t.date()):
        print("[auto] 生成本周周报")
        try:
            print(run_weekly(t.date(), use_llm=not args.no_llm))
            did = True
        except (Exception, SystemExit) as e:  # noqa: BLE001
            print(f"::warning::周报生成失败：{e}")
            failed = True
    if not did and not failed:
        print(f"[auto] {t:%m-%d %H:%M} 当前时段无需生成（已生成或不在时段内）")
    return 0


def cmd_weekly(args) -> int:
    from datetime import date

    from .weekly import run_weekly
    day = date.fromisoformat(args.date) if args.date else None
    print(run_weekly(day, use_llm=not args.no_llm, out_dir=Path(args.out) if args.out else None))
    return 0


def cmd_site(args) -> int:
    from .site import build_site
    print(build_site(Path(args.out), base_url=args.base_url))
    return 0


def cmd_market(args) -> int:
    from datetime import timedelta as td

    from .ashare import fetch_snapshot
    from .calendar_watch import fetch_watchlist
    from .report import _render_ashare, _render_watch
    snap = fetch_snapshot()
    print(f"# A股盘面（{snap.trade_date}）\n")
    print("\n".join(_render_ashare(snap)))
    w = fetch_watchlist(now() + td(days=args.days_ahead))
    print(f"# 关注（{w.day}）\n")
    print("\n".join(_render_watch(w)) or "（无）")
    for e in [*snap.errors, *w.errors]:
        print("⚠️", e)
    return 0


def cmd_push(args) -> int:
    from .push import PushConfig, loop

    if not args.verbose:
        logging.getLogger("finhot.collect").setLevel(logging.WARNING)  # 每轮每个信源一行，常驻时太吵
    cfg = PushConfig.from_env()
    if args.min_score is not None:
        cfg.min_score = args.min_score
    if args.only:
        cfg.sources = _only(args.only)
    return loop(cfg, once=args.once, dry_run=args.dry_run)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="finhot", description="FinHOT 财经热点采集与日报")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("sources", help="信源健康检查")
    s.add_argument("--hours", type=float, default=24)
    s.add_argument("--only", help="只检查指定信源，逗号分隔")
    s.set_defaults(func=cmd_sources)

    c = sub.add_parser("collect", help="采集并在终端打印排序结果")
    c.add_argument("--hours", type=float, default=6)
    c.add_argument("--only")
    c.add_argument("--top", type=int, default=30)
    c.add_argument("--save", action="store_true", help="保存到 data/raw/")
    c.set_defaults(func=cmd_collect)

    r = sub.add_parser("run", help="完整流程：生成日报")
    r.add_argument("--hours", type=float, default=None,
                   help="采集最近多少小时（默认：接着上一份日报，夹在 6~72 小时之间；无历史则 24 小时）")
    r.add_argument("--edition", choices=["pre", "noon", "close"], help="版次（默认按当前时间）")
    r.add_argument("--no-llm", action="store_true", help="不调用 LLM，只用规则")
    r.add_argument("--no-market", action="store_true", help="不抓 A 股盘面与今日关注")
    r.add_argument("--only")
    r.add_argument("--keep", type=int, default=400, help="items.jsonl 最多保存多少条")
    r.add_argument("--out", help="输出目录（默认写入仓库 reports/ 和 data/）")
    r.set_defaults(func=cmd_run)

    a = sub.add_parser("auto", help="定时任务入口：按时段生成日报/周报，已生成则跳过")
    a.add_argument("--no-llm", action="store_true")
    a.set_defaults(func=cmd_auto)

    w = sub.add_parser("weekly", help="生成周报")
    w.add_argument("--date", help="周内任意一天 YYYY-MM-DD（默认今天所在周）")
    w.add_argument("--no-llm", action="store_true")
    w.add_argument("--out", help="输出目录（默认 reports/）")
    w.set_defaults(func=cmd_weekly)

    st = sub.add_parser("site", help="生成静态网站")
    st.add_argument("--out", default="_site")
    st.add_argument("--base-url", default="", help="站点根路径，如 /FinHOT（用于 RSS 绝对链接，可留空）")
    st.set_defaults(func=cmd_site)

    m = sub.add_parser("market", help="打印 A 股盘面快照与今日关注")
    m.add_argument("--days-ahead", type=int, default=0, help="关注日期偏移（1=明天）")
    m.set_defaults(func=cmd_market)

    pu = sub.add_parser("push", help="常驻：把快讯推给网站引擎")
    pu.add_argument("--once", action="store_true", help="只跑一轮")
    pu.add_argument("--dry-run", action="store_true", help="只打印会推哪些，不推送")
    pu.add_argument("--min-score", type=float, help="簇的规则分门槛（默认 FINHOT_PUSH_MIN_SCORE 或 6）")
    pu.add_argument("--only", help="只推指定信源，逗号分隔")
    pu.set_defaults(func=cmd_push)

    args = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
