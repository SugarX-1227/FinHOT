"""日报版次与调度。

GitHub Actions 的定时任务经常延迟数小时（实测 07:30 的任务 10~11 点才跑），
所以版次不再由"实际运行时刻"决定，而是：
- workflow 在一个时间段内放多个触发点；
- 每次触发先判断当前属于哪个"时段"(slot)，该时段的日报已存在就跳过；
- 采集窗口 = 上一份日报的截止时间 → 现在（再夹在 [min_hours, max_hours] 之间），
  周末/节假日后的第一份日报自然覆盖整个休市期间。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from .config import DATA_DIR, REPORTS_DIR


@dataclass(frozen=True)
class Edition:
    key: str      # pre | noon | close
    name: str     # 显示名
    suffix: str   # 文件名后缀


EDITIONS = {
    "pre": Edition("pre", "盘前版", ""),
    "noon": Edition("noon", "午间版", "-noon"),
    "close": Edition("close", "盘后版", "-close"),
}


def edition_by_time(t: datetime) -> Edition:
    """按北京时间粗分：12 点前盘前版，12~15 点午间版，15 点后盘后版。"""
    m = t.hour * 60 + t.minute
    if m < 12 * 60:
        return EDITIONS["pre"]
    if m < 15 * 60:
        return EDITIONS["noon"]
    return EDITIONS["close"]


def report_path(date: str, ed: Edition, reports_dir: Path = REPORTS_DIR) -> Path:
    return reports_dir / f"{date}{ed.suffix}.md"


def scheduled_slot(t: datetime, reports_dir: Path = REPORTS_DIR) -> Edition | None:
    """定时触发时该生成哪一版；已生成或不在任何时段内则返回 None。

    - 盘前版：03:00~12:00 之间的触发（每天，含周末——周末也有重要消息）
    - 盘后版：15:00~24:00 之间的触发（仅工作日）
    """
    m = t.hour * 60 + t.minute
    date = t.strftime("%Y-%m-%d")
    if 3 * 60 <= m < 12 * 60:
        ed = EDITIONS["pre"]
    elif m >= 15 * 60 and t.weekday() < 5:
        ed = EDITIONS["close"]
    else:
        return None
    return None if report_path(date, ed, reports_dir).exists() else ed


def last_report_until(data_dir: Path = DATA_DIR, before: datetime | None = None) -> datetime | None:
    """最近一份日报的采集截止时间（读 data/<date>/meta*.json）。"""
    days = sorted((p for p in data_dir.glob("20??-??-??") if p.is_dir()), reverse=True)[:7]
    latest = None
    for d in days:
        for meta in d.glob("meta*.json"):
            try:
                until = datetime.fromisoformat(json.loads(meta.read_text(encoding="utf-8"))["until"])
            except (ValueError, KeyError, OSError):
                continue
            if before and until >= before:
                continue
            if latest is None or until > latest:
                latest = until
    return latest


def window_since(until: datetime, hours: float | None, data_dir: Path = DATA_DIR,
                 min_hours: float = 6, max_hours: float = 72, default_hours: float = 24) -> datetime:
    """采集起点：指定了 hours 就用 hours；否则接着上一份日报，夹在 [min_hours, max_hours]。"""
    if hours:
        return until - timedelta(hours=hours)
    last = last_report_until(data_dir, before=until)
    if last is None:
        return until - timedelta(hours=default_hours)
    span = (until - last).total_seconds() / 3600
    span = max(min_hours, min(max_hours, span))
    return until - timedelta(hours=span)
