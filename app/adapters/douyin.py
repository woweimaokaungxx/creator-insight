"""抖音 Adapter：分享文案/URL 解析、元数据、字幕获取

下载依赖：yt-dlp（必需）+ playwright + 系统 Chrome/Edge（可选，自动生成 cookie 时用）。
抖音对匿名请求风控严格，本 adapter 提供三级 cookie 策略：
  1. config.json 的 monitor.douyin_cookie（手动配置，优先级最高）
  2. data/cookies/douyin_{vid}.txt（上次自动生成的缓存）
  3. playwright 驱动系统 Chrome/Edge 现场生成（自动兜底）
"""
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
from ..config import config

URL_RE = re.compile(r"https?://[^\s，。！？；：、\"''【】<>《》()（）]+")

# V0.4 关注监控：主页作品目录 API（参考 douyin-creator-distill 的 api_supplement）
PROFILE_API = "https://www.douyin.com/aweme/v1/web/aweme/post/"
USER_PROFILE_API = "https://www.douyin.com/aweme/v1/web/user/profile/other/"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

# 每次自动生成 cookie 的最小间隔（秒），避免频繁驱动浏览器
_AUTO_COOKIE_MIN_INTERVAL = 600


def _to_int(v) -> int | None:
    """把点赞/评论等数字安全转 int，失败返回 None"""
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _parse_cookie_string(cookie_str: str) -> dict:
    """把 'k1=v1; k2=v2' 解析为字典"""
    cookies: dict[str, str] = {}
    for part in cookie_str.split(";"):
        part = part.strip()
        if "=" in part:
            k, _, v = part.partition("=")
            if k.strip():
                cookies[k.strip()] = v.strip()
    return cookies


@register
class DouyinAdapter(PlatformAdapter):
    platform = "douyin"

    def parse_input(self, raw_input: str) -> ParsedURL:
        """支持：长链 /video/{id}、/share/video/{id}、v.douyin.com 短链、整段分享文案"""
        raw = raw_input.strip()
        urls = URL_RE.findall(raw)
        if not urls:
            raise ValueError("未找到抖音链接")
        url = urls[0]

        # 从分享文案中尝试提取博主名与标题（短链/长链共用）
        creator_hint = None
        title_hint = None
        m_creator = re.search(r"【([^】]+)的作品", raw) or re.search(r"看看([^的]+)的作品", raw)
        if m_creator:
            creator_hint = m_creator.group(1).strip()
        m_title = re.search(r"作品《(.+?)》", raw)
        if m_title:
            title_hint = m_title.group(1).strip()

        # 短链 → 跟随重定向拿真实地址
        if "v.douyin.com" in url:
            short_token = None
            m_short = re.search(r"v\.douyin\.com/([^/?#]+)", url)
            if m_short:
                short_token = m_short.group(1)
            try:
                with httpx.Client(follow_redirects=True, timeout=15) as client:
                    resp = client.get(url)
                    final_url = str(resp.url)
            except Exception:
                raise ValueError("短链重定向失败（网络异常）")
            # 重定向到 video/note 页 → 提取 ID
            m = re.search(r"/(?:video|note|share)/?(\d+)", final_url)
            if m:
                url = final_url
                vid = m.group(1)
                return ParsedURL(
                    platform="douyin", content_id=vid,
                    url=url, creator_hint=creator_hint, title_hint=title_hint,
                    raw_input=raw,
                )
            # 重定向后无 ID → 链接失效或跳首页
            raise ValueError(f"抖音短链解析失败（可能已失效）: {url}")

        # /video/{id} 或 /share/video/{id}
        m = re.search(r"/(?:video|note|share)/(\d+)", url)
        if not m:
            raise ValueError(f"无法解析抖音视频 ID: {url}")
        vid = m.group(1)

        return ParsedURL(
            platform="douyin",
            content_id=vid,
            url=url,
            creator_hint=creator_hint,
            title_hint=title_hint,
            raw_input=raw,
        )

    # ─── 抖音 cookie 策略（手动配置 → 本地缓存 → playwright 自动生成）──
    def _cookie_file(self, vid: str) -> Path:
        """自动生成 cookie 的本地缓存路径（Netscape 格式，供 yt-dlp 用）"""
        return (config.data_dir / "cookies" / f"douyin_{vid}.txt")

    def _config_cookies(self) -> dict:
        """从 config.json monitor.douyin_cookie 解析出 cookie 字典"""
        return _parse_cookie_string(self._monitor_config().get("douyin_cookie") or "")

    def _auto_cookie(self, vid: str) -> Path | None:
        """用 playwright 驱动系统 Chrome/Edge 现场生成 cookie 并缓存。

        核心思路参考 video-transcribe 技能包：访问抖音页面触发 JS SDK
        生成 ttwid 等登录态 cookie，导出为 Netscape 格式文件交给 yt-dlp。
        """
        if not self._playwright_available():
            return None
        cookie_file = self._cookie_file(vid)
        # 命中缓存且未过期太久则直接复用，避免频繁驱动浏览器
        if cookie_file.exists():
            age = time.time() - cookie_file.stat().st_mtime
            if age < _AUTO_COOKIE_MIN_INTERVAL:
                return cookie_file
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return None
        try:
            with sync_playwright() as p:
                browser = None
                for channel in ("chrome", "msedge"):
                    try:
                        browser = p.chromium.launch(
                            channel=channel, headless=True,
                            args=["--disable-blink-features=AutomationControlled"])
                        break
                    except Exception:
                        continue
                if browser is None:
                    print("[douyin] 未找到系统 Chrome/Edge，无法自动生成 cookie")
                    return None
                try:
                    ctx = browser.new_context(user_agent=UA, locale="zh-CN",
                                              viewport={"width": 1280, "height": 900})
                    page = ctx.new_page()
                    page_title = ""
                    og_title = ""
                    og_desc = ""
                    author = ""
                    publish_text = ""
                    try:
                        page.goto("https://www.douyin.com/", timeout=45000,
                                  wait_until="domcontentloaded")
                        page.wait_for_timeout(6000)  # 等 JS SDK 生成 ttwid
                        page.goto(f"https://www.douyin.com/video/{vid}",
                                  timeout=45000, wait_until="domcontentloaded")
                        page.wait_for_timeout(12000)  # 等视频数据加载
                        try:
                            page_title = page.title() or ""
                            og_title = page.evaluate(
                                "() => document.querySelector('meta[property=\"og:title\"]')?.content || ''")
                            og_desc = page.evaluate(
                                "() => document.querySelector('meta[property=\"og:description\"]')?.content || ''")
                            # 作者名：优先取昵称容器 .m53pwJvW（纯昵称，不含认证徽章）；
                            # 兜底：去掉"认证徽章"字样后按"粉丝/获赞"切分
                            author = page.evaluate(
                                "() => {"
                                "  const el = document.querySelector('[data-e2e=\"user-info\"]');"
                                "  if (!el) return '';"
                                "  const nick = el.querySelector('.m53pwJvW');"
                                "  if (nick && nick.textContent.trim()) return nick.textContent.trim();"
                                "  const t = el.textContent.replace(/认证徽章/g, '').trim();"
                                "  return t.split(/[粉丝]|获赞/)[0].trim();"
                                "}")
                            # 发布时间：data-e2e="detail-video-publish-time"
                            publish_text = page.evaluate(
                                "() => document.querySelector('[data-e2e=\"detail-video-publish-time\"]')?.textContent?.trim() || ''")
                        except Exception:
                            pass
                    except Exception as exc:
                        print(f"[douyin] 浏览器访问抖音异常: {str(exc)[:120]}")
                    cookies = ctx.cookies()
                    browser.close()
                finally:
                    if browser is not None and not browser.is_connected():
                        try:
                            browser.close()
                        except Exception:
                            pass
            if not cookies:
                print("[douyin] 未获取到任何 cookie")
                return None
            cookie_file.parent.mkdir(parents=True, exist_ok=True)
            lines = ["# Netscape HTTP Cookie File"]
            seen = set()
            for c in cookies:
                key = (c.get("name"), c.get("domain"))
                if key in seen:
                    continue
                seen.add(key)
                dom = c.get("domain", "")
                flag = "TRUE" if dom.startswith(".") else "FALSE"
                path = c.get("path", "/")
                secure = "TRUE" if c.get("secure") else "FALSE"
                exp = int(c.get("expires", 0) or 0)
                if exp < 0:
                    exp = 0
                lines.append(f"{dom}\t{flag}\t{path}\t{secure}\t{exp}\t"
                             f"{c.get('name', '')}\t{c.get('value', '')}")
            cookie_file.write_text("\n".join(lines), encoding="utf-8")
            print(f"[douyin] 自动生成 cookie -> {cookie_file.name} ({len(seen)} 个)")
            # 缓存页面元数据（详情 API 被风控时的 title/creator 兜底来源）
            meta = {}
            if og_title:
                meta["title"] = og_title
            elif page_title and page_title != "抖音":
                meta["title"] = page_title
            if author:
                meta["author"] = author
            if publish_text:
                meta["publish_text"] = publish_text
            if og_desc:
                meta["og_description"] = og_desc
            if meta:
                try:
                    (cookie_file.with_suffix(".meta.json")).write_text(
                        json.dumps(meta, ensure_ascii=False), encoding="utf-8")
                except Exception:
                    pass
            return cookie_file
        except Exception as exc:
            print(f"[douyin] 自动生成 cookie 失败: {str(exc)[:160]}")
            return None

    @staticmethod
    def _playwright_available() -> bool:
        """检查 playwright 是否可用（避免每次失败都重试安装提示）"""
        try:
            import importlib.util
            return importlib.util.find_spec("playwright") is not None
        except Exception:
            return False

    @staticmethod
    def _cookie_file_to_dict(cookie_file: Path) -> dict:
        """把 Netscape 格式 cookie 文件解析为 {name: value} dict（供 httpx 用）"""
        import http.cookiejar
        if not cookie_file or not cookie_file.exists():
            return {}
        try:
            cj = http.cookiejar.MozillaCookieJar(str(cookie_file))
            cj.load()
            return {c.name: c.value for c in cj}
        except Exception:
            return {}

    def _any_cached_cookie(self) -> dict:
        """遍历 cookies 目录，取一个内容最全的缓存 cookie（复用视频 cookie 抓主页）"""
        try:
            cookie_dir = config.data_dir / "cookies"
            if not cookie_dir.exists():
                return {}
            best: dict = {}
            for f in cookie_dir.glob("douyin_*.txt"):
                d = self._cookie_file_to_dict(f)
                if len(d) > len(best):
                    best = d
            return best
        except Exception:
            return {}

    def _ensure_cookies(self, vid: str) -> tuple[dict, Path | None]:
        """返回 (cookie 字典, netscape cookie 文件路径)。

        优先级：手动配置 → 本地缓存 → playwright 自动生成。
        返回的 cookie 文件路径供 yt-dlp 使用；字典供 httpx 详情 API 使用。
        """
        cfg = self._config_cookies()
        if cfg:
            return cfg, None
        cookie_file = self._cookie_file(vid)
        if cookie_file.exists():
            return self._cookie_file_to_dict(cookie_file), cookie_file
        auto_file = self._auto_cookie(vid)
        if auto_file and auto_file.exists():
            return self._cookie_file_to_dict(auto_file), auto_file
        return {}, None

    def _detail_api(self, vid: str, cookies: dict | None = None,
                    retry_auto: bool = True) -> dict:
        """调用抖音详情 API 获取元数据。

        匿名失败（风控）时若 retry_auto 且未用 cookie，则尝试自动生成 cookie 重试。
        """
        url = f"https://www.douyin.com/aweme/v1/web/aweme/detail/?aweme_id={vid}&aid=1128"
        headers = {"User-Agent": UA, "Referer": "https://www.douyin.com/"}
        used_cookies = cookies
        if used_cookies is None:
            used_cookies, _ = self._ensure_cookies(vid)
        try:
            with httpx.Client(headers=headers, cookies=used_cookies or {},
                              timeout=20, follow_redirects=True) as client:
                resp = client.get(url)
                if resp.status_code != 200:
                    return {}
                data = resp.json()
        except Exception:
            return {}
        # 匿名被风控返回空或 code!=0，且允许自动兜底时重试
        if retry_auto and not used_cookies and not data.get("aweme_detail"):
            auto_cookies, _ = self._ensure_cookies(vid)
            if auto_cookies:
                try:
                    with httpx.Client(headers=headers, cookies=auto_cookies,
                                      timeout=20, follow_redirects=True) as client:
                        resp = client.get(url)
                        if resp.status_code == 200:
                            data = resp.json()
                except Exception:
                    pass
        return data

    def fetch_creator(self, platform_id: str) -> CreatorInfo:
        # 平台 ID 即 sec_user_id；尝试通过主页 API 反查昵称（失败时退回占位）
        try:
            data = self._user_profile_api(platform_id)
            user = (data or {}).get("user") or {}
            name = user.get("nickname") or platform_id or "未知博主"
        except Exception:
            name = platform_id or "未知博主"
        return CreatorInfo(
            platform="douyin", platform_id=platform_id,
            name=name,
            url=f"https://www.douyin.com/user/{platform_id}" if platform_id else "",
        )

    @staticmethod
    def _extract_avatar(author: dict) -> str:
        """从抖音作者对象提取头像 URL。

        匿名详情 API 通常只返回 avatar_thumb=100x100，故取到后统一调用
        _upgrade_avatar_url 升格为大图档位（实测 720x720/1080x1080 有效）。
        """
        for key in ("avatar_larger", "avatar_medium", "avatar_thumb"):
            node = author.get(key) or {}
            if isinstance(node, dict):
                urls = node.get("url_list") or []
                if urls and isinstance(urls[0], str) and urls[0].startswith("http"):
                    return urls[0]
            elif isinstance(node, str) and node.startswith("http"):
                return node
        return ""

    @staticmethod
    def _upgrade_avatar_url(url: str) -> str:
        """把抖音头像 URL 的尺寸段升格为高清档位。

        实测 CDN 有效档位：100x100 / 200x200 / 720x720 / 1080x1080（中间值如
        300/540/640/1440 会 400）。目标用 1080x1080；非抖音域/无尺寸段原样返回。
        """
        if not url or not re.search(r"(douyinpic\.com|byteimg\.com)", url):
            return url
        if not re.search(r"/(aweme/)?\d{2,4}x\d{2,4}/", url):
            return url
        return re.sub(r"/(aweme/)?\d{2,4}x\d{2,4}/", r"/\g<1>1080x1080/", url, count=1)

    def fetch_content_meta(self, parsed: ParsedURL) -> ContentInfo:
        data = self._detail_api(parsed.content_id)
        aweme = (data or {}).get("aweme_detail") or {}
        author = aweme.get("author") or {}
        video = aweme.get("video") or {}
        stats = aweme.get("statistics") or {}
        published = aweme.get("create_time")  # Unix 秒
        duration = None
        if video.get("duration"):
            try:
                duration = int(video["duration"]) // 1000
            except Exception:
                duration = None

        title = aweme.get("desc") or parsed.title_hint or ""
        creator_name = author.get("nickname") or parsed.creator_hint or ""
        creator_uid = str(author.get("uid") or parsed.creator_hint or "")
        # 抖音头像：详情 API 常只给 avatar_thumb=100x100，升格为 1080x1080 高清档
        avatar = self._upgrade_avatar_url(self._extract_avatar(author))
        # 详情 API 被风控返回空时，用 playwright 缓存的页面元数据兜底
        if not title or not creator_name or not published:
            meta = self._meta_cache(parsed.content_id)
            if not title and meta.get("title"):
                title = meta["title"]
                # og:title 形如「标题 - 抖音」，去掉平台后缀
                title = re.sub(r"\s*-\s*抖音\s*$", "", title)
            if not creator_name and meta.get("author"):
                creator_name = meta["author"]
            if not published and meta.get("publish_text"):
                published = self._parse_publish_time(meta["publish_text"])

        return ContentInfo(
            platform="douyin",
            platform_vid=parsed.content_id,
            title=title,
            url=parsed.url,
            creator_platform_id=creator_uid,
            creator_name=creator_name,
            creator_avatar_url=avatar,
            published_at=int(published) if published else None,
            duration_sec=duration,
            raw_meta={"stats": stats, "author": author.get("nickname") or creator_name},
            digg_count=_to_int(stats.get("digg_count")),
            comment_count=_to_int(stats.get("comment_count")),
            share_count=_to_int(stats.get("share_count")),
            collect_count=_to_int(stats.get("collect_count")),
        )

    @staticmethod
    def _parse_publish_time(text: str) -> int | None:
        """把「发布时间：2026-08-24 01:38」解析为 Unix 秒"""
        try:
            m = re.search(r"(\d{4}-\d{2}-\d{2}\s+\d{1,2}:\d{2})", text)
            if not m:
                return None
            from datetime import datetime
            dt = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M")
            return int(dt.timestamp())
        except Exception:
            return None

    def _meta_cache(self, vid: str) -> dict:
        """读取 playwright 生成的页面元数据缓存（详情 API 兜底用）"""
        try:
            f = self._cookie_file(vid).with_suffix(".meta.json")
            if f.exists():
                return json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            pass
        return {}

    def fetch_transcript(self, content: ContentInfo) -> TranscriptResult | None:
        """抖音无公开字幕接口，返回 None 交由 Whisper 兜底"""
        return None

    def download_media(self, content: ContentInfo, target_dir: Path) -> Path | None:
        """用 yt-dlp 下载音频/视频（供 Whisper 使用）。

        若 yt-dlp 报 "Fresh cookies needed"（匿名被风控），自动用
        playwright 生成 cookie 文件后重试一次。
        """
        try:
            from yt_dlp import YoutubeDL
        except ImportError:
            return None
        out_tmpl = str(target_dir / f"douyin_{content.platform_vid}.%(ext)s")
        opts = {
            "outtmpl": out_tmpl,
            "format": "bestaudio/best",
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
        }
        vid = content.platform_vid

        def _try_download(cookiefile: str | None) -> Path | None:
            o = dict(opts)
            if cookiefile:
                o["cookiefile"] = cookiefile
                o["user_agent"] = UA
                o["http_headers"] = {"Referer": "https://www.douyin.com/"}
            try:
                with YoutubeDL(o) as ydl:
                    info = ydl.extract_info(content.url, download=True)
                    path = Path(ydl.prepare_filename(info))
                if path.exists():
                    return path
                for p in target_dir.glob(f"douyin_{vid}.*"):
                    return p
                return None
            except Exception:
                return None

        # 第一次：匿名尝试
        path = _try_download(None)
        if path:
            return path
        # 第二次：用自动生成的 cookie 重试（解决 "Fresh cookies needed"）
        _, cookie_file = self._ensure_cookies(vid)
        if cookie_file and cookie_file.exists():
            path = _try_download(str(cookie_file))
            if path:
                return path
        return None

    # ─── V0.4 关注监控：博主主页目录抓取 ────────────────────────

    def _ua(self) -> str:
        return ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

    def _monitor_config(self) -> dict:
        return config.get("monitor", default={}) or {}

    def _cookies(self) -> dict:
        """从配置读取 douyin_cookie（'k1=v1; k2=v2'），供目录/主页 API 使用"""
        return _parse_cookie_string(self._monitor_config().get("douyin_cookie") or "")

    def _user_profile_api(self, sec_user_id: str) -> dict:
        url = f"{USER_PROFILE_API}?sec_user_id={sec_user_id}&aid=6383&device_platform=webapp"
        headers = {"User-Agent": self._ua(), "Referer": "https://www.douyin.com/"}
        try:
            with httpx.Client(headers=headers, cookies=self._cookies(),
                              timeout=20, follow_redirects=True) as client:
                resp = client.get(url)
                if resp.status_code != 200:
                    return {}
                return resp.json()
        except Exception:
            return {}

    def _profile_api(self, sec_user_id: str, max_cursor: int = 0, count: int = 18) -> dict:
        """抓取一页主页作品目录；max_cursor=0 表示第一页"""
        params = {
            "device_platform": "webapp",
            "aid": 6383,
            "channel": "channel_pc_web",
            "sec_user_id": sec_user_id,
            "max_cursor": max_cursor,
            "locate_query": "false",
            "show_live_replay_strategy": 1,
            "need_time_list": 1,
            "time_list_query": 0,
            "publish_video_strategy_type": 2,
            "count": count,
        }
        headers = {
            "User-Agent": self._ua(),
            "Referer": f"https://www.douyin.com/user/{sec_user_id}",
            "Accept": "application/json, text/plain, */*",
        }

        def _call(cookies: dict) -> dict:
            try:
                with httpx.Client(headers=headers, cookies=cookies or {},
                                  timeout=20, follow_redirects=True) as client:
                    resp = client.get(PROFILE_API, params=params)
                    if resp.status_code != 200:
                        return {"status_code": resp.status_code, "error": f"HTTP {resp.status_code}"}
                    return resp.json()
            except Exception as exc:
                return {"status_code": -1, "error": str(exc)}

        # 先手动配置 cookie，无则用 Playwright 自动生成（目录 API 匿名必被风控）
        cookies, _ = self._ensure_cookies(sec_user_id)
        data = _call(cookies)
        # 仍失败：复用已有视频 cookie（更完整）重试
        if not data.get("aweme_list"):
            cached = self._any_cached_cookie()
            if cached:
                data = _call(cached)
        # 仍未拿到：尝试重新自动生成一次
        if not data.get("aweme_list") and not cookies:
            cookies2, _ = self._ensure_cookies(sec_user_id)
            if cookies2:
                data = _call(cookies2)
        return data

    def _aweme_to_content(self, aweme: dict, sec_user_id: str) -> ContentInfo:
        author = aweme.get("author") or {}
        video = aweme.get("video") or {}
        stats = aweme.get("statistics") or {}
        aweme_id = str(aweme.get("aweme_id") or aweme.get("id") or "")
        create_time = aweme.get("create_time")
        duration = None
        if video.get("duration"):
            try:
                duration = int(video["duration"]) // 1000
            except Exception:
                duration = None
        return ContentInfo(
            platform="douyin",
            platform_vid=aweme_id,
            title=aweme.get("desc") or "",
            url=f"https://www.douyin.com/video/{aweme_id}" if aweme_id else "",
            creator_platform_id=str(author.get("uid") or sec_user_id or ""),
            creator_name=author.get("nickname") or "",
            creator_avatar_url=self._upgrade_avatar_url(self._extract_avatar(author)),
            published_at=int(create_time) if create_time else None,
            duration_sec=duration,
            raw_meta={
                "stats": stats,
                "nickname": author.get("nickname"),
                "sec_uid": author.get("sec_uid") or sec_user_id,
                "is_top": bool(aweme.get("is_top")),
            },
            digg_count=_to_int(stats.get("digg_count")),
            comment_count=_to_int(stats.get("comment_count")),
            share_count=_to_int(stats.get("share_count")),
            collect_count=_to_int(stats.get("collect_count")),
        )

    def _fetch_videos_via_browser(self, sec_user_id: str, max_items: int = 0) -> list[ContentInfo]:
        """用 Playwright 登录态 Chrome profile 抓取博主主页作品目录。

        抖音主页目录 API 需要 a_bogus 签名 + 登录态，纯 httpx 必被风控。
        参考 douyin-creator-distill：launch_persistent_context 打开登录态
        Chrome profile，拦截目录 API（aweme/post）响应获取完整数据。

        需在 config monitor.profile_path 配置已登录抖音的 Chrome user-data-dir。
        """
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return []
        cfg = self._monitor_config()
        profile_path = cfg.get("profile_path") or ""
        if not profile_path:
            print("[douyin] 未配置 monitor.profile_path（登录态 Chrome profile），无法浏览器抓取")
            return []
        collected: dict[str, dict] = {}
        try:
            with sync_playwright() as p:
                # 用系统 Chrome/Edge（Playwright 自带 chromium 可能未下载）
                ctx = None
                last_err = None
                for ch in ("chrome", "msedge"):
                    try:
                        ctx = p.chromium.launch_persistent_context(
                            user_data_dir=profile_path, channel=ch, headless=True,
                            args=["--disable-blink-features=AutomationControlled",
                                  "--no-first-run", "--disable-default-apps"],
                        )
                        break
                    except Exception as exc:
                        last_err = exc
                        continue
                if ctx is None:
                    print(f"[douyin] 无法用系统浏览器打开 profile: {last_err}")
                    return []
                page = ctx.pages[0] if ctx.pages else ctx.new_page()

                def on_response(response):
                    if "aweme/post" not in response.url:
                        return
                    try:
                        payload = response.json()
                    except Exception:
                        return
                    for a in payload.get("aweme_list") or []:
                        aid = str(a.get("aweme_id") or "")
                        if aid:
                            collected[aid] = a
                page.on("response", on_response)
                # 访问博主主页，滚动加载触发目录 API
                page.goto(f"https://www.douyin.com/user/{sec_user_id}",
                          timeout=45000, wait_until="commit")
                page.wait_for_timeout(8000)
                for _ in range(12):  # 滚动加载多页
                    try:
                        page.mouse.wheel(0, 4000)
                        page.wait_for_timeout(2000)
                    except Exception:
                        break
                    if max_items and len(collected) >= max_items:
                        break
                ctx.close()
        except Exception as exc:
            print(f"[douyin] 浏览器抓取失败: {str(exc)[:160]}")
            return []
        if not collected:
            print("[douyin] 浏览器抓取未获取到作品（profile 可能未登录或被风控）")
            return []
        videos = []
        for aid, aweme in collected.items():
            try:
                videos.append(self._aweme_to_content(aweme, sec_user_id))
            except Exception:
                continue
        return videos

    def fetch_creator_videos(self, creator_id: str, cursor=None, max_items: int = 0) -> list[ContentInfo]:
        """分页抓取博主主页作品目录，返回 ContentInfo 列表。

        creator_id 为抖音 sec_user_id；cursor 可选（0 起）。max_items=0 表示
        抓取到 has_more=False 或达到 max_pages 上限（默认 20 页）。
        优先 httpx 目录 API；若被 a_bogus 风控返回空，回退 Playwright 登录态
        profile 浏览器抓取（需配置 monitor.profile_path）。
        """
        cfg = self._monitor_config()
        max_pages = int(cfg.get("max_pages", 20))
        per_page = int(cfg.get("per_page", 18))
        max_cursor = int(cursor or 0)
        videos: list[ContentInfo] = []
        seen: set[str] = set()
        for _ in range(max_pages):
            data = self._profile_api(creator_id, max_cursor=max_cursor, count=per_page)
            aweme_list = data.get("aweme_list") or []
            for aweme in aweme_list:
                if not aweme:
                    continue
                info = self._aweme_to_content(aweme, creator_id)
                if not info.platform_vid or info.platform_vid in seen:
                    continue
                seen.add(info.platform_vid)
                videos.append(info)
                if max_items and len(videos) >= max_items:
                    return videos
            if not data.get("has_more"):
                break
            max_cursor = int(data.get("max_cursor") or 0)
            if max_cursor <= 0:
                break
            time.sleep(0.5)  # 温和限速，避免风控
        # httpx 目录 API 被 a_bogus 风控返回空 → 回退登录态浏览器抓取
        if not videos:
            print("[douyin] 目录 API 返回空，回退 Playwright 登录态 profile 抓取")
            videos = self._fetch_videos_via_browser(creator_id, max_items=max_items)
        return videos

    def resolve_creator(self, raw_input: str) -> tuple[str, str, str]:
        """从主页链接 / 分享文案 / 任意作品链接解析博主。

        返回 (sec_user_id, nickname, 主页URL)。失败抛 ValueError。
        """
        text = raw_input.strip()

        # 1) 主页链接直接提取 sec_user_id
        m_user = re.search(r"douyin\.com/user/([A-Za-z0-9_\-]+)", text)
        if m_user:
            sec = m_user.group(1)
            hint = re.search(r"【([^】]+)的作品", text)
            return sec, (hint.group(1).strip() if hint else sec), f"https://www.douyin.com/user/{sec}"

        # 2) v.douyin.com 短链 → 跟随重定向，可能是主页或单条视频
        m_short = re.search(r"v\.douyin\.com/([^/?#\s]+)", text)
        if m_short:
            try:
                with httpx.Client(follow_redirects=True, timeout=15) as client:
                    resp = client.get(f"https://v.douyin.com/{m_short.group(1)}/")
                    final_url = str(resp.url)
            except Exception:
                final_url = ""
            if "/user/" in final_url:
                m_user = re.search(r"douyin\.com/user/([A-Za-z0-9_\-]+)", final_url)
                if m_user:
                    return m_user.group(1), "", f"https://www.douyin.com/user/{m_user.group(1)}"
            if final_url:
                text = final_url

        # 3) 单条作品链接/分享文案 → 详情 API 反查 author.sec_uid
        try:
            parsed = self.parse_input(text)
        except ValueError:
            parsed = None
        if parsed:
            # 传 None → _detail_api 内部自动走 cookie 策略（含 playwright 兜底）
            data = self._detail_api(parsed.content_id, None)
            aweme = (data or {}).get("aweme_detail") or {}
            author = aweme.get("author") or {}
            sec = author.get("sec_uid")
            if sec:
                nick = author.get("nickname") or parsed.creator_hint or sec
                return str(sec), nick, f"https://www.douyin.com/user/{sec}"

        raise ValueError(
            "无法识别抖音博主主页：请粘贴主页链接（douyin.com/user/...）、"
            "该博主的任意作品链接，或包含作品链接的分享文案"
        )
