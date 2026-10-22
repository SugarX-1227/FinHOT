"""阶段判断（波段口径，只用收盘数据）：启动 / 发酵 / 主升 / 分歧 / 退潮，外加冷门题材的“蛰伏”。

依据题材指数的均线位置、相对沪深 300 的 20 日强弱、成分股站上均线和创新高的比例。
指数缺均线数据时，用“过半成分股站上均线”代替。阈值是经验值，要用历史题材回放校准（见需求文档“验收标准”），
所以输出里把每条依据都列出来，判断错了也能看出错在哪。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .fetch import WencaiData, median

RS_HOT = 10.0       # 20 日跑赢沪深 300 超过这么多个百分点，算“主升”的强度
HIGH_HOT = 0.20     # 创 60 日新高的成分股占比超过这个，算“主升”的广度
BREADTH_ON = 0.5    # 过半成分股站上均线，视为板块站上均线（指数缺数据时用）
FADE_RET60 = -10.0  # 60 日跌超这么多且跑输大盘，算“退潮”而不是“蛰伏”

ADVICE = {
    "启动": "信息最少、可能是假启动：先读懂档案，加入跟踪，等发酵信号确认",
    "发酵": "认知窗口期：逻辑开始被验证，这一段看懂最划算",
    "主升": "逻辑已被广泛认知，追高风险上升；继续持有要看业绩能否接力",
    "分歧": "中期趋势还在、短期走弱：判断是换手还是见顶，看中军能否守住 20 日线",
    "退潮": "跌破中期均线且跑输大盘：远离，等下一次催化",
    "蛰伏": "没有行情：适合提前研究，留意潜在催化",
    "数据不足": "没有问财行情数据，只能看下面的快讯和涨停记录",
}


@dataclass
class Stage:
    name: str
    reasons: list[str] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)

    @property
    def advice(self) -> str:
        return ADVICE.get(self.name, "")


def _pct(x: float | None) -> str:
    return "—" if x is None else f"{x:+.1f}%"


def judge(wd: WencaiData | None, heat: dict | None = None) -> Stage:
    if not wd or (not wd.members and not wd.index):
        st = Stage("数据不足")
        if heat:
            st.reasons.append(_heat_text(heat))
        return st

    idx, bench = wd.index, wd.bench
    total = wd.total or sum(1 for m in wd.members if not m.extra) or None
    b = wd.breadth or {}
    share = lambda k: (b.get(k) / total) if (b.get(k) is not None and total) else None  # noqa: E731
    s20, s60, hi = share("above_ma20"), share("above_ma60"), share("high60")

    universe = [m for m in wd.members if not m.extra]
    ret20 = idx.ret20 if idx and idx.ret20 is not None else median(m.ret20 for m in universe)
    ret60 = idx.ret60 if idx and idx.ret60 is not None else median(m.ret60 for m in universe)
    src = "指数" if idx and idx.ret20 is not None else "成分股中位数"
    rs20 = ret20 - bench.ret20 if ret20 is not None and bench and bench.ret20 is not None else None

    def above(n: int, share_: float | None) -> bool | None:
        if idx and idx.close is not None and getattr(idx, f"ma{n}") is not None:
            return idx.close > getattr(idx, f"ma{n}")
        return None if share_ is None else share_ >= BREADTH_ON

    a20, a60 = above(20, s20), above(60, s60)
    m = {"ret20": ret20, "ret60": ret60, "rs20": rs20, "above20": a20, "above60": a60,
         "share_ma20": s20, "share_ma60": s60, "share_high60": hi, "source": src}
    reasons = [f"{src}近 20 日 {_pct(ret20)}，近 60 日 {_pct(ret60)}"
               + (f"；相对沪深 300 {rs20:+.1f} 个百分点" if rs20 is not None else "")]
    if a20 is not None or a60 is not None:
        on = lambda x: "站上" if x else ("跌破" if x is not None else "未知")  # noqa: E731
        how = "指数" if idx and idx.ma20 is not None else "过半成分股"
        reasons.append(f"{how}{on(a20)} 20 日线、{on(a60)} 60 日线")
    if s20 is not None:
        reasons.append(f"成分股站上 20 日线 {s20:.0%}、60 日线 {s60:.0%}" if s60 is not None
                       else f"成分股站上 20 日线 {s20:.0%}")
    if hi is not None:
        reasons.append(f"创 60 日新高的成分股占 {hi:.0%}")
    if heat:
        reasons.append(_heat_text(heat))

    hot = (rs20 is not None and rs20 >= RS_HOT) or (hi is not None and hi >= HIGH_HOT)
    weak = (ret60 is not None and ret60 <= FADE_RET60) or (rs20 is not None and rs20 < -3)
    if a20 is None and a60 is None:
        strength = rs20 if rs20 is not None else ret20
        name = "数据不足" if strength is None else ("发酵" if strength > 0 else "蛰伏")
        if name != "数据不足":
            reasons.append("缺均线数据，只按涨幅粗判")
    elif a60 is True:
        name = "分歧" if a20 is False else ("主升" if hot else "发酵")
    elif a60 is False:
        name = "启动" if a20 is True else ("退潮" if weak else "蛰伏")
    else:  # 只知道 20 日线
        name = ("主升" if hot else "发酵") if a20 else ("退潮" if weak else "蛰伏")
        reasons.append("缺 60 日线数据")
    return Stage(name, reasons, m)


def _heat_text(h: dict) -> str:
    r, b = h["recent"], h["base"]
    trend = "升温" if r > b * 1.5 and r >= 1 else ("降温" if b > 0 and r < b * 0.6 else "持平")
    return (f"FinHOT 相关快讯：近 {h['recent_days']} 天日均 {r:.1f} 条，之前 {h['base_days']} 天日均 {b:.1f} 条（{trend}）")
