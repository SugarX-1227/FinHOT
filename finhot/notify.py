"""把个人简报和即时提醒发到私人渠道：飞书 / 企业微信的群机器人 webhook。

- 每人一个私人群（只有自己和机器人），webhook 地址写在自己的画像里或服务器环境变量里，不入库；
- 每人每天有上限（画像的 push.daily_limit，默认见 sources/personal.yaml），免打扰时段里的即时提醒不发，
  记成“延后”，下一份简报照常包含它们（简报的时间窗口覆盖免打扰时段）；
- 同一条只推一次（finhot/history.py 的账本）；
- 群机器人是单向的，收不到按钮点击。反馈用消息里的签名链接（点开就记一条反馈，见 finhot/feedback_server.py），
  没配 FINHOT_FEEDBACK_URL 时不带链接，用命令行记：python -m finhot feedback add。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import time
from urllib.parse import urlencode

import requests

from .config import env
from .profile import Profile

log = logging.getLogger(__name__)
MAX_CHARS = {"feishu": 15000, "wecom": 3800}   # 企业微信 markdown 最长 4096 字节


class NotifyError(RuntimeError):
    pass


def feishu_sign(secret: str, ts: int) -> str:
    """飞书自定义机器人的签名校验：base64(hmac_sha256(key=f"{ts}\\n{secret}", msg=""))。"""
    key = f"{ts}\n{secret}".encode()
    return base64.b64encode(hmac.new(key, b"", hashlib.sha256).digest()).decode()


def feishu_body(title: str, markdown: str, secret: str = "", ts: int | None = None) -> dict:
    body: dict = {"msg_type": "interactive", "card": {
        "config": {"wide_screen_mode": True},
        "header": {"title": {"tag": "plain_text", "content": title}, "template": "blue"},
        "elements": [{"tag": "markdown", "content": markdown[:MAX_CHARS["feishu"]]}]}}
    if secret:
        ts = ts or int(time.time())
        body.update(timestamp=str(ts), sign=feishu_sign(secret, ts))
    return body


def wecom_body(title: str, markdown: str) -> dict:
    text = f"**{title}**\n{markdown}"
    while len(text.encode()) > 4000:
        text = text[: int(len(text) * 0.9)] + "…"
    return {"msgtype": "markdown", "markdown": {"content": text}}


def send(p: Profile, title: str, markdown: str, session: requests.Session | None = None) -> None:
    channel, url = p.push.channel, p.push.url
    if channel == "none":
        return
    if not url:
        raise NotifyError(f"画像 {p.name} 的推送地址是空的")
    body = feishu_body(title, markdown, p.push.key) if channel == "feishu" else wecom_body(title, markdown)
    s = session or requests.Session()
    res = s.post(url, json=body, timeout=15)
    try:
        data = res.json()
    except ValueError:
        data = {}
    code = data.get("code", data.get("StatusCode", data.get("errcode", 0)))
    if res.status_code >= 400 or code not in (0, None):
        raise NotifyError(f"{channel} 返回 {res.status_code} {data.get('msg') or data.get('errmsg') or ''}".strip())


# ------------------------------------------------------------------ 反馈链接
def feedback_sign(profile: str, item_id: str, label: str, secret: str | None = None) -> str:
    secret = secret if secret is not None else env("FINHOT_FEEDBACK_SECRET")
    return hmac.new(secret.encode(), f"{profile}|{item_id}|{label}".encode(), hashlib.sha256).hexdigest()[:20]


def feedback_links(profile: str, item_id: str) -> str:
    """“有用 · 不相关 · 已知道”三个链接；没配 FINHOT_FEEDBACK_URL / FINHOT_FEEDBACK_SECRET 时返回空串。"""
    base, secret = env("FINHOT_FEEDBACK_URL").rstrip("/"), env("FINHOT_FEEDBACK_SECRET")
    if not base or len(secret) < 16:
        return ""
    from .history import LABELS
    parts = []
    for label, text in LABELS.items():
        q = urlencode({"p": profile, "i": item_id, "v": label, "s": feedback_sign(profile, item_id, label, secret)})
        parts.append(f"[{text}]({base}/fb?{q})")
    return " · ".join(parts)
