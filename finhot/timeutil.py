from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Shanghai")


def now() -> datetime:
    return datetime.now(TZ)


def from_ts(ts: float | int | str) -> datetime:
    """Unix 时间戳（秒或毫秒）→ 北京时间。"""
    ts = float(ts)
    if ts > 1e12:
        ts /= 1000
    return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(TZ)


def parse_local(s: str) -> datetime:
    """解析 'YYYY-MM-DD HH:MM:SS' 形式的北京时间字符串。"""
    s = s.strip().replace("/", "-").replace("T", " ")
    for fmt, n in (("%Y-%m-%d %H:%M:%S", 19), ("%Y-%m-%d %H:%M", 16), ("%Y-%m-%d", 10)):
        try:
            return datetime.strptime(s[:n], fmt).replace(tzinfo=TZ)
        except ValueError:
            continue
    raise ValueError(f"无法解析时间: {s!r}")
