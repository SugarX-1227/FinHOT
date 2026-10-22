"""把知识层 + 数据层渲染成一张 Markdown 档案卡（reports/topics/<slug>.md）。

结构按需求文档“工具必须能回答的 10 个问题”排：是什么、为什么炒、产业链、炒到哪了、龙头与正宗度、
近期催化、历史行情、风险与证伪、催化日历。每条知识层的结论尽量带出处链接；问财和 FinHOT 的数据注明来源。
"""

from __future__ import annotations

import re
from datetime import datetime

from .fetch import Member, WencaiData, leaders, segment_stats
from .kb import Stock, Topic
from .local import News
from .stage import Stage


def pct(x: float | None, digits: int = 1) -> str:
    return "—" if x is None else f"{x:+.{digits}f}%"


def yi(x: float | None) -> str:
    if x is None:
        return "—"
    return f"{x:,.0f}" if abs(x) >= 100 else f"{x:,.1f}"


def num1(x: float | None) -> str:
    return "—" if x is None else f"{x:.1f}"


CJK_GAP = re.compile(r"(?<=[一-鿿，。；：、！？）”])\s+(?=[一-鿿（“])")


def cell(s) -> str:
    """表格单元格 / 单行文本：去掉竖线和换行；YAML 折行在中文之间留下的空格也去掉。"""
    text = str(s or "").replace("|", "／").replace("\n", " ").strip()
    return CJK_GAP.sub("", text) or "—"


def link(text: str, url: str | None) -> str:
    return f"[{text}]({url})" if url else text


def _src(url: str | None) -> str:
    return f" [来源]({url})" if url else ""


def _stock_label(name: str, m: Member | None, core: bool) -> str:
    label = f"**{name}**" if core else name
    return f"{label} {pct(m.ret20)}" if m and m.ret20 is not None else label


def render(topic: Topic, at: datetime, wd: WencaiData | None, stage: Stage, local: dict,
           wencai_note: str = "") -> str:
    t = topic.data
    members = {m.code: m for m in (wd.members if wd else [])}
    by_name = {m.name: m for m in (wd.members if wd else [])}
    find = lambda s: members.get(s.code) or by_name.get(s.name)  # noqa: E731
    segs = segment_stats(wd.members, topic) if wd and wd.members else []
    lead = leaders(wd.members) if wd and wd.members else {"core": [], "strong": []}
    wencai_universe = (t.get("wencai") or {}).get("universe", "")
    core_known = []  # 知识层标“核心”的公司，同一家出现在多个环节时只取第一次
    for s in topic.stocks():
        if s.tier == "核心" and s.name not in {k.name for k in core_known}:
            core_known.append(s)

    out = [f"# {t['name']} · 题材档案", "",
           f"<!-- topic: slug={t['slug']} stage={stage.name} at={at:%Y-%m-%d %H:%M} -->", ""]
    status = "（知识层未校对）" if not t.get("reviewed") else ""
    data_line = wencai_note or "行情：未接入问财（设置 IWENCAI_API_KEY 后重跑）"
    out += [f"> {cell(t['one_liner'])}", ">",
            f"> 生成于 {at:%Y-%m-%d %H:%M} · {data_line} · 知识层更新于 {t.get('updated', '—')}{status}", ""]

    # ---------------- 速览
    def names(ms: list[Member], k: int = 5) -> str:
        return "、".join(f"{m.name} {pct(m.ret20)}" for m in ms[:k]) or "—"

    focus = t.get("focus") or []
    hot_segs = "、".join(f"{s['segment']} {pct(s['ret20'])}" for s in segs[:3] if s["ret20"] is not None)
    cal = t.get("calendar") or []
    next_cal = "；".join(f"{c.get('date')} {c.get('event')}" for c in cal[:2])
    out += ["## 速览", "", "| 问题 | 结论 |", "| --- | --- |",
            f"| 炒到哪了 | **{stage.name}** · {cell('；'.join(stage.reasons[:2]) or '—')} |",
            f"| 为什么炒 | {cell('、'.join(t.get('drivers', {}).get('types') or []))} · "
            f"{cell(t.get('drivers', {}).get('evidence'))} |",
            f"| 资金在炒哪个细分 | {cell(hot_segs or '；'.join(f.get('segment', '') for f in focus[:3]))}"
            f"{'（近 20 日环节中位涨幅）' if hot_segs else '（知识层判断，接入问财后按环节涨幅排）'} |",
            f"| 趋势中军 | {cell(names(lead['core']))} |",
            f"| 正宗龙头（知识层） | {cell('、'.join(_stock_label(s.name, find(s), False) for s in core_known[:8]))} |",
            f"| 下一个催化 | {cell(next_cal)} |",
            f"| 什么会证伪 | {cell((t.get('falsifiers') or ['—'])[0])} |", ""]

    # ---------------- 一、是什么
    out += ["## 一、是什么", ""]
    for s in t.get("status") or []:
        out.append(f"- {s['text']}{_src(s.get('url'))}")
    b = t.get("boundary") or {}
    if b:
        out += ["", "**概念边界**", ""]
        out += [f"- 包含：{x}" for x in b.get("includes") or []]
        out += [f"- 不含：{x}" for x in b.get("excludes") or []]
        rel = b.get("related") or []
        if rel:
            out += ["", "| 相近概念 | 区别 |", "| --- | --- |"]
            out += [f"| {cell(r['name'])} | {cell(r['diff'])} |" for r in rel]
    out.append("")

    # ---------------- 二、为什么炒
    d = t.get("drivers") or {}
    out += ["## 二、为什么炒：驱动、政策与海外映射", "",
            f"**驱动类型**：{'、'.join(d.get('types') or [])}　**证据强度**：{d.get('evidence', '—')}", "",
            cell(d.get("summary")), ""]
    pol = t.get("policy") or []
    if pol:
        out += ["| 日期 | 政策 | 发布方 | 要点 |", "| --- | --- | --- | --- |"]
        for p in sorted(pol, key=lambda p: str(p.get("date")), reverse=True):
            out.append(f"| {p.get('date', '')} | {link(cell(p['name']), p.get('url'))} | {cell(p.get('issuer'))} | "
                       f"{cell(p.get('points'))}{'' if p.get('url') else '（待补出处）'} |")
        out.append("")
    ov = t.get("overseas") or []
    if ov:
        out += ["**海外映射**", "", "| 海外 | 代码 | 怎么传导到 A 股 |", "| --- | --- | --- |"]
        out += [f"| {link(cell(o['name']), o.get('url'))} | {cell(o.get('ticker'))} | {cell(o.get('link'))} |"
                for o in ov]
        out.append("")

    # ---------------- 三、产业链
    out += ["## 三、产业链", ""]
    seg_ret = {s["segment"]: s for s in segs}
    if segs:
        out += ["“环节涨幅”是该环节问财成分股近 20 日涨幅的中位数（成分股按知识层名单和主营产品归类）。", ""]
    for st in t.get("chain") or []:
        out += [f"### {st['stage']}", "", "| 环节 | 做什么 | 壁垒与格局 | A 股公司（近 20 日） | 环节涨幅 |",
                "| --- | --- | --- | --- | --- |"]
        for seg in st.get("segments") or []:
            stocks = [Stock(name=str(s.get("name", "")), code=str(s.get("code") or ""), tier=str(s.get("tier") or ""))
                      for s in seg.get("stocks") or []]
            listed = "、".join(_stock_label(s.name, find(s), s.tier == "核心") for s in stocks)
            extra = [m for m in (wd.members if wd else []) if seg["name"] in m.segments
                     and m.code not in {s.code for s in stocks} and m.name not in {s.name for s in stocks}]
            extra = sorted(extra, key=lambda m: -(m.mcap or 0))[:3]
            if extra:
                listed += ("；" if listed else "") + "主营相关：" + "、".join(f"{m.name} {pct(m.ret20)}" for m in extra)
            sr = seg_ret.get(seg["name"])
            out.append(f"| {cell(seg['name'])} | {cell(seg.get('what'))} | {cell(seg.get('moat'))} | {cell(listed)} | "
                       f"{pct(sr['ret20']) if sr else '—'} |")
        out.append("")
    out += ["加粗为知识层标注的核心公司。", ""]

    # ---------------- 四、炒到哪了
    out += ["## 四、炒到哪了：阶段判断", "", f"**{stage.name}** —— {stage.advice}", ""]
    out += [f"- {r}" for r in stage.reasons]
    if wd and (wd.index or wd.bench):
        out += ["", "| 指标 | 题材 | 沪深 300 |", "| --- | --- | --- |"]
        i, bn = wd.index, wd.bench
        out += [f"| 近 20 日涨跌 | {pct(i.ret20) if i else '—'} | {pct(bn.ret20) if bn else '—'} |",
                f"| 近 60 日涨跌 | {pct(i.ret60) if i else '—'} | {pct(bn.ret60) if bn else '—'} |",
                f"| 收盘 / 20 日线 / 60 日线 | {num1(i.close) if i else '—'} / {num1(i.ma20) if i else '—'} / "
                f"{num1(i.ma60) if i else '—'} | — |",
                f"| 近 20 日成交额（亿元） | {yi(i.amount20) if i else '—'} | — |"]
    out += ["", "阶段规则：站上 20 日和 60 日线为发酵，再叠加 20 日跑赢沪深 300 超 10 个百分点或两成成分股创 60 日新高"
            "为主升；60 日线上、20 日线下为分歧；20 日线上、60 日线下为启动；都在下方时，60 日跌超 10% 或明显跑输"
            "为退潮，否则为蛰伏。阈值待用历史题材回放校准。", ""]

    # ---------------- 五、个股梯队与正宗度
    out += ["## 五、个股梯队与正宗度", ""]
    rows: list[tuple[str, Member | None, str, str]] = []
    seen = set()
    for m in lead["core"]:
        rows.append(("趋势中军", m, m.name, "、".join(m.segments)))
        seen.add(m.code)
    for s in core_known:
        m = find(s)
        if m and m.code in seen:
            continue
        rows.append(("正宗龙头", m, s.name, s.segment))
        if m:
            seen.add(m.code)
    for m in lead["strong"]:
        if m.code in seen:
            continue
        rows.append(("强势股", m, m.name, "、".join(m.segments)))
        seen.add(m.code)
    if rows:
        out += ["| 角色 | 股票 | 环节 | 总市值（亿） | 近 20 日 | 近 60 日 | 营收同比 | 净利同比 | PE(TTM) | 正宗度依据 |",
                "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
        for role, m, name, seg in rows:
            basis = []
            if m and m.tier:
                basis.append(m.tier)
            if m and m.note:
                basis.append(m.note)
            elif not m:
                known = next((s for s in core_known if s.name == name), None)
                if known and known.note:
                    basis.append(known.note)
            if m and m.products:
                basis.append(f"主营：{m.products[:40]}")
            if m and m.extra:
                basis.append(f"不在问财“{wencai_universe}”成分里")
            if m and not m.tier:
                basis.append("知识层和主营都对不上，可能是蹭概念")
            out.append(f"| {role} | {name}{f'（{m.code}）' if m else ''} | {cell(seg)} | {yi(m.mcap) if m else '—'} | "
                       f"{pct(m.ret20) if m else '—'} | {pct(m.ret60) if m else '—'} | {pct(m.rev_yoy) if m else '—'} | "
                       f"{pct(m.np_yoy) if m else '—'} | {num1(m.pe) if m else '—'} | {cell('；'.join(basis))} |")
        out += ["", "趋势中军：市值前 30% 里近 20 日上涨、成交额最大的几只（波段看它们能否守住趋势）；强势股：近 20 日涨幅"
                "最大的几只；情绪龙头见下一节的涨停记录。", ""]
        universe = [m for m in (wd.members if wd else []) if not m.extra]
        loose = [m for m in universe if not m.tier]
        if universe and loose:
            rising = sorted((m for m in loose if m.ret20 is not None and m.ret20 > 0), key=lambda m: -m.ret20)[:6]
            out.append(f"**正宗度提示**：问财成分股 {len(universe)} 只里，{len(loose)} 只的主营产品和知识层名单都对不上"
                       "（可能是蹭概念，也可能是知识层漏了）"
                       + (f"；其中近 20 日涨得最多的：{'、'.join(f'{m.name} {pct(m.ret20)}' for m in rising)}。" if rising
                          else "。"))
            out.append("")
    else:
        out += ["接入问财后按数据排出梯队。", ""]

    # ---------------- 六、近期催化
    out += ["## 六、近期催化（近 30 天）", ""]
    tl: list[News] = local.get("timeline") or []
    hits = wd.news if wd else []
    if tl or hits:
        out += ["| 日期 | 事件 | 来源 |", "| --- | --- | --- |"]
        titles = set()
        for n in tl:
            titles.add(n.title[:14])
            out.append(f"| {n.published[5:10]} | {link(cell(n.title), n.url)} | {cell(n.source)}"
                       f"{f' 等{n.dup}家' if n.dup > 1 else ''} |")
        for h in hits[:8]:
            if h.title[:14] in titles:
                continue
            out.append(f"| {h.published[5:10] or '—'} | {link(cell(h.title), h.url)} | 问财新闻 |")
        out.append("")
        out.append("FinHOT 快讯按重要性打分，每天取前两条；问财新闻补充 FinHOT 没覆盖到的。")
    else:
        out.append("近 30 天没有相关高分快讯。")
    out.append("")

    # ---------------- 七、涨停与板块
    zt = local.get("limitups") or []
    bd = local.get("boards") or []
    out += ["## 七、涨停与板块记录（FinHOT 盘面）", ""]
    if zt:
        out += ["| 日期 | 股票 | 连板 | 涨停原因 |", "| --- | --- | --- | --- |"]
        for z in sorted(zt, key=lambda z: (z["date"], z.get("boards", 1)), reverse=True)[:15]:
            out.append(f"| {z['date'][5:]} | {z['name']}（{z['code']}） | {cell(z.get('stat') or z.get('boards'))} | "
                       f"{cell(z.get('reason'))} |")
        out.append("")
    if bd:
        days = sorted({x["date"] for x in bd}, reverse=True)
        out += ["相关板块进入当日涨幅榜前后 30 名的记录：", "", "| 日期 | 板块 | 涨跌 | 主力净流入（亿） | 领涨 |",
                "| --- | --- | --- | --- | --- |"]
        for day in days[:8]:
            for x in sorted((x for x in bd if x["date"] == day), key=lambda x: -(x.get("pct") or 0))[:3]:
                out.append(f"| {day[5:]} | {x['name']} | {pct(x.get('pct'), 2)} | "
                           f"{num1(x.get('inflow'))} | {cell(x.get('leader'))} |")
        out.append("")
    if not zt and not bd:
        out += ["最近 20 天 FinHOT 盘面记录里没有相关涨停或板块。", ""]

    # ---------------- 八、历史行情
    hist = t.get("history") or []
    out += ["## 八、历史行情", ""]
    if hist:
        out += ["| 时间 | 起因 | 驱动 | 龙头 | 怎么结束的 |", "| --- | --- | --- | --- | --- |"]
        for h in reversed(hist):
            mark = "" if h.get("verified") else "（凭记忆，待核实）"
            out.append(f"| {cell(h['window'])} | {link(cell(h.get('trigger')), h.get('url'))}{mark} | "
                       f"{cell(h.get('driver'))} | {cell(h.get('leaders'))} | {cell(h.get('fade'))} |")
        out += ["", "下一步由复盘库用 3 年行情自动找出爆炒窗口、算出区间涨幅最大的个股，替换“待核实”的记录。", ""]

    # ---------------- 九、风险与证伪
    out += ["## 九、风险与证伪", "", "**什么会证伪逻辑**", ""]
    out += [f"- {x}" for x in t.get("falsifiers") or []]
    out += ["", "**风险信号**", ""]
    out += [f"- {x}" for x in t.get("risks") or []]
    if wd and wd.notices:
        out += ["", "问财公告检索（含“澄清”）：", ""]
        out += [f"- {h.published[:10]} {link(cell(h.title), h.url)}" for h in wd.notices[:8]]
    out.append("")

    # ---------------- 十、催化日历
    out += ["## 十、催化日历", ""]
    if cal:
        out += ["| 日期 | 事件 |", "| --- | --- |"]
        out += [f"| {c.get('date')} | {link(cell(c.get('event')), c.get('url'))} |" for c in cal]
    else:
        out.append("暂无。")
    out.append("")

    # ---------------- 研报
    if wd and wd.reports:
        out += ["## 研报（问财）", ""]
        out += [f"- {h.published[:10]} {link(cell(h.title), h.url)}" for h in wd.reports[:10]]
        out.append("")

    # ---------------- 数据说明
    out += ["## 数据说明", ""]
    if wd:
        out.append(f"- 问财：本次真实调用 {wd.calls} 次、命中缓存 {wd.cache_hits} 次；成分股 {wd.total} 只，"
                   f"取到行情 {sum(1 for m in wd.members if m.ret20 is not None)} 只")
        if wd.fatal:
            out.append(f"- 问财提前停止：{wd.fatal}")
        for e in wd.errors[:8]:
            out.append(f"- 问财问句失败：{cell(e)[:160]}")
    else:
        out.append("- 问财：未接入，档案里的行情、成分股、阶段指标都是空的")
    h = local.get("heat") or {}
    out.append(f"- FinHOT 本地：相关快讯 {sum((h.get('daily') or {}).values())} 条（近 30 天），"
               f"涨停记录 {len(zt)} 条，板块记录 {len(bd)} 条；快讯从 2026-09-30 开始积累")
    out.append(f"- 知识层：topics/{topic.path.name}" + ("（未校对，改完把 reviewed 设为 true）" if not t.get("reviewed") else ""))
    src = t.get("sources") or []
    if src:
        out += ["", "**知识层参考来源**", ""]
        out += [f"- {link(cell(s['title']), s.get('url'))}" for s in src]
    out += ["", "> 本档案由程序和 AI 根据公开信息整理，用于理解题材，不构成投资建议。", ""]
    return "\n".join(out)
