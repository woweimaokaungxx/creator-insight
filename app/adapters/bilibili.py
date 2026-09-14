"""B 站 Adapter：公开 API 获取元数据、字幕，以及 UP 主投稿目录。

- 单视频：`x/web-interface/view`（412 风控时 yt-dlp 兜底）
- 字幕：`x/player/v2`（**需登录 Cookie**，否则大概率拿不到 → 外层走 Whisper）
- 博主：`resolve_creator`（主页/视频链接 → mid）+ `fetch_creator_videos`
  （空间投稿接口 `x/space/wbi/arc/search`，**需 wbi 签名**，V0.14 补齐）

Cookie 策略：`config.json` 的 `platforms.bilibili.cookie`（'k1=v1; k2=v2'）。
留空时单视频元数据一般仍可用，但字幕与空间接口容易遇到风控（-352 / 412）。
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import urllib.parse
from pathlib import Path
from typing import Optional

import httpx

from ..config import config
from .base import (
    ContentInfo, CreatorInfo, ParsedURL, PlatformAdapter, TranscriptResult, register,
)

URL_RE = re.compile(r"https?://[^\s，。！？；：、\"''【】<>《》()（）]+")
BV_RE = re.compile(r"(BV[0-9A-Za-z]{10})|(av\d+)")

VIEW_API = "https://api.bilibili.com/x/web-interface/view"
PLAYER_API = "https://api.bilibili.com/x/player/v2"
NAV_API = "https://api.bilibili.com/x/web-interface/nav"
ACC_INFO_API = "https://api.bilibili.com/x/space/wbi/acc/info"
SPACE_VIDEOS_API = "https://api.bilibili.com/x/space/wbi/arc/search"
# 匿名设备指纹（buvid3/buvid4）：不带会被空间接口的 WAF 直接 412
FINGER_API = "https://api.bilibili.com/x/frontend/finger/spi"

# wbi 签名的固定置换表（B 站前端混淆顺序）
MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
    33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40,
    61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11,
    36, 20, 34, 44, 52,
]


def _parse_cookie_string(cookie_str: str) -> dict:
    """把 'k1=v1; k2=v2' 解析为字典"""
    cookies: dict[str, str] = {}
    for part in (cookie_str or "").split(";"):
        part = part.strip()
        if "=" in part:
            k, _, v = part.partition("=")
            if k.strip():
                cookies[k.strip()] = v.strip()
    return cookies


def _get_mixin_key(orig: str) -> str:
    """按固定置换表从 img_key+sub_key 生成 32 位 mixin_key"""
    return "".join(orig[i] for i in MIXIN_KEY_ENC_TAB if i < len(orig))[:32]


def _length_to_sec(length: str) -> int | None:
    """'09:49' / '1:02:03' → 秒；失败返回 None"""
    if not length:
        return None
    try:
        nums = [int(p) for p in str(length).split(":") if p != ""]
    except (TypeError, ValueError):
        return None
    if not nums:
        return None
    sec = 0
    for n in nums:
        sec = sec * 60 + n
    return sec or None


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

    def __init__(self):
        # wbi 密钥缓存：(取回时间戳, img_key, sub_key)，1 小时过期
        self._wbi_cache: tuple[float, str, str] | None = None
        # 匿名指纹缓存：(取回时间戳, {buvid3, buvid4})，1 小时过期
        self._buvid_cache: tuple[float, dict] | None = None

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

    def _platform_config(self) -> dict:
        """platforms.bilibili 配置段"""
        platforms = config.get("platforms", default={}) or {}
        return platforms.get("bilibili") or {}

    def _cookies(self) -> dict:
        """合并 Cookie：用户配置（如有）+ 自动获取的匿名指纹 buvid

        留空 Cookie 时单视频元数据一般可用，但字幕与空间接口易被风控；
        自动补 buvid3/buvid4 可显著降低空间接口的 412 概率。
        """
        cookies = _parse_cookie_string(self._platform_config().get("cookie") or "")
        if not cookies.get("buvid3"):
            cookies.update(self._buvid_cookies())
        return cookies

    def _buvid_cookies(self) -> dict:
        """获取匿名设备指纹 Cookie（buvid3 / buvid4），缓存 1 小时"""
        now = time.time()
        if self._buvid_cache and now - self._buvid_cache[0] < 3600:
            return self._buvid_cache[1]
        cookies: dict[str, str] = {}
        try:
            with httpx.Client(headers=self._headers(), timeout=15,
                              follow_redirects=True) as client:
                resp = client.get(FINGER_API)
                if resp.status_code == 200:
                    data = (resp.json() or {}).get("data") or {}
                    if data.get("b_3"):
                        cookies["buvid3"] = data["b_3"]
                    if data.get("b_4"):
                        cookies["buvid4"] = data["b_4"]
        except Exception as exc:
            print(f"[bilibili] 获取匿名指纹失败: {exc}")
        self._buvid_cache = (now, cookies)
        return cookies

    def _headers(self) -> dict:
        """统一请求头：UA + Referer + Origin（Origin 可降低 WAF 拦截概率）"""
        return {
            "User-Agent": UA,
            "Referer": "https://www.bilibili.com/",
            "Origin": "https://www.bilibili.com",
        }

    def _api_get(self, url: str, params: dict | None = None) -> dict:
        """统一 GET：带 UA / Referer / Cookie，返回 JSON dict（失败返回 {}）"""
        try:
            with httpx.Client(headers=self._headers(), cookies=self._cookies(),
                              timeout=20, follow_redirects=True) as client:
                resp = client.get(url, params=params or {})
                if resp.status_code != 200:
                    print(f"[bilibili] {url} HTTP {resp.status_code}"
                          f"（可能被风控，建议在系统设置里配置 platforms.bilibili.cookie）")
                    return {}
                return resp.json() or {}
        except Exception as exc:
            print(f"[bilibili] {url} 请求异常: {exc}")
            return {}

    def _view_api(self, bvid_or_av: str) -> dict:
        params = {}
        if bvid_or_av.startswith("BV"):
            params["bvid"] = bvid_or_av
        else:
            params["aid"] = bvid_or_av.replace("av", "")
        return self._api_get(VIEW_API, params)

    def _subtitle_api(self, bvid: str, cid: int) -> list[dict]:
        """拉取 CC 字幕列表（需登录 Cookie；未登录通常返回空 → 外层走 Whisper）"""
        data = self._api_get(PLAYER_API, {"bvid": bvid, "cid": cid})
        subs = (data.get("data") or {}).get("subtitle", {}).get("subtitles") or []
        return [s for s in subs if s.get("lan_doc")]

    # ─── wbi 签名（空间接口必需）─────────────────────────────
    def _wbi_keys(self) -> tuple[str, str]:
        """获取并缓存 img_key / sub_key（来自 nav 接口，1 小时过期）"""
        now = time.time()
        if self._wbi_cache and now - self._wbi_cache[0] < 3600:
            return self._wbi_cache[1], self._wbi_cache[2]
        img_key = sub_key = ""
        nav = self._api_get(NAV_API)
        wbi = ((nav.get("data") or {}).get("wbi_img")) or {}
        if wbi.get("img_url"):
            img_key = Path(wbi["img_url"]).stem
        if wbi.get("sub_url"):
            sub_key = Path(wbi["sub_url"]).stem
        if not (img_key and sub_key):
            print("[bilibili] 未取到 wbi 密钥，空间接口可能失败（可尝试配置 Cookie）")
        self._wbi_cache = (now, img_key, sub_key)
        return img_key, sub_key

    def _sign_wbi(self, params: dict) -> dict:
        """给参数追加 wts 与 w_rid（B 站空间接口必需）"""
        img_key, sub_key = self._wbi_keys()
        signed = dict(params)
        signed["wts"] = int(time.time())
        # 签名前过滤 !'()* 并按 key 升序，再拼 mixin_key 取 md5
        items = sorted((k, re.sub(r"[!'()*]", "", str(v))) for k, v in signed.items())
        query = urllib.parse.urlencode(items)
        signed["w_rid"] = hashlib.md5(
            (query + _get_mixin_key(img_key + sub_key)).encode()
        ).hexdigest()
        return signed

    def fetch_creator(self, platform_id: str) -> CreatorInfo:
        return CreatorInfo(
            platform="bilibili", platform_id=platform_id,
            name=self._fetch_creator_name(platform_id) or "B 站用户",
            url=f"https://space.bilibili.com/{platform_id}",
        )

    def _fetch_creator_name(self, mid: str) -> str:
        """用空间账号接口取昵称（需 wbi 签名；失败返回空串，不抛异常）"""
        if not str(mid or "").isdigit():
            return ""
        data = self._api_get(ACC_INFO_API, self._sign_wbi({"mid": mid}))
        return ((data.get("data") or {}).get("name")) or ""

    def resolve_creator(self, raw_input: str) -> tuple[str, str, str]:
        """从主页链接 / 视频链接 / 分享文案解析 UP 主。

        返回 (mid, nickname, 主页URL)。失败抛 ValueError。
        """
        text = (raw_input or "").strip()

        # 1) 主页链接直接提取 mid
        m = re.search(r"space\.bilibili\.com/(\d+)", text)
        if m:
            mid = m.group(1)
            return mid, (self._fetch_creator_name(mid) or mid), f"https://space.bilibili.com/{mid}"

        # 2) 视频链接 / BV 号 / av 号 → view API 反查 owner.mid
        try:
            parsed = self.parse_input(text)
        except ValueError:
            parsed = None
        if parsed:
            v = (self._view_api(parsed.content_id) or {}).get("data") or {}
            owner = v.get("owner") or {}
            mid = str(owner.get("mid") or "")
            if mid:
                return mid, owner.get("name") or mid, f"https://space.bilibili.com/{mid}"

        raise ValueError(f"无法从输入解析 B 站 UP 主（支持主页链接或视频链接）: {text[:60]}")

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
            # 字幕文件本身也要带 Cookie（部分字幕 URL 需鉴权）
            with httpx.Client(headers=self._headers(), cookies=self._cookies(),
                              timeout=20) as client:
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

    # ─── V0.14 UP 主投稿目录抓取（供「博主全部视频」与自动监控）──────
    def _space_videos_api(self, params: dict) -> dict | None:
        """调用空间投稿接口（需 wbi 签名）；失败或被风控返回 None"""
        data = self._api_get(SPACE_VIDEOS_API, params)
        if not data:
            return None
        code = data.get("code")
        if code != 0:
            print(f"[bilibili] 空间投稿接口错误 code={code} msg={data.get('message')}"
                  f"（-352 / -412 通常需要在系统设置里配置 platforms.bilibili.cookie）")
            return None
        return data.get("data") or {}

    def _vlist_to_content(self, item: dict, mid: str, creator_name: str) -> ContentInfo:
        """把空间投稿条目转成 ContentInfo。

        注意：空间接口**只提供评论数**；点赞/转发/收藏需 view API，
        由 `_fill_video_stats` 按需补全（默认关闭，逐条请求较慢）。
        """
        bvid = item.get("bvid") or ""
        if not bvid:
            raise ValueError("投稿条目缺少 bvid")
        return ContentInfo(
            platform="bilibili",
            platform_vid=bvid,
            title=item.get("title") or "",
            url=f"https://www.bilibili.com/video/{bvid}",
            creator_platform_id=str(mid),
            creator_name=item.get("author") or creator_name,
            published_at=_to_int(item.get("created")),
            duration_sec=_length_to_sec(item.get("length")),
            raw_meta={"play": item.get("play"), "source": "space_arc_search"},
            comment_count=_to_int(item.get("comment")),
        )

    def _fill_video_stats(self, content: ContentInfo) -> None:
        """用 view API 补全互动数据（点赞 / 评论 / 转发 / 收藏）"""
        v = (self._view_api(content.platform_vid) or {}).get("data") or {}
        stat = v.get("stat") or {}
        if not stat:
            return
        content.digg_count = _to_int(stat.get("like")) or content.digg_count
        content.comment_count = _to_int(stat.get("reply")) or content.comment_count
        content.share_count = _to_int(stat.get("share"))
        content.collect_count = _to_int(stat.get("favorite"))
        time.sleep(0.3)   # 限速，避免密集请求触发风控

    def fetch_creator_videos(self, creator_id: str, cursor=None,
                             max_items: int = 0) -> list[ContentInfo]:
        """分页抓取 B 站 UP 主投稿目录。

        - `creator_id`：数字 mid（可从主页链接 / 视频链接经 `resolve_creator` 解析）
        - `cursor`：起始页码（1 起）
        - `max_items=0`：抓到「没有更多」或达到 `monitor.max_pages`（默认 20 页）
        - 每页条数取 `monitor.per_page`（B 站上限 50）
        - 互动数据默认只含评论数；设 `platforms.bilibili.fetch_video_stats=true`
          可逐条补全点赞/转发/收藏（较慢，带 0.3s 限速）
        """
        mid = str(creator_id or "").strip()
        if not mid.isdigit():
            raise ValueError(f"B 站博主 ID 应为数字 mid（可从主页链接解析）：{creator_id}")

        monitor = config.get("monitor", default={}) or {}
        per_page = min(max(int(monitor.get("per_page") or 30), 1), 50)
        max_pages = max(int(monitor.get("max_pages") or 20), 1)
        start_page = max(int(cursor or 1), 1)

        videos: list[ContentInfo] = []
        creator_name = ""
        fill_stats = bool(self._platform_config().get("fetch_video_stats", False))

        for page in range(start_page, start_page + max_pages):
            if max_items and len(videos) >= max_items:
                break
            params = self._sign_wbi(
                {"mid": mid, "ps": per_page, "pn": page, "order": "pubdate"})
            data = self._space_videos_api(params)
            if data is None:
                break
            vlist = (data.get("list") or {}).get("vlist") or []
            if not vlist:
                break
            if not creator_name:
                creator_name = vlist[0].get("author") or ""
            for item in vlist:
                try:
                    content = self._vlist_to_content(item, mid, creator_name)
                except Exception:
                    continue
                if fill_stats:
                    try:
                        self._fill_video_stats(content)
                    except Exception:
                        pass
                videos.append(content)
                if max_items and len(videos) >= max_items:
                    break
            if len(vlist) < per_page:
                break          # 已到最后一页
            time.sleep(0.5)    # 翻页限速

        if not videos:
            print(f"[bilibili] UP {mid} 未取到投稿（可能无作品或被风控，建议配置 Cookie）")
        return videos
