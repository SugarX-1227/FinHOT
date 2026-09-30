"""命令行入口。

  python -m finhot run                 # 采集过去 24 小时 → 生成日报
  python -m finhot run --hours 12 --no-llm
  python -m finhot sources             # 信源健康检查（每个源只抓一页）
  python -m finhot collect --hours 2   # 只采集并打印，不生成日报
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
               out_dir=Path(args.out) if args.out else None)
    print(path)
    return 0


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
    r.add_argument("--hours", type=float, default=24, help="采集最近多少小时（默认 24）")
    r.add_argument("--no-llm", action="store_true", help="不调用 LLM，只用规则")
    r.add_argument("--only")
    r.add_argument("--keep", type=int, default=400, help="items.jsonl 最多保存多少条")
    r.add_argument("--out", help="输出目录（默认写入仓库 reports/ 和 data/）")
    r.set_defaults(func=cmd_run)

    args = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
