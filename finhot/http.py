"""带重试、超时、统一 UA 的 HTTP 会话。"""

from __future__ import annotations

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
DEFAULT_TIMEOUT = 15


class Session(requests.Session):
    def __init__(self, timeout: float = DEFAULT_TIMEOUT, retries: int = 2):
        super().__init__()
        self.timeout = timeout
        retry = Retry(
            total=retries,
            backoff_factor=1.0,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=("GET", "POST"),
        )
        adapter = HTTPAdapter(max_retries=retry)
        self.mount("http://", adapter)
        self.mount("https://", adapter)
        self.headers.update({"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"})

    def request(self, method, url, **kwargs):  # type: ignore[override]
        kwargs.setdefault("timeout", self.timeout)
        return super().request(method, url, **kwargs)

    def get_json(self, url: str, **kwargs):
        r = self.get(url, **kwargs)
        r.raise_for_status()
        return r.json()
