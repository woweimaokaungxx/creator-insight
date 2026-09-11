from .base import (
    ContentInfo, CreatorInfo, ParsedURL, PlatformAdapter,
    TranscriptResult, get_adapter, guess_platform, register,
)
from . import bilibili, douyin  # noqa: F401  触发 register 装饰器

__all__ = [
    "ContentInfo", "CreatorInfo", "ParsedURL", "PlatformAdapter",
    "TranscriptResult", "get_adapter", "guess_platform", "register",
]
