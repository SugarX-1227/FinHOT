"""生成服务器上唯一的配置文件 web/.env（网站引擎和推送进程共用）。

以 web/.env.example 为底，填好随机密钥、管理员密码、推送 token，模型默认用智谱 GLM 编程套餐接口。
用法（deploy/finhot.sh init 会调用它）：
  python3 deploy/init_env.py                     # 交互式：问公网 IP、端口和模型 API Key
  python3 deploy/init_env.py --ip 1.2.3.4 --llm-key xxx --port 3000
已经有 web/.env 时不会覆盖，要重来加 --force（会生成新的密码和密钥）。
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import secrets
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / "web" / ".env.example"
TARGET = ROOT / "web" / ".env"

GLM_BASE_URL = "https://open.bigmodel.cn/api/coding/paas/v4"
GLM_MODEL = "glm-5.3-flash"
GLM_EXTRA = {"thinking": {"type": "enabled"}, "reasoning_effort": "low"}

FINHOT_BLOCK = """
# ===== FinHOT 快讯推送进程（pusher 容器） =====
# 簇的规则分达到多少才推给引擎（越低，送进模型的条目越多、越费额度）：
FINHOT_PUSH_MIN_SCORE=6
# 同一件事最多推几家信源（多家报道才能算出热度）：
FINHOT_PUSH_MAX_PER_EVENT=4
# 两轮之间隔多少秒：
FINHOT_PUSH_INTERVAL=90
# 只推这些信源（逗号分隔），留空推 sources/sources.yaml 里所有快讯源：
FINHOT_PUSH_SOURCES=
"""


def public_ip() -> str:
    """腾讯云（含轻量服务器）的实例元数据服务能查到公网 IP；查不到就返回空串。"""
    try:
        with urllib.request.urlopen("http://metadata.tencentyun.com/latest/meta-data/public-ipv4", timeout=2) as r:
            ip = r.read().decode().strip()
            return ip if re.fullmatch(r"\d+\.\d+\.\d+\.\d+", ip) else ""
    except OSError:
        return ""


def set_value(lines: list[str], key: str, value: str) -> list[str]:
    """把 KEY=... 或注释掉的 # KEY=... 换成 KEY=value；没有就加在末尾。"""
    pat = re.compile(rf"^#?\s*{re.escape(key)}=")
    for i, line in enumerate(lines):
        if pat.match(line):
            lines[i] = f"{key}={value}"
            return lines
    lines.append(f"{key}={value}")
    return lines


def build(example: str, ip: str, port: int, llm_key: str) -> tuple[str, str]:
    admin = secrets.token_urlsafe(12)
    values = {
        "SITE_URL": f"http://{ip}:{port}",
        "ADMIN_PASSWORD": admin,
        "SESSION_SECRET": secrets.token_hex(32),
        "IMG_PROXY_SIGN_SECRET": secrets.token_hex(32),
        "POSTGRES_PASSWORD": secrets.token_hex(24),
        "LLM_BASE_URL": GLM_BASE_URL,
        "LLM_API_KEY": llm_key,
        "LLM_MODEL": GLM_MODEL,
        "LLM_EXTRA_JSON": json.dumps(GLM_EXTRA, separators=(",", ":")),
        "LLM_REASONING_TOKENS": "4000",
        "INGEST_TOKEN": secrets.token_hex(24),
        "INGEST_RATE_LIMIT": "60",
        "PORT": str(port),
        "COLLECT_ENABLED": "true",
        "MODEL_CALLS_ENABLED": "true",
    }
    lines = example.splitlines()
    lines[0] = "# FinHOT 服务器配置（deploy/init_env.py 生成）。不要提交到 Git，不要发给别人。"
    for k, v in values.items():
        lines = set_value(lines, k, v)
    return "\n".join(lines).rstrip() + "\n" + FINHOT_BLOCK, admin


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="生成 web/.env")
    p.add_argument("--ip", help="服务器公网 IP（默认自动查询）")
    p.add_argument("--port", type=int, default=3000, help="网站端口（默认 3000，记得在防火墙放行）")
    p.add_argument("--llm-key", help="智谱 API Key（默认交互输入）")
    p.add_argument("--force", action="store_true", help="覆盖已有的 web/.env")
    p.add_argument("--out", type=Path, default=TARGET, help=argparse.SUPPRESS)
    args = p.parse_args(argv)

    if args.out.exists() and not args.force:
        print(f"{args.out} 已经存在，不覆盖。要重新生成加 --force（管理员密码和所有密钥都会换新）。")
        return 1
    ip = args.ip or public_ip()
    if not ip:
        ip = input("服务器公网 IP（腾讯云控制台实例详情里能看到）：").strip()
    key = args.llm_key or getpass.getpass("智谱 API Key（输入时不显示）：").strip()
    if not ip or not key:
        print("公网 IP 和 API Key 都要填。")
        return 1

    text, admin = build(EXAMPLE.read_text(encoding="utf-8"), ip, args.port, key)
    args.out.write_text(text, encoding="utf-8")
    os.chmod(args.out, 0o600)
    print(f"已生成 {args.out}")
    print(f"网站地址：http://{ip}:{args.port}    后台：http://{ip}:{args.port}/admin")
    print(f"管理员密码（只显示这一次，请记下来）：{admin}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
