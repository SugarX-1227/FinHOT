"""收反馈的小服务：消息里的“有用 / 不相关 / 已知道”是带签名的链接，点开就记一条反馈（finhot/history.py）。

群机器人的消息是单向的，收不到按钮点击，所以用链接。签名（FINHOT_FEEDBACK_SECRET，至少 16 位）防止别人伪造，
签名里带着链接生成的时间，14 天后失效；链接里只有画像名和条目 id，没有持仓。推送进程设置了 FINHOT_FEEDBACK_SECRET 和 FINHOT_FEEDBACK_PORT 时在后台线程里起这个服务，
也可以单独跑：python -m finhot feedback serve。对外的地址写在 FINHOT_FEEDBACK_URL（如 http://服务器IP:8790）。
"""

from __future__ import annotations

import hmac
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .config import env
from .history import LABELS, add_feedback
from .notify import FEEDBACK_TTL_DAYS, feedback_sign
from .timeutil import now

log = logging.getLogger(__name__)
PAGE = ("<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
        "<title>FinHOT 反馈</title><body style='font:16px/1.6 system-ui;padding:2em;max-width:30em;margin:auto'>"
        "<p>{msg}</p><p style='color:#888;font-size:14px'>可以关掉这个页面了。</p>")


def handle(query: str, secret: str, at: float | None = None) -> tuple[int, str]:
    q = {k: v[0] for k, v in parse_qs(query).items()}
    profile, item, label, sig = q.get("p", ""), q.get("i", ""), q.get("v", ""), q.get("s", "")
    try:
        ts = int(q.get("t", ""))
    except ValueError:
        ts = 0
    if label not in LABELS or not profile or not item or not ts or len(secret) < 16:
        return 400, "链接不完整。"
    if not hmac.compare_digest(sig, feedback_sign(profile, item, label, ts, secret)):
        return 403, "链接签名不对，没有记录。"
    if (at or time.time()) - ts > FEEDBACK_TTL_DAYS * 86400:
        return 410, f"链接已经过期（{FEEDBACK_TTL_DAYS} 天），没有记录。"
    try:
        add_feedback(profile, item, label, now(), via="link")
    except Exception as e:  # noqa: BLE001
        log.warning("反馈没记上：%s", e)
        return 500, "没记上，稍后再试。"
    return 200, f"已记录：{LABELS[label]}。谢谢！"


class Handler(BaseHTTPRequestHandler):
    secret = ""

    def do_GET(self):  # noqa: N802
        url = urlparse(self.path)
        code, msg = handle(url.query, self.secret) if url.path == "/fb" else (404, "没有这个页面。")
        body = PAGE.format(msg=msg).encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):  # 不把查询串（画像名）打进日志
        log.debug("feedback %s", self.path.split("?", 1)[0])


def serve(port: int | None = None, background: bool = False) -> ThreadingHTTPServer | None:
    secret = env("FINHOT_FEEDBACK_SECRET")
    if len(secret) < 16:
        log.info("没有设置 FINHOT_FEEDBACK_SECRET（至少 16 位），不起反馈服务")
        return None
    Handler.secret = secret
    server = ThreadingHTTPServer(("0.0.0.0", port or int(env("FINHOT_FEEDBACK_PORT", "8790"))), Handler)
    log.info("反馈服务在 %s 端口", server.server_port)
    if background:
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return server
    server.serve_forever()
    return server
