"""B 站 Adapter：使用公开 API 获取元数据与字幕"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Optional

import httpx

from .base import (
    ContentInfo, CreatorInfo, ParsedURL, PlatformAdapter, TranscriptResult, register,
)

URL_RE = re.compile(r"https?://[^\s，。！？；：、\"''【】<>《》()（）]+")
BV_RE = re.compile(r"(BV[0-9A-Za-z]{10})|(av\d+)")


def _to_int(v) -> int | None:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


@register
class BilibiliAdapter(PlatformAdapter):
    platform = "bilibili"

    def parse_input(self, raw_input: str) -> ParsedURL:
        raw = raw_input.strip()
        urls = URL_RE.findall(raw)
        url = urls[0] if urls else raw
        m = re.search(r"(?:bilibili\.com/video/)?(BV[0-9A-Za-z]{10})", url)
        if m:
            bvid = m.group(1)
            return ParsedURL(platform="bilibili", content_id=bvid, url=url, raw_input=raw)
        m = re.search(r"(av\d+)", url)
        if m:
            return ParsedURL(platform="bilibili", content_id=m.group(1), url=url, raw_input=raw)
        raise ValueError("未找到 B 站视频 ID")

    def _headers(self):
        return {"User-Agent": UA, "Referer": "https://www.bilibili.com/"}

    def _view_api(self, bvid_or_av: str) -> dict:
        params = {}
        if bvid_or_av.startswith("BV"):
            params["bvid"] = bvid_or_av
        else:
            params["aid"] = bvid_or_av.replace("av", "")
        with httpx.Client(headers=self._headers(), timeout=20,
                          follow_redirects=True) as client:
            resp = client.get("https://api.bilibili.com/x/web-interface/view", params=params)
            if resp.status_code == 200:
                return resp.json()
            return {}

    def _subtitle_api(self, bvid: str, cid: int) -> list[dict]:
        """拉取 CC 字幕列表（需要用户 cookie，V0.1 可空跑）"""
        params = {"bvid": bvid, "cid": cid}
        with httpx.Client(headers=self._headers(), timeout=20,
                          follow_redirects=True) as client:
            resp = client.get("https://api.bilibili.com/x/player/v2", params=params)
            if resp.status_code == 200:
                data = resp.json()
                subs = (data.get("data") or {}).get("subtitle", {}).get("subtitles") or []
                return [s for s in subs if s.get("lan_doc")]
            return []

    def fetch_creator(self, platform_id: str) -> CreatorInfo:
        return CreatorInfo(
            platform="bilibili", platform_id=platform_id,
            name="B 站用户",
            url=f"https://space.bilibili.com/{platform_id}",
        )

    def fetch_content_meta(self, parsed: ParsedURL) -> ContentInfo:
        # 优先 API；412 风控时用 yt-dlp 兜底
        data = self._view_api(parsed.content_id)
        v = (data or {}).get("data") or {}
        if not v:
            return self._meta_via_ytdlp(parsed)
        owner = v.get("owner") or {}
        stat = v.get("stat") or {}
        # B站头像在 owner.face
        avatar = owner.get("face") or ""
        if isinstance(avatar, str) and avatar.startswith("//"):
            avatar = "https:" + avatar
        return ContentInfo(
            platform="bilibili",
            platform_vid=str(v.get("bvid") or parsed.content_id),
            title=v.get("title") or "",
            url=f"https://www.bilibili.com/video/{v.get('bvid')}",
            creator_platform_id=str(owner.get("mid") or ""),
            creator_name=owner.get("name") or "",
            creator_avatar_url=avatar,
            published_at=v.get("pubdate"),
            duration_sec=v.get("duration"),
            raw_meta={"view": stat},
            digg_count=_to_int(stat.get("like")),
            comment_count=_to_int(stat.get("reply")),
            share_count=_to_int(stat.get("share")),
            collect_count=_to_int(stat.get("favorite")),
        )

    def _meta_via_ytdlp(self, parsed: ParsedURL) -> ContentInfo:
        """B 站 API 被风控（412）时用 yt-dlp 获取元数据"""
        try:
            from yt_dlp import YoutubeDL
        except ImportError:
            return ContentInfo(
                platform="bilibili", platform_vid=parsed.content_id,
                title="", url=parsed.url, creator_platform_id="", raw_meta={},
            )
        opts = {"quiet": True, "no_warnings": True, "skip_download": True,
                "noplaylist": True}
        try:
            with YoutubeDL(opts) as ydl:
                info = ydl.extract_info(parsed.url, download=False)
            bvid = info.get("id") or parsed.content_id
            return ContentInfo(
                platform="bilibili",
                platform_vid=bvid,
                title=info.get("title") or "",
                url=f"https://www.bilibili.com/video/{bvid}",
                creator_platform_id=str(info.get("uploader_id") or ""),
                creator_name=info.get("uploader") or "",
                published_at=int(info["timestamp"]) if info.get("timestamp") else None,
                duration_sec=int(info.get("duration") or 0) or None,
                raw_meta={"ytdlp": True},
            )
        except Exception:
            return ContentInfo(
                platform="bilibili", platform_vid=parsed.content_id,
                title="", url=parsed.url, creator_platform_id="", raw_meta={},
            )

    def fetch_transcript(self, content: ContentInfo) -> TranscriptResult | None:
        """B 站 CC 字幕（若用户登录则可拿），否则返回 None 走 Whisper"""
        try:
            cid = self._resolve_cid(content.platform_vid)
            if not cid:
                return None
            subs = self._subtitle_api(content.platform_vid, cid)
            if not subs:
                return None
            # 取第一个字幕（通常为中文）
            sub = subs[0]
            with httpx.Client(headers=self._headers(), timeout=20) as client:
                resp = client.get(sub["subtitle_url"])
                if resp.status_code != 200:
                    return None
                body = resp.json()
            lines = [l for l in body.get("body", []) if l.get("content")]
            text_full = "\n".join(l["content"] for l in lines)
            if not text_full:
                return None
            segments = [{"start": l.get("from", 0), "end": l.get("to", 0),
                         "text": l.get("content", "")} for l in lines]
            return TranscriptResult(source="platform_subtitle", language=sub.get("lan", "zh"),
                                    text_full=text_full, segments=segments)
        except Exception:
            return None

    def _resolve_cid(self, bvid: str) -> Optional[int]:
        data = self._view_api(bvid)
        v = (data or {}).get("data") or {}
        pages = v.get("pages") or []
        if pages:
            return pages[0].get("cid")
        return None

    def download_media(self, content: ContentInfo, target_dir: Path) -> Path | None:
        try:
            from yt_dlp import YoutubeDL
        except ImportError:
            return None
        out_tmpl = str(target_dir / f"bili_{content.platform_vid}.%(ext)s")
        opts = {
            "outtmpl": out_tmpl,
            "format": "bestaudio/best",
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
        }
        try:
            with YoutubeDL(opts) as ydl:
                info = ydl.extract_info(content.url, download=True)
                path = Path(ydl.prepare_filename(info))
            if path.exists():
                return path
            for p in target_dir.glob(f"bili_{content.platform_vid}.*"):
                return p
        except Exception:
            pass
        return None
