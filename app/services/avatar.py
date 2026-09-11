"""创作者头像：下载并缓存到本地，避免平台热链接失效。"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

import httpx

from ..config import config

# 头像缓存目录：<data_dir>/avatars
AVATAR_DIRNAME = "avatars"
DEFAULT_TIMEOUT = 15
MAX_BYTES = 5 * 1024 * 1024  # 头像通常几十 KB，5MB 足够兜底


def avatar_dir() -> Path:
    d = Path(config.data_dir) / AVATAR_DIRNAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def _upgrade_douyin_url(url: str) -> str:
    """把抖音 CDN 头像 URL 尺寸段升格为 1080x1080 高清档。

    实测有效档位：100x100 / 200x200 / 720x720 / 1080x1080（中间值 400）。
    旧数据头像 URL 常为 avatar_thumb=100x100，下载仅 2KB；此处统一升格。
    """
    if not url or not re.search(r"(douyinpic\.com|byteimg\.com)", url):
        return url
    if not re.search(r"/(aweme/)?\d{2,4}x\d{2,4}/", url):
        return url
    return re.sub(r"/(aweme/)?\d{2,4}x\d{2,4}/", r"/\g<1>1080x1080/", url, count=1)


def _safe_ext(url: str, content_type: str = "") -> str:
    """从 URL 或 MIME 推断扩展名，默认 jpg。"""
    ctype = (content_type or "").split(";")[0].strip().lower()
    mapping = {
        "image/jpeg": ".jpg", "image/jpg": ".jpg", "image/png": ".png",
        "image/webp": ".webp", "image/gif": ".gif", "image/avif": ".avif",
    }
    if ctype in mapping:
        return mapping[ctype]
    m = re.search(r"\.(jpg|jpeg|png|webp|gif|avif)(?:\?|$)", url or "", re.I)
    if m:
        return "." + m.group(1).lower().replace("jpeg", "jpg")
    return ".jpg"


def download_avatar(avatar_url: str, platform: str, platform_id: str) -> str | None:
    """下载头像到本地，返回相对 data/ 的路径（如 avatars/xx.jpg）；失败返回 None。

    同名覆盖：一个创作者只保留一张头像，换头像时重新下载覆盖。
    """
    if not avatar_url or not avatar_url.startswith(("http://", "https://")):
        return None
    if not platform_id:
        return None
    try:
        key = f"{platform}:{platform_id}"
        name = hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
        target_dir = avatar_dir()
        # 抖音小图升格高清档（avatar_thumb=100x100 → 1080x1080）
        url = _upgrade_douyin_url(avatar_url)
        with httpx.Client(timeout=DEFAULT_TIMEOUT, follow_redirects=True,
                          trust_env=False) as client:
            resp = client.get(url)
            resp.raise_for_status()
            data = resp.content
        if not data or len(data) > MAX_BYTES:
            return None
        # 若升格失败实际仍是小图（<8KB 且原是小图 URL），跳过避免覆盖成小图
        if len(data) < 8 * 1024 and _upgrade_douyin_url(avatar_url) != avatar_url:
            print(f"[avatar] 高清档下载异常（{len(data)}B），跳过覆盖 {platform}:{platform_id}")
            return None
        # 清掉旧扩展名的同名文件（换 ext 时避免残留）
        for old in target_dir.glob(f"{name}.*"):
            try:
                old.unlink()
            except OSError:
                pass
        ext = _safe_ext(url, resp.headers.get("content-type", ""))
        target = target_dir / f"{name}{ext}"
        target.write_bytes(data)
        # 返回相对 data_dir 的路径，便于前端拼接
        return f"{AVATAR_DIRNAME}/{target.name}"
    except Exception as exc:
        print(f"[avatar] 下载失败 {platform}:{platform_id} - {exc}")
        return None


def avatar_file_path(rel_path: str | None) -> Path | None:
    """相对路径 → 本地绝对路径；不存在返回 None。"""
    if not rel_path:
        return None
    p = Path(config.data_dir) / rel_path
    return p if p.exists() else None
