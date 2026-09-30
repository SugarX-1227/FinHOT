"""文本清洗工具。"""

from __future__ import annotations

import html
import re

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
# 快讯常见前缀：【财联社9月30日电】、财联社9月30日电，、金十数据9月30日讯，
_PREFIX_RE = re.compile(
    r"^(?:【[^】]{0,20}(?:电|讯|消息)】|[一-龥A-Za-z]{2,8}\d{1,2}月\d{1,2}日(?:电|讯)[，,：:]?)\s*"
)
_TITLE_RE = re.compile(r"^【([^】]{4,80})】\s*")


def strip_html(s: str | None) -> str:
    if not s:
        return ""
    s = re.sub(r"<br\s*/?>|</p>", "\n", s, flags=re.I)
    s = _TAG_RE.sub("", s)
    s = html.unescape(s)
    return _WS_RE.sub(" ", s).strip()


def split_title(text: str, title: str = "", max_len: int = 60) -> tuple[str, str]:
    """整理快讯的标题与正文，返回 (title, content)。

    - 正文开头的【标题】会被剥离（没有独立标题时用它作标题）；
    - 正文开头的"财联社9月30日电，"之类前缀会被去掉；
    - 仍没有标题时取正文首句。
    """
    text = _WS_RE.sub(" ", text).strip()
    title = _WS_RE.sub(" ", title or "").strip()
    m = _TITLE_RE.match(text)
    if m:
        title = title or m.group(1)
        text = text[m.end():].strip()
    text = _PREFIX_RE.sub("", text).strip() or title
    if not title:
        first = re.split(r"(?<=[。！？!?；;])", text, maxsplit=1)[0]
        title = first if len(first) <= max_len else first[:max_len] + "…"
    title = title.rstrip("。；; ")
    return title, text


def normalize(s: str) -> str:
    """用于去重比较的规范化文本：去前缀、标点、空白，全部小写。"""
    s = _PREFIX_RE.sub("", s.strip())
    s = _TITLE_RE.sub(r"\1", s)
    s = re.sub(r"[^\w一-龥]", "", s)
    return s.lower()
