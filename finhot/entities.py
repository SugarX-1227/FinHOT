"""实体库：全 A 股代码、简称、曾用名、所属行业，加上人工维护的别名、易混简称和 H 股 / 美股代码。

- `sources/entities/stocks.json`：股票清单，`python -m finhot entities sync` 生成（巨潮的股票清单，带查公告要用的 orgId；
  能连上东方财富时顺带补上所属行业）。这是公开数据，可以入库，GitHub Actions 每天同步一次；
  服务器上镜像里的目录写不了时存到数据目录（data/entities/stocks.json），读的时候取两份里较新的一份。
- `sources/entities/aliases.yaml`：人工维护：新闻里常见的别称（茅台 → 贵州茅台）、本身是常用词的简称（机器人、太阳能）、
  H 股 / 美股代码。

每次同步和上一份比较：简称变了的把旧名记进 former（曾用名），名字带 ST 的标出来，清单里没有了的标成退市（保留，
老新闻还能对上）。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
from functools import lru_cache
from pathlib import Path

import yaml

from .config import DATA_DIR, ROOT

log = logging.getLogger(__name__)

ENTITY_DIR = ROOT / "sources" / "entities"
STOCKS_FILE = ENTITY_DIR / "stocks.json"
ALIASES_FILE = ENTITY_DIR / "aliases.yaml"
CNINFO_STOCKS = "http://www.cninfo.com.cn/new/data/szse_stock.json"
_DELISTED_NAME = re.compile(r"退$|退市|^PT")
_ST = re.compile(r"^\*?S?\*?ST\s*|^S\s+|^N(?=[一-龥])|^C(?=[一-龥])")   # *ST、ST、S（未股改）、N / C（新股上市首日 / 前五日）


@dataclass
class Stock:
    code: str
    name: str
    exchange: str = ""          # SH / SZ / BJ
    org_id: str = ""            # 巨潮的机构 id（按公司查公告要用）
    pinyin: str = ""
    industry: str = ""          # 东方财富行业
    former: list[str] = field(default_factory=list)   # 曾用名（同步时发现的改名）
    st: bool = False
    status: str = "listed"      # listed | delisted
    hk: str = ""                # H 股代码，如 01211.HK（人工维护）
    us: str = ""
    aliases: list[str] = field(default_factory=list)  # 人工维护的别称

    @property
    def bare(self) -> str:
        """去掉 *ST / N / C 前缀的简称（新闻里常省掉）。"""
        return _ST.sub("", self.name).strip()


def exchange_of(code: str) -> str:
    if code.startswith(("6", "9")) and not code.startswith("920"):
        return "SH"
    if code.startswith(("4", "8", "920")):
        return "BJ"
    return "SZ"


def secid(code: str) -> str:
    """东方财富行情接口的证券 id：沪市 1.，深市和北交所 0.。"""
    return f"{'1' if exchange_of(code) == 'SH' else '0'}.{code}"


# ------------------------------------------------------------------ 同步（纯函数便于离线测试）
def parse_cninfo_stocks(data: dict) -> list[dict]:
    """巨潮股票清单：{"stockList": [{"orgId", "category": "A股", "code", "pinyin", "zwjc"}]}，只留 A 股。"""
    out = []
    for r in data.get("stockList") or []:
        code = str(r.get("code") or "").strip()
        if r.get("category") not in (None, "", "A股") or not re.fullmatch(r"\d{6}", code):
            continue
        name = re.sub(r"\s+", "", str(r.get("zwjc") or ""))
        if name:
            out.append({"code": code, "name": name, "org_id": str(r.get("orgId") or ""), "pinyin": str(r.get("pinyin") or "")})
    return out


def parse_em_stock_list(data: dict) -> dict[str, str]:
    """东方财富 A 股列表（clist，字段 f12 代码、f100 行业）→ {代码: 行业}。列表里有就是在市（新股的行业可能是空的）。"""
    return {str(d["f12"]): ("" if d.get("f100") in (None, "-") else str(d["f100"]).strip())
            for d in ((data.get("data") or {}).get("diff") or []) if d.get("f12")}


def merge_sync(old: list[Stock], fresh: list[dict], industries: dict[str, str] | None = None) -> tuple[list[Stock], dict]:
    """把新取到的清单并进旧的：新股加进来、改名记曾用名、ST 标出来、清单里没有了的标退市。返回（新清单, 变化）。

    巨潮的清单里也有早已退市的（“国华退”“PT金田A”）和北交所改成 920 开头之前的老代码；industries（东方财富
    当前在市的 A 股）取到时，不在里面的都算退市。"""
    industries = industries or {}
    by_code = {s.code: replace(s, former=list(s.former)) for s in old}   # 不改调用方的对象
    changes: dict[str, list[str]] = {"新增": [], "改名": [], "退市": [], "恢复": []}
    seen = set()
    for r in fresh:
        code, name = r["code"], r["name"]
        seen.add(code)
        s = by_code.get(code)
        if s is None:
            s = by_code[code] = Stock(code=code, name=name, exchange=exchange_of(code))
            changes["新增"].append(f"{code} {name}")
        elif s.name != name:
            if _ST.sub("", s.name) != _ST.sub("", name) and s.name not in s.former:  # 只是戴帽摘帽不算改名
                s.former.append(s.name)
            changes["改名"].append(f"{code} {s.name} → {name}")
            s.name = name
        listed = not _DELISTED_NAME.search(name) and (not industries or code in industries)
        if listed and s.status != "listed":
            changes["恢复"].append(f"{code} {name}")
        elif not listed and s.status == "listed":
            changes["退市"].append(f"{code} {name}")
        s.status = "listed" if listed else "delisted"
        s.org_id = r.get("org_id") or s.org_id
        s.pinyin = r.get("pinyin") or s.pinyin
        s.exchange = s.exchange or exchange_of(code)
        s.st = "ST" in name.upper()
        if industries.get(code):
            s.industry = industries[code]
    if fresh:  # 什么都没取到时不能把全部标成退市
        for code, s in by_code.items():
            if code not in seen and s.status == "listed":
                s.status = "delisted"
                changes["退市"].append(f"{code} {s.name}")
    return sorted(by_code.values(), key=lambda s: s.code), {k: v for k, v in changes.items() if v}


def _local_file(data_dir: Path) -> Path:
    return data_dir / "entities" / "stocks.json"


def _read(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def load_file(path: Path | None = None, data_dir: Path = DATA_DIR) -> dict:
    """股票清单文件内容：{updated, source, complete, stocks: [...]}；随代码发布的和数据目录里的取较新的。"""
    docs = [d for d in (_read(path or STOCKS_FILE), _read(_local_file(data_dir))) if d]
    if not docs:
        return {"updated": "", "source": "", "complete": False, "stocks": []}
    return max(docs, key=lambda d: d.get("updated") or "")


def save_file(doc: dict, path: Path | None = None, data_dir: Path = DATA_DIR) -> Path:
    text = json.dumps({**doc, "stocks": doc["stocks"]}, ensure_ascii=False, indent=0)
    for target in (path or STOCKS_FILE, _local_file(data_dir)):
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text + "\n", encoding="utf-8")
            return target
        except OSError as e:
            log.info("股票清单写不进 %s（%s），换个地方存", target, e)
    raise OSError("股票清单哪里都写不了")


def sync(session=None, path: Path | None = None, data_dir: Path = DATA_DIR, now: datetime | None = None) -> tuple[Path, dict]:
    """取巨潮股票清单（必须成功）和东方财富行业（取不到就保留原来的），并进已有清单。"""
    from .http import Session
    from .timeutil import now as _now
    session = session or Session()
    fresh = parse_cninfo_stocks(session.get_json(CNINFO_STOCKS))
    if len(fresh) < 3000:
        raise RuntimeError(f"巨潮股票清单只有 {len(fresh)} 条，不像是全量，不更新")
    industries: dict[str, str] = {}
    try:
        industries = fetch_industries(session)
    except Exception as e:  # noqa: BLE001 —— 行业是锦上添花，东财在部分机房 IP 上取不到
        log.warning("东方财富行业取不到，保留原来的: %s", e)
    doc = load_file(path, data_dir)
    stocks, changes = merge_sync([Stock(**s) for s in doc["stocks"]], fresh, industries)
    out = save_file({"updated": (now or _now()).isoformat(timespec="seconds"), "source": "巨潮资讯股票清单"
                     + ("+东方财富行业" if industries else ""), "complete": True,
                     "stocks": [asdict(s) for s in stocks]}, path, data_dir)
    book.cache_clear()
    return out, changes


def fetch_industries(session) -> dict[str, str]:
    from .ashare import PUSH2_HOSTS, push2_json
    out: dict[str, str] = {}
    for pn in range(1, 80):
        data = push2_json(session, "/api/qt/clist/get", {
            "pn": str(pn), "pz": "100", "po": "1", "np": "1", "fltt": "2", "invt": "2", "fid": "f12",
            "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048", "fields": "f12,f100"}, hosts=PUSH2_HOSTS)
        rows = parse_em_stock_list(data)
        out.update(rows)
        if len(((data.get("data") or {}).get("diff") or [])) < 100:
            break
    return out


# ------------------------------------------------------------------ 查询
@dataclass
class Book:
    stocks: dict[str, Stock]
    names: dict[str, list[str]]       # 简称 / 曾用名 / 别称 → 代码
    generic: set[str]                 # 本身是常用词的简称
    complete: bool = False            # 是不是全市场清单（种子清单只有几百只）
    updated: str = ""

    def resolve(self, name: str) -> list[Stock]:
        """简称对应的股票；有在市的就只给在市的（北交所老代码、退市的同名公司排除掉）。"""
        hits = [self.stocks[c] for c in self.names.get(name, []) if c in self.stocks]
        return [s for s in hits if s.status == "listed"] or hits

    def get(self, code: str) -> Stock | None:
        return self.stocks.get(code)

    def is_company(self, name: str) -> bool:
        return name in self.names

    def label(self, code: str) -> str:
        s = self.stocks.get(code)
        return f"{s.name}（{code}）" if s else code


def build(doc: dict, aliases: dict | None = None) -> Book:
    aliases = aliases or {}
    stocks = {s["code"]: Stock(**{k: v for k, v in s.items() if k in Stock.__dataclass_fields__}) for s in doc.get("stocks") or []}
    for code, extra in (aliases.get("stocks") or {}).items():
        code = str(code).zfill(6)
        s = stocks.get(code)
        if s is None:
            s = stocks[code] = Stock(code=code, name=str(extra.get("name") or code), exchange=exchange_of(code))
        s.aliases = [str(a) for a in extra.get("aliases") or []]
        s.hk, s.us = str(extra.get("hk") or s.hk), str(extra.get("us") or s.us)
        s.industry = s.industry or str(extra.get("industry") or "")
    names: dict[str, list[str]] = {}

    def add(name: str, code: str) -> None:
        if len(name) >= 2 and code not in names.setdefault(name, []):
            names[name].append(code)

    for s in stocks.values():
        for n in (s.name, s.bare, *s.aliases):
            add(n, s.code)
    for s in stocks.values():      # 曾用名排在后面：现名优先
        for n in s.former:
            add(n, s.code)
    generic = {str(g) for g in aliases.get("generic") or []}
    return Book(stocks, names, generic, bool(doc.get("complete")), doc.get("updated") or "")


@lru_cache(maxsize=1)
def book() -> Book:
    try:
        aliases = yaml.safe_load(ALIASES_FILE.read_text(encoding="utf-8")) or {}
    except OSError:
        aliases = {}
    return build(load_file(), aliases)
