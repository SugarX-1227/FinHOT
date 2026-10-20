"""个人画像：持仓、自选、关注题材、交易风格、常看时段、推送渠道，加上个人的关系库。

画像和一切个人数据（简报、推送记录、反馈）都不能进仓库：仓库是公开的，GitHub Actions 会把 reports/ 和 data/
自动提交，公开仓库的 Actions 日志也是公开的。所以：
- 画像放在 FINHOT_PROFILES_DIR（默认仓库下的 profiles/，已在 .gitignore 里；服务器上挂载进容器）；
- 个人数据放在 FINHOT_PRIVATE_DIR（默认仓库下的 .private/；服务器上是数据卷里的 private/）；
- 这两个目录如果被配置到 reports/、data/、_site/ 这些会公开的目录下面，直接报错；
- 在 GitHub Actions 里（GITHUB_ACTIONS=true）拒绝读画像、拒绝生成个人内容。
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, time
from pathlib import Path

import yaml

from .config import DATA_DIR, REPORTS_DIR, ROOT, env
from .entities import Book, book as default_book
from .themes import THEMES

STYLES = ("短线", "波段", "中长线")
RELATION_KINDS = ("上游", "下游", "客户", "供应商", "竞品", "合作", "同行业")
PUBLIC_DIRS = (REPORTS_DIR, DATA_DIR, ROOT / "_site", ROOT / "sources", ROOT / "docs", ROOT / "web")


class PrivacyError(RuntimeError):
    pass


def _safe_dir(path: Path, what: str) -> Path:
    path = path.resolve()
    for pub in PUBLIC_DIRS:
        pub = pub.resolve()
        if path == pub or pub in path.parents:
            # data/private、data/profiles 已在 .gitignore 里（服务器上数据卷挂在 /app/data，个人数据放 /app/data/private）
            if pub == DATA_DIR.resolve() and path.relative_to(pub).parts[:1] in (("private",), ("profiles",)):
                continue
            raise PrivacyError(f"{what}（{path}）在会公开的目录 {pub} 下面，个人数据不能放这里")
    return path


def refuse_ci() -> None:
    if env("GITHUB_ACTIONS") == "true":
        raise PrivacyError("在 GitHub Actions 里不生成个人内容：公开仓库的运行日志和提交都是公开的")


def profiles_dir() -> Path:
    return _safe_dir(Path(env("FINHOT_PROFILES_DIR") or ROOT / "profiles"), "画像目录")


def private_dir() -> Path:
    path = _safe_dir(Path(env("FINHOT_PRIVATE_DIR") or ROOT / ".private"), "个人数据目录")
    path.mkdir(parents=True, exist_ok=True)
    return path


@dataclass
class PushConfig:
    channel: str = "none"          # feishu / wecom / none
    webhook: str = ""              # 地址，或 env:变量名
    secret: str = ""               # 飞书签名校验密钥，或 env:变量名
    daily_limit: int | None = None
    quiet: str | None = None       # "22:30-07:30"

    def resolve(self, value: str) -> str:
        return env(value[4:]) if value.startswith("env:") else value

    @property
    def url(self) -> str:
        return self.resolve(self.webhook)

    @property
    def key(self) -> str:
        return self.resolve(self.secret)


@dataclass
class Relation:
    code: str                      # 持仓 / 自选的代码
    target: str                    # 关联公司（代码或简称）
    kind: str                      # 上游 / 下游 / 客户 / 供应商 / 竞品 / 合作
    source: str = "人工维护"        # 来源：人工维护，或原文链接 + 原句
    confidence: float = 1.0
    status: str = "已确认"          # 已确认 / 待审核（待审核的不参与排序）
    generated: str = ""            # 生成时间（自动归纳的才有）
    quote: str = ""                # 原句（自动归纳的必须有）

    @property
    def manual(self) -> bool:
        return self.source == "人工维护"


@dataclass
class Profile:
    name: str
    holdings: list[str] = field(default_factory=list)
    watch: list[str] = field(default_factory=list)
    themes: list[str] = field(default_factory=list)
    style: str = "波段"
    hours: list[str] = field(default_factory=lambda: ["08:20", "15:45"])
    push: PushConfig = field(default_factory=PushConfig)
    aliases: dict[str, list[str]] = field(default_factory=dict)
    relations: list[Relation] = field(default_factory=list)

    @property
    def codes(self) -> list[str]:
        return list(dict.fromkeys(self.holdings + self.watch))

    def relation_of(self, code: str) -> str:
        return "持仓" if code in self.holdings else "自选" if code in self.watch else ""

    def confirmed_relations(self) -> list[Relation]:
        return [r for r in self.relations if r.status == "已确认"]

    def brief_times(self) -> list[tuple[str, time]]:
        """常看时段 → （版次, 时刻）：12 点前是盘前版，15 点后是盘后版，中间的是午间版。"""
        out = []
        for h in self.hours:
            try:
                t = time.fromisoformat(h)
            except ValueError:
                continue
            out.append(("pre" if t < time(12) else "close" if t >= time(15) else "noon", t))
        return out

    def to_dict(self) -> dict:
        d = asdict(self)
        d["push"] = {k: v for k, v in d["push"].items() if v not in (None, "")}
        d["relations"] = [{k: v for k, v in r.items() if v not in ("", None)} for r in d["relations"]]
        return d


def _codes(values) -> list[str]:
    return [str(v).strip().zfill(6) for v in values or [] if str(v).strip()]


def from_dict(d: dict) -> Profile:
    push = d.get("push") or {}
    return Profile(
        name=str(d.get("name") or ""),
        holdings=_codes(d.get("holdings")), watch=_codes(d.get("watch")),
        themes=[str(t) for t in d.get("themes") or []],
        style=str(d.get("style") or "波段"),
        hours=[str(h) for h in d.get("hours") or ["08:20", "15:45"]],
        push=PushConfig(**{k: v for k, v in push.items() if k in PushConfig.__dataclass_fields__}),
        aliases={str(k).zfill(6): [str(a) for a in v or []] for k, v in (d.get("aliases") or {}).items()},
        relations=[Relation(**{k: (str(v).zfill(6) if k == "code" else v) for k, v in r.items()
                               if k in Relation.__dataclass_fields__}) for r in d.get("relations") or []],
    )


def path_of(name: str) -> Path:
    if not re.fullmatch(r"[\w\-一-龥]{1,40}", name):
        raise ValueError(f"画像名只能用字母、数字、中文、- 和 _：{name!r}")
    return profiles_dir() / f"{name}.yaml"


def load(name: str) -> Profile:
    refuse_ci()
    path = path_of(name)
    if not path.exists():
        raise FileNotFoundError(f"没有画像 {path}（先运行 python -m finhot profile init {name}）")
    p = from_dict(yaml.safe_load(path.read_text(encoding="utf-8")) or {})
    p.name = p.name or name
    return p


def save(p: Profile) -> Path:
    refuse_ci()
    if p.name == "example":   # profiles/example.yaml 是入库的示例，写进真实持仓就会被提交
        raise PrivacyError("example 是入库的示例画像，换个名字（profiles/ 下其他文件都不入库）")
    path = path_of(p.name)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "# FinHOT 个人画像（不入库）。字段说明见 profiles/example.yaml。\n" + yaml.safe_dump(
        p.to_dict(), allow_unicode=True, sort_keys=False)
    path.write_text(text, encoding="utf-8")
    return path


def all_profiles() -> list[Profile]:
    refuse_ci()
    d = profiles_dir()
    return [load(p.stem) for p in sorted(d.glob("*.yaml")) if p.stem != "example"] if d.exists() else []


def validate(p: Profile, b: Book | None = None) -> list[str]:
    """画像里的问题（不认识的代码、题材名对不上……）。"""
    b = b or default_book()
    out = []
    for c in p.codes:
        if not b.get(c):
            out.append(f"代码 {c} 不在股票清单里" + ("" if b.complete else "（清单还是种子版，先运行 python -m finhot entities sync）"))
        elif b.get(c).status != "listed":
            out.append(f"{b.label(c)} 已退市")
    for t in p.themes:
        if t not in THEMES:
            out.append(f"题材“{t}”不在 finhot/themes.py 里（可选：{'、'.join(THEMES)}）")
    if p.style not in STYLES:
        out.append(f"交易风格“{p.style}”应为 {'/'.join(STYLES)}")
    if not p.codes and not p.themes:
        out.append("画像里既没有股票也没有题材，简报只有宏观段")
    if p.push.channel not in ("feishu", "wecom", "none"):
        out.append(f"推送渠道“{p.push.channel}”应为 feishu / wecom / none")
    elif p.push.channel != "none" and not p.push.url:
        out.append(f"推送渠道是 {p.push.channel}，但 webhook 是空的（{p.push.webhook or '没填'}）")
    for r in p.relations:
        if r.kind not in RELATION_KINDS:
            out.append(f"关系“{r.kind}”应为 {'/'.join(RELATION_KINDS)}")
        if not r.manual and not r.quote:
            out.append(f"{r.code}→{r.target} 不是人工维护的，却没有原句，不会被使用")
    return out


# ------------------------------------------------------------------ 导入自选
_CODE_RE = re.compile(r"(?<![\d.])(?:(?:SH|SZ|BJ|sh|sz|bj)[.:]?)?([03468]\d{5}|920\d{3})(?:\.(?:SH|SZ|BJ|sh|sz|bj))?(?![\d.])")


def read_text(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def extract(text: str, b: Book | None = None) -> tuple[list[str], list[str]]:
    """从导出文件或随手粘贴的文字里找股票：先认代码（同花顺 / 东方财富导出的 CSV、TXT、“SZ300750”“600519.SH”），
    没有代码的行再按简称对（对不上的原样返回）。返回（代码, 没认出来的名字）。"""
    b = b or default_book()
    codes: list[str] = []
    unresolved: list[str] = []
    rows = list(csv.reader(io.StringIO(text.replace("\t", ","))))
    for row in rows:
        line = ",".join(row)
        found = [m.group(1) for m in _CODE_RE.finditer(line)]
        if found:
            codes += [c for c in found if b.get(c) or not b.complete]
            continue
        for cell in row:
            cell = cell.strip()
            if not cell or not re.search(r"[一-龥]", cell) or len(cell) > 12:
                continue
            hits = b.resolve(cell)
            if len(hits) == 1:
                codes.append(hits[0].code)
            elif re.fullmatch(r"[*A-Z一-龥]{2,10}", cell) and cell not in ("代码", "名称", "股票", "简称", "自选股", "持仓"):
                unresolved.append(cell)
    return list(dict.fromkeys(codes)), list(dict.fromkeys(unresolved))


def now_stamp() -> str:
    from .timeutil import now
    return now().isoformat(timespec="seconds")


def merge_relations(old: list[Relation], new: list[Relation]) -> tuple[list[Relation], int]:
    """把新归纳出来的关系并进去：人工维护的、已确认的不动；同一对（代码, 关联方, 关系）已有的不重复加。"""
    keys = {(r.code, r.target, r.kind) for r in old}
    added = [r for r in new if (r.code, r.target, r.kind) not in keys]
    return old + added, len(added)


def parse_quiet(spec: str | None) -> tuple[time, time] | None:
    if not spec:
        return None
    try:
        a, b = (time.fromisoformat(x.strip()) for x in spec.split("-"))
    except ValueError:
        return None
    return a, b


def in_quiet(at: datetime, spec: str | None) -> bool:
    q = parse_quiet(spec)
    if not q:
        return False
    a, b = q
    t = at.time()
    return (a <= t < b) if a <= b else (t >= a or t < b)
