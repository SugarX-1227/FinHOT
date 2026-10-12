"""网站（web/ 引擎）的两个接口：读精选快照、往“盘面”页推数据。

- 服务器上的推送进程用它读精选、推涨停原因和信号战绩（引擎地址 http://api:3001，token 是 INGEST_TOKEN）；
- GitHub Actions 上的回测用它读精选里的利好 / 利空（FINHOT_SITE_URL，公开接口，不用 token），只出静态站的战绩。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import requests

from .config import env


@dataclass
class Site:
    url: str
    token: str = ""
    session: requests.Session = field(default_factory=requests.Session)

    @classmethod
    def from_env(cls) -> "Site | None":
        url = env("FINHOT_SITE_URL").rstrip("/")
        return cls(url) if url else None

    def selected(self, pages: int = 20) -> list[dict[str, Any]]:
        """精选快照（/api/v1/selected/snapshot），翻到最后一页。"""
        out, page = [], None
        for _ in range(pages):
            params = {"limit": "500", **({"page": page} if page else {})}
            res = self.session.get(f"{self.url}/api/v1/selected/snapshot", params=params, timeout=30)
            res.raise_for_status()
            body = res.json()
            out += body.get("items") or []
            page = body.get("nextPage")
            if not body.get("hasMore") or not page:
                break
        return out

    def push(self, kind: str, key: str, data: dict) -> None:
        """推到“盘面”页（web/modules/market）。"""
        if len(self.token) < 16:
            raise RuntimeError("没有推送 token（web/.env 的 INGEST_TOKEN，至少 16 位）")
        res = self.session.post(f"{self.url}/api/finhot/market", json={"kind": kind, "key": key, "data": data},
                                headers={"Authorization": f"Bearer {self.token}"}, timeout=60)
        if res.status_code == 404:
            raise RuntimeError("网站还没有“盘面”模块（先 deploy/finhot.sh update 更新网站）")
        if res.status_code == 401:
            raise RuntimeError("网站拒绝了推送（401）：token 和网站 web/.env 里的 INGEST_TOKEN 不一致")
        res.raise_for_status()
