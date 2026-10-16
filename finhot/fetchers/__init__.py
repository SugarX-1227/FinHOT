from . import flash, notices, official  # noqa: F401  —— 导入即注册
from .base import REGISTRY, FetchContext, FetchResult, run_fetch

__all__ = ["REGISTRY", "FetchContext", "FetchResult", "run_fetch"]
