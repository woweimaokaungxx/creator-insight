"""PlatformAdapter 抽象接口与注册表（docs/13 契约）"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ..models import PredictionIn  # 仅类型引用，实际由 pipeline 使用


@dataclass
class ParsedURL:
    """输入解析结果：分享文案/短链/长链统一出口"""
    platform: str                 # 'douyin' | 'bilibili'
    content_id: str               # 平台视频 ID（已归一化）
    creator_hint: Optional[str] = None   # 分享文案中可能的博主名
    title_hint: Optional[str] = None     # 分享文案中的标题
    raw_input: str = ""
    url: str = ""


@dataclass
class CreatorInfo:
    platform: str
    platform_id: str
    name: str
    url: str
    domain_tags: list[str] = field(default_factory=list)
    avatar_url: str = ""                  # 平台远程头像地址


@dataclass
class ContentInfo:
    platform: str
    platform_vid: str
    title: str
    url: str
    creator_platform_id: str
    creator_name: str = ""
    creator_avatar_url: str = ""          # 视频作者头像（随视频元数据一起拿到）
    published_at: Optional[int] = None
    duration_sec: Optional[int] = None
    media_path: Optional[Path] = None
    cover_path: Optional[Path] = None
    raw_meta: dict = field(default_factory=dict)
    # 互动数据（点赞/评论/转发/收藏），缺失为 None
    digg_count: Optional[int] = None
    comment_count: Optional[int] = None
    share_count: Optional[int] = None
    collect_count: Optional[int] = None


@dataclass
class TranscriptResult:
    source: str                    # 'platform_subtitle' | 'whisper_local' | 'cloud_asr'
    language: str = "zh"
    text_full: str = ""
    segments: list[dict] = field(default_factory=list)  # [{start,end,text}]
    media_path: Optional[Path] = None


class PlatformAdapter(ABC):
    """每个平台一个实现，只负责获取，不触碰 AI/DB/验证/Obsidian。"""

    platform: str = "base"

    @abstractmethod
    def parse_input(self, raw_input: str) -> ParsedURL:
        """解析粘贴的 URL 或分享文案，识别平台与视频 ID。"""
        ...

    @abstractmethod
    def fetch_creator(self, platform_id: str) -> CreatorInfo:
        """获取博主基本信息。"""
        ...

    @abstractmethod
    def fetch_content_meta(self, parsed: ParsedURL) -> ContentInfo:
        """获取单条视频元数据。"""
        ...

    @abstractmethod
    def fetch_transcript(self, content: ContentInfo) -> TranscriptResult | None:
        """优先平台字幕；无法获取返回 None（由调用方决定是否走 Whisper）。"""
        ...

    def download_media(self, content: ContentInfo, target_dir: Path) -> Path | None:
        """下载视频（可选；用于 Whisper 兜底与回听）。"""
        return None

    def fetch_creator_videos(self, creator_id: str, cursor=None) -> list[ContentInfo]:
        """博主视频列表（V0.4 自动监控用；V0.1 可抛 NotImplementedError）。"""
        raise NotImplementedError(f"{self.platform} 暂不支持创作者列表抓取")


# ─── 注册表 ────────────────────────────────────────────────
_REGISTRY: dict[str, type[PlatformAdapter]] = {}


def register(adapter_cls: type[PlatformAdapter]) -> type[PlatformAdapter]:
    _REGISTRY[adapter_cls.platform] = adapter_cls
    return adapter_cls


def get_adapter(platform: str) -> PlatformAdapter:
    if platform not in _REGISTRY:
        raise ValueError(f"未注册的平台适配器: {platform}（可用: {list(_REGISTRY)}）")
    return _REGISTRY[platform]()


def guess_platform(raw_input: str) -> str | None:
    """根据输入内容猜测平台，供入口路由使用。

    先用域名/关键词特征快速判断，再交由对应 adapter 解析验证。
    """
    import re
    text = raw_input.lower()
    # 域名特征（最高优先级）
    domain_platforms = [
        ("douyin.com", "douyin"), ("iesdouyin.com", "douyin"),
        ("bilibili.com", "bilibili"), ("b23.tv", "bilibili"),
    ]
    for domain, plat in domain_platforms:
        if domain in text:
            try:
                get_adapter(plat).parse_input(raw_input)
                return plat
            except Exception:
                return plat  # 域名明确，即使解析失败也返回该平台
    # 关键词特征（B 站视频号 / av 号）
    if re.search(r"BV[0-9A-Za-z]{10}|av\d+", text):
        return "bilibili"
    # 抖音口令特征（复制打开抖音 / v.douyin）
    if "douyin" in text or "抖音" in text:
        return "douyin"
    return None
