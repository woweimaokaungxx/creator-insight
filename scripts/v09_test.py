"""V0.9 测试：B 站适配器（元数据 / 字幕 / 空间投稿目录 / wbi 签名 / Cookie）。

全部离线（monkeypatch 掉网络调用），覆盖：

1. 工具函数：Cookie 解析 / 时长解析 / wbi mixin_key
2. 输入解析：BV 号 / av 号 / 分享文案 / 无法识别
3. wbi 签名：参数完整、w_rid 按官方算法可复算
4. resolve_creator：主页链接 / 视频链接反查 / 失败路径
5. Cookie 注入：从 platforms.bilibili.cookie 读取
6. 投稿目录：字段映射 / 分页终止 / 风控安全退出 / 非法 mid
"""
from __future__ import annotations

import hashlib
import os
import re
import sys
import tempfile
import urllib.parse

os.environ["PYTHONIOENCODING"] = "utf-8"
os.environ["CI_APP__DATA_DIR"] = tempfile.mkdtemp(prefix="ci_v09_")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import app.adapters.bilibili as bili_mod  # noqa: E402
from app.adapters.bilibili import (  # noqa: E402
    BilibiliAdapter, _get_mixin_key, _length_to_sec, _parse_cookie_string,
)
from app.config import config  # noqa: E402

PASS = 0
FAIL = 0


def check(name: str, cond: bool) -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name}")


# 跳过翻页限速（避免测试变慢）
_orig_sleep = bili_mod.time.sleep
bili_mod.time.sleep = lambda *_a, **_k: None

# 全局隔离网络：匿名指纹默认不主动获取（个别用例用实例级覆盖验证该逻辑）
BilibiliAdapter._buvid_cookies = lambda self: {}

# 测试配置：per_page=3 便于验证分页；带 B 站 Cookie
config._data["monitor"] = {"per_page": 3, "max_pages": 5}
config._data["platforms"] = {
    "bilibili": {
        "enabled": True,
        "cookie": "SESSDATA=abc123; bili_jct=xyz; DedeUserID=42",
        "fetch_video_stats": False,
    }
}

FAKE_IMG = "7wtoamPh0AwKdvnr1Ivl7Dhxrdt5CVUr"
FAKE_SUB = "qOnfjLJPPxJ8FxnFMPZKFbCwUQ2NqLAg"


def make_item(bvid: str, title: str = "", author: str = "测试UP主") -> dict:
    return {
        "bvid": bvid, "title": title or f"投稿 {bvid}", "author": author,
        "created": 1700000000, "length": "09:49", "comment": 12, "play": 340,
    }


# ── 1. 工具函数 ──
print("── 1. 工具函数 ──")
cookies = _parse_cookie_string("SESSDATA=a1; bili_jct=b2;;  =c3; DedeUserID=d4")
check("Cookie 解析键值", cookies.get("SESSDATA") == "a1" and cookies.get("bili_jct") == "b2")
check("Cookie 跳过空片段", len(cookies) == 3)
check("Cookie 空串安全", _parse_cookie_string("") == {})

check("09:49 → 589 秒", _length_to_sec("09:49") == 589)
check("1:02:03 → 3723 秒", _length_to_sec("1:02:03") == 3723)
check("07:00 → 420 秒", _length_to_sec("07:00") == 420)
check("空时长 → None", _length_to_sec("") is None)
check("非法时长 → None", _length_to_sec("abc") is None)

mixin = _get_mixin_key(FAKE_IMG + FAKE_SUB)
check("mixin_key 为 32 位", len(mixin) == 32)
check("mixin_key 字符来自原串", set(mixin) <= set(FAKE_IMG + FAKE_SUB))
check("置换顺序稳定（可复现）", _get_mixin_key(FAKE_IMG + FAKE_SUB) == mixin)
check("短输入不越界", len(_get_mixin_key("abc")) <= 32)

# ── 2. 输入解析 ──
print("── 2. 输入解析 ──")
ad = BilibiliAdapter()
p = ad.parse_input("https://www.bilibili.com/video/BV1xx411c7mD")
check("BV 号解析", p.content_id == "BV1xx411c7mD" and p.platform == "bilibili")
p = ad.parse_input("【测试视频】https://www.bilibili.com/video/av12345 快来看")
check("分享文案中 av 号解析", p.content_id == "av12345")
p = ad.parse_input("BV1xx411c7mD 这段文字里只有 BV 号")
check("裸 BV 号解析", p.content_id == "BV1xx411c7mD")
try:
    ad.parse_input("https://example.com/no-video-id")
    check("无视频 ID 时抛 ValueError", False)
except ValueError:
    check("无视频 ID 时抛 ValueError", True)

# ── 3. wbi 签名 ──
print("── 3. wbi 签名 ──")
ad._wbi_keys = lambda: (FAKE_IMG, FAKE_SUB)      # 跳过 nav 请求
params = {"mid": "12345", "ps": 30, "pn": 1, "order": "pubdate"}
signed = ad._sign_wbi(params)
check("签名保留原参数", signed["mid"] == "12345" and signed["order"] == "pubdate")
check("签名追加 wts", isinstance(signed.get("wts"), int) and signed["wts"] > 0)
check("w_rid 为 32 位 md5", len(signed.get("w_rid") or "") == 32)

items = sorted(
    (k, re.sub(r"[!'()*]", "", str(v)))
    for k, v in {**params, "wts": signed["wts"]}.items()
)
expect = hashlib.md5(
    (urllib.parse.urlencode(items) + _get_mixin_key(FAKE_IMG + FAKE_SUB)).encode()
).hexdigest()
check("w_rid 可按官方算法复算", signed["w_rid"] == expect)
check("特殊字符被过滤后再签名",
      "w_rid" in ad._sign_wbi({"keyword": "a!b'c(d)e*f"}))

# ── 4. resolve_creator ──
print("── 4. resolve_creator ──")
ad = BilibiliAdapter()
ad._fetch_creator_name = lambda mid: "测试UP主"
mid, name, url = ad.resolve_creator("https://space.bilibili.com/12345")
check("主页链接解析 mid", mid == "12345")
check("主页链接取昵称", name == "测试UP主")
check("主页链接生成 URL", url == "https://space.bilibili.com/12345")

ad._view_api = lambda vid: {"data": {"owner": {"mid": 67890, "name": "视频UP"}}}
mid, name, url = ad.resolve_creator("https://www.bilibili.com/video/BV1xx411c7mD")
check("视频链接反查 mid", mid == "67890")
check("视频链接反查昵称", name == "视频UP")
check("视频链接生成主页 URL", url == "https://space.bilibili.com/67890")

ad._view_api = lambda vid: {}
ad._fetch_creator_name = lambda mid: ""
try:
    ad.resolve_creator("这只是一段无意义的文字")
    check("无法解析时抛 ValueError", False)
except ValueError:
    check("无法解析时抛 ValueError", True)

# 主页链接无昵称时回退为 mid
ad2 = BilibiliAdapter()
ad2._fetch_creator_name = lambda mid: ""
mid, name, _ = ad2.resolve_creator("https://space.bilibili.com/999")
check("昵称取不到时回退 mid", mid == "999" and name == "999")

# ── 5. Cookie 注入与匿名指纹 ──
print("── 5. Cookie 注入与匿名指纹 ──")
ad3 = BilibiliAdapter()
check("从 config 读取 Cookie", ad3._cookies().get("SESSDATA") == "abc123")
check("Cookie 多键解析", ad3._cookies().get("DedeUserID") == "42")
check("请求头含 Referer", "bilibili.com" in ad3._headers().get("Referer", ""))
check("请求头含 Origin", ad3._headers().get("Origin") == "https://www.bilibili.com")

config._data["platforms"]["bilibili"]["cookie"] = ""
ad_anon = BilibiliAdapter()
ad_anon._buvid_cookies = lambda: {"buvid3": "B3", "buvid4": "B4"}
check("无 Cookie 时自动补匿名指纹", ad_anon._cookies().get("buvid3") == "B3")
check("匿名指纹含 buvid4", ad_anon._cookies().get("buvid4") == "B4")

config._data["platforms"]["bilibili"]["cookie"] = "SESSDATA=abc123; buvid3=EXIST"
ad_has = BilibiliAdapter()
ad_has._buvid_cookies = lambda: {"buvid3": "SHOULD_NOT_USE"}
check("已有 buvid3 时不覆盖", ad_has._cookies().get("buvid3") == "EXIST")
config._data["platforms"]["bilibili"]["cookie"] = "SESSDATA=abc123; bili_jct=xyz; DedeUserID=42"

# ── 6. 投稿目录 ──
print("── 6. 投稿目录 ──")
ad = BilibiliAdapter()
item = make_item("BV1xx411c7mD", title="深度解析", author="张三")
c = ad._vlist_to_content(item, "12345", "")
check("bvid → platform_vid", c.platform_vid == "BV1xx411c7mD")
check("title 映射", c.title == "深度解析")
check("url 拼接正确", c.url == "https://www.bilibili.com/video/BV1xx411c7mD")
check("作者映射", c.creator_name == "张三")
check("mid 映射", c.creator_platform_id == "12345")
check("发布时间映射", c.published_at == 1700000000)
check("时长解析进字段", c.duration_sec == 589)
check("评论数映射", c.comment_count == 12)
check("列表阶段不填点赞（需 view API）", c.digg_count is None)
check("播放数进 raw_meta", c.raw_meta.get("play") == 340)
try:
    ad._vlist_to_content({"title": "缺 bvid"}, "1", "")
    check("缺 bvid 时抛异常", False)
except ValueError:
    check("缺 bvid 时抛异常", True)

# 分页：第 1 页满页 → 继续；第 2 页不满 → 停止
pages = {
    1: {"code": 0, "data": {"list": {"vlist": [make_item(f"BV{i:010d}") for i in range(3)]}}},
    2: {"code": 0, "data": {"list": {"vlist": [make_item("BV9999999999")]}}},
}
calls: list[int] = []


def fake_api(url, params=None):
    if url == bili_mod.SPACE_VIDEOS_API:
        pn = (params or {}).get("pn")
        calls.append(pn)
        return pages.get(pn, {"code": 0, "data": {"list": {"vlist": []}}})
    return {}


ad._api_get = fake_api
ad._wbi_keys = lambda: (FAKE_IMG, FAKE_SUB)
videos = ad.fetch_creator_videos("12345")
check("分页累计 4 条", len(videos) == 4)
check("翻到第 2 页后因不满页停止", calls == [1, 2])
check("返回顺序保持", videos[0].platform_vid == "BV0000000000")

# max_items 截断
calls.clear()
videos = ad.fetch_creator_videos("12345", max_items=2)
check("max_items 截断生效", len(videos) == 2)
check("截断后不再翻页", calls == [1])

# 风控：接口返回非 0 code → 安全退出
ad._api_get = lambda url, params=None: {"code": -352, "message": "风控校验失败"}
check("风控时返回空列表（不抛异常）", ad.fetch_creator_videos("12345") == [])

# 非法 mid
try:
    ad.fetch_creator_videos("not-a-mid")
    check("非法 mid 抛 ValueError", False)
except ValueError:
    check("非法 mid 抛 ValueError", True)

# 空投稿
ad._api_get = lambda url, params=None: {"code": 0, "data": {"list": {"vlist": []}}}
check("无投稿时返回空列表", ad.fetch_creator_videos("12345") == [])

# fetch_video_stats 开启时补全互动数据
config._data["platforms"]["bilibili"]["fetch_video_stats"] = True
ad._api_get = fake_api
calls.clear()
ad._view_api = lambda vid: {"data": {"stat": {"like": 99, "reply": 8, "share": 5, "favorite": 7}}}
videos = ad.fetch_creator_videos("12345", max_items=1)
check("开启后补全点赞", videos[0].digg_count == 99)
check("开启后补全转发", videos[0].share_count == 5)
check("开启后补全收藏", videos[0].collect_count == 7)
config._data["platforms"]["bilibili"]["fetch_video_stats"] = False

# 恢复 sleep，避免影响其它测试
bili_mod.time.sleep = _orig_sleep

print(f"\n{'=' * 46}")
print(f"V0.9 B 站适配器：{PASS} 通过 / {FAIL} 失败")
print(f"{'=' * 46}")
sys.exit(1 if FAIL else 0)
