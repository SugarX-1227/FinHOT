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
  python -m finhot gold --n 200        # 抽金标样本，生成标注页 data/gold/label.html（见 finhot/gold.py）
  python -m finhot backtest            # 信号战绩：日报里存下的信号之后几天的表现（reports/backtest/latest.md）
  python -m finhot push-market         # 马上算一次涨停原因，推给网站“盘面”页（平时推送进程收盘后自动跑）
  python -m finhot probe               # 检查行情数据接口（东方财富、同花顺、新浪）在这台机器上能不能连通

个性化（见 docs/PERSONALIZATION.md；画像和个人数据都不入库）：
  python -m finhot entities sync       # 同步全 A 股清单（巨潮），记改名、ST、退市
  python -m finhot entities link       # 抽查最近几天快讯的实体链接
  python -m finhot profile init 我 --themes 锂电储能,算力,半导体
  python -m finhot profile import 我 自选.csv --as holdings
  python -m finhot brief --profile 我 [--send] [--weekly]
  python -m finhot eval --profile 我 [--pool | --labels labels.json]
  python -m finhot backtest --announcements   # 按公告类型的事件研究
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
    from collections import Counter

    from .announce import announcement_sources
    from .entities import exchange_of
    anns = collect(announcement_sources(cfg), now() - timedelta(hours=max(args.hours, 24)), max_pages=2)
    if anns:
        print("\n公告源（各取 2 页，看能不能连上、沪深京是不是都有）：")
        for r in anns:
            ex = Counter(exchange_of(it.codes[0]) for it in r.items if it.codes)
            detail = r.error or "  ".join(f"{k} {v}" for k, v in sorted(ex.items())) or "（窗口内无数据）"
            print(f"{r.source_id:<14}{'OK' if r.ok else 'FAIL':<6}{len(r.items):>5}  {r.seconds:>5.1f}s  {detail}")
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
    from .backtest import record_fresh, run_backtest
    from .editions import EDITIONS, report_path, scheduled_slot
    from .pipeline import run
    from .weekly import run_weekly, weekly_exists

    t = now()
    did, failed = False, False
    ed = scheduled_slot(t)
    if ed:
        print(f"[auto] {t:%m-%d %H:%M} 生成{ed.name}")
        print(run(use_llm=not args.no_llm, edition=ed.key))
        did = True
    # 盘后版出了：当天收盘价也有了，更新信号战绩。这一班没算成（行情接口断开）的，之后的定时触发再试；失败不影响日报提交
    if report_path(t.strftime("%Y-%m-%d"), EDITIONS["close"]).exists() and not record_fresh(t):
        print(f"[auto] {t:%m-%d %H:%M} 更新信号战绩")
        try:
            print(run_backtest())
            did = True
        except Exception as e:  # noqa: BLE001
            print(f"::warning::回测失败：{e}")
    # 股票清单每天同步一次（新股、改名、ST、退市）；按公告类型的事件研究跟着盘后版更新。都是公开数据，失败不影响日报
    from . import entities
    if (entities.load_file().get("updated") or "")[:10] != t.strftime("%Y-%m-%d"):
        try:
            path, changes = entities.sync()
            print(f"[auto] 股票清单已同步：{path}；" + "，".join(f"{k} {len(v)}" for k, v in changes.items()))
            did = True
        except Exception as e:  # noqa: BLE001
            print(f"::warning::股票清单同步失败：{e}")
    if ed and ed.key == "close":
        try:
            from .evaluate import run_event_study
            print(run_event_study())
        except Exception as e:  # noqa: BLE001
            print(f"::warning::公告事件研究没算成：{e}")
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


def cmd_gold(args) -> int:
    from . import gold

    out = Path(args.out)
    cand = out / "candidates.jsonl"
    if args.labels:
        labels = json.loads(Path(args.labels).read_text(encoding="utf-8"))
        rows = gold.with_labels(gold.read_jsonl(cand), labels)
        gold.write_jsonl(rows, out / "gold.jsonl")
        print(f"写入 {out / 'gold.jsonl'}：{len(rows)} 条")
        return 0
    if cand.exists() and not args.force:
        cases = gold.read_jsonl(cand)
        print(f"沿用已有样本 {cand}（{len(cases)} 条；重新抽样加 --force，已有标注会对不上）")
    else:
        cases = gold.build_cases(gold.sample(gold.load_pool(), args.n))
        gold.write_jsonl(cases, cand)
        print(f"抽样 {len(cases)} 条 → {cand}")
    page = out / "label.html"
    page.write_text(gold.render_page(cases), encoding="utf-8")
    print(f"标注页 → {page}（用浏览器打开）")
    return 0


def cmd_push_market(args) -> int:
    from . import market_job
    from .llm import LLMClient
    from .push import Pusher, PushConfig, push_sources
    from .site_api import Site

    cfg = PushConfig.from_env()
    if len(cfg.token) < 16:
        print("没有设置 INGEST_TOKEN（至少 16 位，和网站 .env 里的一样）", file=sys.stderr)
        return 2
    site = Site(cfg.engine_url, cfg.token, Pusher(cfg).session)
    sources = push_sources(cfg, load_sources()["sources"])
    print(market_job.run_market(site, sources, DATA_DIR, llm=None if args.no_llm else LLMClient.from_env()))
    return 0


def cmd_probe(args) -> int:
    from .probe import probe

    return probe()


def cmd_backtest(args) -> int:
    from .backtest import PricesUnavailable, run_backtest

    try:
        if args.announcements:
            from .evaluate import run_event_study
            print(run_event_study(days=args.days, out_dir=Path(args.out) if args.out else None))
            return 0
        print(run_backtest(days=args.days, out_dir=Path(args.out) if args.out else None))
    except PricesUnavailable as e:
        # 行情接口暂时连不上（海外机器上常见）：上一次的结果原样保留。退出码 3 让调用方能和真正的错误区分开
        print(f"::warning::回测跳过：{e}", file=sys.stderr)
        return 3
    return 0


def cmd_entities(args) -> int:
    from . import entities

    if args.action == "sync":
        path, changes = entities.sync()
        print(f"股票清单 → {path}")
        for k, v in changes.items():
            print(f"{k} {len(v)}：{'；'.join(v[:20])}{' …' if len(v) > 20 else ''}")
        return 0
    b = entities.book()
    if args.action == "show":
        print(f"股票清单：{len(b.stocks)} 只（{'全市场' if b.complete else '种子清单，先运行 entities sync'}，更新于 {b.updated}）")
        for q in args.names:
            hits = [b.get(q)] if b.get(q) else b.resolve(q)
            for st in hits:
                print(f"  {st.code} {st.name} {st.exchange} 行业:{st.industry or '-'} 曾用名:{'、'.join(st.former) or '-'}"
                      f" 别称:{'、'.join(st.aliases) or '-'} H股:{st.hk or '-'} {'ST ' if st.st else ''}{st.status}")
            if not hits:
                print(f"  {q}：没找到")
        return 0
    # link：抽查最近几天快讯的实体链接，人工核对查准率
    import random
    from collections import Counter

    from .linker import Linker
    from .models import NewsItem
    linker = Linker(b)
    rows, seen = [], set()
    for day in sorted(DATA_DIR.glob("20??-??-??"))[-args.days:]:
        for path in sorted(day.glob("items*.jsonl")):
            for line in path.read_text(encoding="utf-8").splitlines():
                it = NewsItem.from_dict(json.loads(line))
                if it.uid in seen:
                    continue
                seen.add(it.uid)
                rows += [(m, it) for m in linker.link(it)]
    roles = Counter(m.role for m, _ in rows)
    print(f"{len(seen)} 条快讯，{len(rows)} 次点名：" + "，".join(f"{k} {v}" for k, v in roles.most_common()))
    # 分层抽样：每种角色都抽一些，易混的名字（一名多码、常用词）全放进来
    rng = random.Random(args.seed)
    by_role: dict[str, list] = {}
    for m, it in rows:
        by_role.setdefault(m.role, []).append((m, it))
    picked = []
    for role, group in by_role.items():
        picked += rng.sample(group, min(len(group), max(2, args.sample * len(group) // max(1, len(rows)))))
    for m, it in picked[: args.sample]:
        print(f"[{m.role}] {m.code} {m.name}（原文写作“{m.text}”）｜{it.title[:50]}")
    return 0


def cmd_profile(args) -> int:
    from . import profile as P

    if args.action == "list":
        for pr in P.all_profiles():
            print(f"{pr.name}：持仓 {len(pr.holdings)}，自选 {len(pr.watch)}，题材 {'、'.join(pr.themes) or '-'}，推送 {pr.push.channel}")
        return 0
    if args.action == "init":
        path = P.path_of(args.name)
        if path.exists() and not args.force:
            print(f"{path} 已经有了（覆盖加 --force）", file=sys.stderr)
            return 1
        pr = P.Profile(args.name, holdings=P._codes(_only(args.holdings) or []), watch=P._codes(_only(args.watch) or []),
                       themes=_only(args.themes) or [], style=args.style)
        print(f"画像 → {P.save(pr)}")
        for w in P.validate(pr):
            print("⚠️", w)
        return 0
    pr = P.load(args.name)
    if args.action == "import":
        codes, unresolved = P.extract(P.read_text(Path(args.file)))
        target = pr.holdings if args.as_ == "holdings" else pr.watch
        before = len(target)
        target += [c for c in codes if c not in target]
        print(f"认出 {len(codes)} 只，新加进{'持仓' if args.as_ == 'holdings' else '自选'} {len(target) - before} 只 → {P.save(pr)}")
        if unresolved:
            print("没认出来的名字（可以手动写代码）：" + "、".join(unresolved))
        return 0
    if args.action == "relations":
        from .relations import load_items, suggest
        found = suggest(pr, load_items(args.days, now()))
        pr.relations, added = P.merge_relations(pr.relations, found)
        P.save(pr)
        print(f"找到 {len(found)} 条候选关系，新加 {added} 条（状态“待审核”，在画像里改成“已确认”才参与排序）")
        for r in found:
            print(f"  {r.code} 的{r.kind}：{r.target}（置信度 {r.confidence}）“{r.quote}”")
        return 0
    from .entities import book
    b = book()
    print(f"画像 {pr.name}（{P.path_of(pr.name)}）")
    print("持仓：" + ("、".join(b.label(c) for c in pr.holdings) or "-"))
    print("自选：" + ("、".join(b.label(c) for c in pr.watch) or "-"))
    print(f"题材：{'、'.join(pr.themes) or '-'}；风格：{pr.style}；常看时段：{'、'.join(pr.hours)}")
    print(f"推送：{pr.push.channel}（{'地址已配置' if pr.push.url else '没有地址'}）")
    for r in pr.relations:
        print(f"关系：{b.label(r.code)} 的{r.kind} {r.target} · {r.status} · {r.source}" + (f"“{r.quote}”" if r.quote else ""))
    for w in P.validate(pr, b):
        print("⚠️", w)
    return 0


def cmd_brief(args) -> int:
    from . import brief, profile as P
    pr = P.load(args.profile)
    if args.weekly:
        from .evaluate import weekly_personal
        md = weekly_personal(pr)
        out = P.private_dir() / "briefs" / pr.name / f"{now():%Y-%m-%d}-weekly.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(md, encoding="utf-8")
        print(md)
        if args.send:
            from .notify import send
            send(pr, f"我的周报 · {now():%m-%d}", md.split("\n", 2)[2])
        print(f"\n（存在 {out}）", file=sys.stderr)
        return 0
    br = brief.run(pr, hours=args.hours, edition=args.edition, market=not args.no_market, site=not args.no_site,
                   send=args.send)
    print(br.markdown)
    print(f"（存在 {brief.save_path(pr, br.at, br.edition)}）", file=sys.stderr)
    return 0


def cmd_feedback(args) -> int:
    from . import history
    if args.action == "serve":
        from .feedback_server import serve
        return 0 if serve(port=args.port) else 2
    row = history.add_feedback(args.profile, args.item, args.label, now())
    print(f"已记录：{history.LABELS[row['label']]}")
    return 0


def cmd_eval(args) -> int:
    from . import evaluate
    if args.pool:
        page, n = evaluate.write_pool(args.profile, args.days, args.top)
        print(f"盲标页（{n} 条）→ {page}；标完导出 labels.json，再运行：python -m finhot eval --profile {args.profile} --labels labels.json")
        return 0
    if args.labels:
        from .profile import private_dir
        rows = json.loads((private_dir() / "eval" / args.profile / "pool.json").read_text(encoding="utf-8"))
        labels = json.loads(Path(args.labels).read_text(encoding="utf-8"))
        for name, r in evaluate.compare(rows, labels).items():
            label = "个性化排序" if name == "personal" else "通用排序"
            print(f"{label}：标过 {r['labeled']} 条，有关 {r['useful']} 条，precision {r['precision']}%")
        return 0
    print(evaluate.feedback_report(args.profile, args.days))
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

    pr = sub.add_parser("probe", help="检查行情数据接口在这台机器上能不能连通")
    pr.set_defaults(func=cmd_probe)

    pm = sub.add_parser("push-market", help="马上算一次涨停原因，推给网站“盘面”页")
    pm.add_argument("--no-llm", action="store_true", help="不用模型改写涨停原因")
    pm.set_defaults(func=cmd_push_market)

    bk = sub.add_parser("backtest", help="信号战绩：日报存下的信号之后几天的表现")
    bk.add_argument("--days", type=int, default=90, help="统计最近多少天的信号")
    bk.add_argument("--out", help="输出目录（默认写入仓库 reports/ 和 data/）")
    bk.add_argument("--announcements", action="store_true", help="按公告类型的事件研究（reports/backtest/announcements.md）")
    bk.set_defaults(func=cmd_backtest)

    en = sub.add_parser("entities", help="实体库：同步全 A 股清单、查股票、抽查实体链接")
    en.add_argument("action", choices=["sync", "show", "link"])
    en.add_argument("names", nargs="*", help="show：代码或简称")
    en.add_argument("--days", type=int, default=5, help="link：最近几天的快讯")
    en.add_argument("--sample", type=int, default=50, help="link：抽多少条")
    en.add_argument("--seed", type=int, default=1)
    en.set_defaults(func=cmd_entities)

    pf = sub.add_parser("profile", help="个人画像（不入库）：init / import / show / relations / list")
    pf.add_argument("action", choices=["init", "import", "show", "relations", "list"])
    pf.add_argument("name", nargs="?", default="")
    pf.add_argument("file", nargs="?", help="import：同花顺 / 东方财富导出的自选、或任意含代码的文本")
    pf.add_argument("--as", dest="as_", choices=["holdings", "watch"], default="watch", help="import：导入到持仓还是自选")
    pf.add_argument("--themes", help="init：关注题材，逗号分隔（冷启动选 3 个就能出简报）")
    pf.add_argument("--holdings", help="init：持仓代码，逗号分隔")
    pf.add_argument("--watch", help="init：自选代码，逗号分隔")
    pf.add_argument("--style", default="波段", choices=["短线", "波段", "中长线"])
    pf.add_argument("--days", type=int, default=30, help="relations：从最近多少天的快讯和公告里找")
    pf.add_argument("--force", action="store_true")
    pf.set_defaults(func=cmd_profile)

    br = sub.add_parser("brief", help="我的盘前 5 分钟 / 盘后简报 / 我的周报（只在本地或服务器上生成）")
    br.add_argument("--profile", required=True)
    br.add_argument("--hours", type=float, help="采集最近多少小时（默认接着上一份简报，6~72 小时）")
    br.add_argument("--edition", choices=["pre", "noon", "close"])
    br.add_argument("--no-market", action="store_true", help="不取行情和日程")
    br.add_argument("--no-site", action="store_true", help="不读网站精选")
    br.add_argument("--send", action="store_true", help="发到画像里配的群")
    br.add_argument("--weekly", action="store_true", help="我的周报")
    br.set_defaults(func=cmd_brief)

    fb = sub.add_parser("feedback", help="反馈：add 记一条；serve 起收反馈链接的服务")
    fb.add_argument("action", choices=["add", "serve"])
    fb.add_argument("--profile")
    fb.add_argument("--item", help="条目 id（简报候选里的 id）")
    fb.add_argument("--label", choices=["useful", "irrelevant", "known"])
    fb.add_argument("--port", type=int)
    fb.set_defaults(func=cmd_feedback)

    ev = sub.add_parser("eval", help="个性化评估：反馈报告 / 盲标对比")
    ev.add_argument("--profile", required=True)
    ev.add_argument("--days", type=int, default=7)
    ev.add_argument("--pool", action="store_true", help="生成盲标页（个性化与通用排序的前 N 条合并打乱）")
    ev.add_argument("--top", type=int, default=10)
    ev.add_argument("--labels", help="导入盲标结果，算两种排序的 precision")
    ev.set_defaults(func=cmd_eval)

    g = sub.add_parser("gold", help="抽金标样本、生成标注页、合成 gold.jsonl")
    g.add_argument("--n", type=int, default=200, help="抽多少条")
    g.add_argument("--out", default=str(DATA_DIR / "gold"))
    g.add_argument("--force", action="store_true", help="重新抽样（覆盖已有样本）")
    g.add_argument("--labels", help="标注结果 JSON（{caseId: select|reject|either}），合成 gold.jsonl")
    g.set_defaults(func=cmd_gold)

    args = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    from .profile import PrivacyError
    try:
        return args.func(args)
    except (PrivacyError, FileNotFoundError) as e:
        print(f"❌ {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
