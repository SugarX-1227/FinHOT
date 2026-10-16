"""个人数据的账本（都在 FINHOT_PRIVATE_DIR 下，不入库）：

- shown.jsonl：给每个人看过 / 推过的条目（时间、条目 id、标题字面特征、栏目、理由类别、渠道）。
  用来做“同一件事只在有新进展时再出现”，也是周报“你的自选本周发生了什么”和评估的底稿；
- candidates/<画像>/<日期>-<版次>.jsonl：每次出简报时的全部候选，带个人排序分和通用分（盲标对比两种排序用）；
- feedback.jsonl：反馈（有用 / 不相关 / 已知道）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from .profile import private_dir
from .timeutil import TZ

LABELS = {"useful": "有用", "irrelevant": "不相关", "known": "已知道"}


def _append(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


@dataclass
class Shown:
    profile: str
    id: str
    title: str
    grams: list[str]
    at: str
    section: str = ""
    reason_kind: str = ""
    channel: str = "brief"     # brief / push / deferred
    code: str = ""
    url: str = ""


def shown_path() -> Path:
    return private_dir() / "shown.jsonl"


def record_shown(rows: list[Shown]) -> None:
    from dataclasses import asdict
    _append(shown_path(), [asdict(r) for r in rows])


def shown(profile: str, since: datetime | None = None, channel: str | None = None) -> list[dict]:
    out = []
    for r in _read(shown_path()):
        if r.get("profile") != profile or (channel and r.get("channel") != channel):
            continue
        if since and datetime.fromisoformat(r["at"]) < since:
            continue
        out.append(r)
    return out


def recent_grams(profile: str, at: datetime, days: float) -> tuple[set[str], list[set[str]]]:
    """最近几天给这个人看过的条目 id 和标题特征。"""
    rows = shown(profile, at - timedelta(days=days))
    return {r["id"] for r in rows}, [set(r.get("grams") or []) for r in rows]


def save_candidates(profile: str, day: str, edition: str, rows: list[dict]) -> Path:
    path = private_dir() / "candidates" / profile / f"{day}-{edition}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    return path


def load_candidates(profile: str, since: datetime) -> list[dict]:
    out = []
    folder = private_dir() / "candidates" / profile
    for path in sorted(folder.glob("*.jsonl")) if folder.exists() else []:
        if path.stem[:10] >= since.strftime("%Y-%m-%d"):
            out += [dict(r, brief=path.stem) for r in _read(path)]
    return out


# ------------------------------------------------------------------ 反馈
def feedback_path() -> Path:
    return private_dir() / "feedback.jsonl"


def add_feedback(profile: str, item_id: str, label: str, at: datetime, via: str = "cli") -> dict:
    if label not in LABELS:
        raise ValueError(f"反馈只能是 {'/'.join(LABELS)}")
    row = {"profile": profile, "id": item_id, "label": label, "at": at.astimezone(TZ).isoformat(), "via": via}
    _append(feedback_path(), [row])
    return row


def feedback(profile: str, since: datetime | None = None) -> dict[str, str]:
    """{条目 id: 最后一次的反馈}。"""
    out: dict[str, str] = {}
    for r in _read(feedback_path()):
        if r.get("profile") == profile and (not since or datetime.fromisoformat(r["at"]) >= since):
            out[r["id"]] = r["label"]
    return out


def learned_factors(profile: str, w: dict) -> dict[str, float]:
    """按反馈给每类理由算修正系数（sources/personal.yaml 的 feedback）。"""
    from .personal import sigmoid_learned
    cfg = w.get("feedback") or {}
    if not cfg.get("learn", True):
        return {}
    labels = feedback(profile)
    kind_of = {r["id"]: r.get("reason_kind") or "" for r in shown(profile) if r["id"] in labels}
    kinds: dict[str, list[str]] = {}
    for item_id, kind in kind_of.items():
        kinds.setdefault(kind, []).append(labels[item_id])
    out = {}
    for kind, ls in kinds.items():
        if len(ls) >= int(cfg.get("min_labels", 5)):
            out[kind] = sigmoid_learned(ls.count("useful"), ls.count("irrelevant"), tuple(cfg.get("bounds") or (0.5, 1.5)))
    return out
