"""抖音账号与登录态管理服务。

与 ``scripts/douyin_login.py`` 配合，提供：

- **可见浏览器首次登录**：登录态持久化到专用 Chrome 目录（后续抓取直接复用），
  同时导出 Netscape Cookie 文件供 httpx / yt-dlp 使用。
- **手动写入**：粘贴 Cookie 或指定浏览器目录即可保存，替代手工编辑 config.json。
- **账号总览（脱敏）**：只返回状态、Cookie 来源与数量，**绝不返回 Cookie 明文与目录绝对路径**。

落点：

    config/config.json             monitor.douyin_cookie（手动 Cookie）
                                   monitor.profile_path（登录态浏览器目录）
    data/cookies/douyin_login.txt  Netscape Cookie（程序复用）
    data/account_status.json       登录阶段状态（前端轮询）
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from ..config import ROOT, config

LOGIN_COOKIE_NAME = "douyin_login.txt"
STATUS_FILENAME = "account_status.json"
LOGIN_SCRIPT = ROOT / "scripts" / "douyin_login.py"
DEFAULT_PROFILE_DIRNAME = "douyin"
MAX_COOKIE_CHARS = 20000

# 登录「进行中」阶段：进程存活判定只对这些阶段生效
_RUNNING_PHASES = {"launching_login", "waiting_for_login"}


# ─── 路径 ───────────────────────────────────────────────────
def cookie_dir() -> Path:
    d = config.data_dir / "cookies"
    d.mkdir(parents=True, exist_ok=True)
    return d


def login_cookie_file() -> Path:
    """登录态 / 手动写入后的 Netscape Cookie 文件（四级策略第 2 级）"""
    return cookie_dir() / LOGIN_COOKIE_NAME


def status_file() -> Path:
    return config.data_dir / STATUS_FILENAME


def default_profile_dir() -> Path:
    """默认登录态目录：config.account.default_profile_dir > data/browser_profile/douyin"""
    conf = str(config.get("account", "default_profile_dir", default="") or "").strip()
    if conf:
        return Path(conf)
    return config.data_dir / "browser_profile" / DEFAULT_PROFILE_DIRNAME


def resolve_profile_path(explicit: str = "") -> str:
    """浏览器目录解析：显式参数 > 配置 > 项目默认目录"""
    given = (explicit or "").strip()
    if given:
        return given
    conf = str(config.get("monitor", "profile_path", default="") or "").strip()
    return conf or str(default_profile_dir())


# ─── 工具 ───────────────────────────────────────────────────
def _read_json(path: Path) -> dict:
    try:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception as exc:
        print(f"[account] 读取 {path.name} 失败: {exc}")
    return {}


def _atomic_write(path: Path, text: str) -> None:
    """先写临时文件再替换，避免半截文件被抓取链路读到（固定 \\n，防 Windows 转换污染值）"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def _pid_alive(pid) -> bool:
    """跨平台进程存活判定（Windows 用 OpenProcess，避免误杀进程）"""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
            if handle:
                kernel32.CloseHandle(handle)
                return True
            return False
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _cookies_to_netscape(cookies: list[dict]) -> str:
    """Cookie dict 列表 → Netscape 文本（yt-dlp / http.cookiejar 通用格式）"""
    lines = ["# Netscape HTTP Cookie File"]
    seen: set[tuple] = set()
    for c in cookies:
        key = (c.get("name"), c.get("domain"))
        if not c.get("name") or key in seen:
            continue
        seen.add(key)
        dom = str(c.get("domain") or ".douyin.com")
        flag = "TRUE" if dom.startswith(".") else "FALSE"
        secure = "TRUE" if c.get("secure") else "FALSE"
        exp = int(c.get("expires", 0) or 0)
        if exp < 0:
            exp = 0
        lines.append(
            f"{dom}\t{flag}\t{c.get('path') or '/'}\t{secure}\t{exp}\t"
            f"{c.get('name')}\t{c.get('value', '')}"
        )
    return "\n".join(lines) + "\n"


def _cookie_dict_to_list(cookies: dict) -> list[dict]:
    """{'k': 'v'} → Cookie 列表（补默认域/路径/有效期）"""
    exp = int(time.time()) + 30 * 86400
    return [
        {"name": k, "value": v, "domain": ".douyin.com", "path": "/",
         "secure": True, "expires": exp}
        for k, v in cookies.items()
    ]


def _count_netscape(path: Path) -> int:
    try:
        return sum(
            1 for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#") and "\t" in line
        )
    except Exception:
        return 0


# ─── 状态文件 ───────────────────────────────────────────────
def read_login_status() -> dict:
    """登录阶段；进行中但子进程已退出 → 判定为窗口已关闭"""
    raw = _read_json(status_file())
    phase = str(raw.get("phase") or "idle")
    if phase in _RUNNING_PHASES and raw.get("helper_pid") and not _pid_alive(raw.get("helper_pid")):
        phase = "browser_closed"
    return {
        "phase": phase,
        "ready": bool(raw.get("ready")) and phase == "login_ready",
        "verified_at": raw.get("verified_at"),
        "status_code": raw.get("status_code"),
        "error": raw.get("error"),
        "updated_at": raw.get("updated_at"),
    }


def write_login_status(**fields) -> dict:
    data = _read_json(status_file())
    data.update(fields)
    data["updated_at"] = int(time.time())
    _atomic_write(status_file(), json.dumps(data, ensure_ascii=False, indent=2))
    return data


# ─── 账号总览（脱敏）────────────────────────────────────────
def _detect_cookie_source() -> tuple[str, int]:
    """当前生效 Cookie 的来源与数量：manual > login > auto > none"""
    from ..adapters.douyin import _parse_cookie_string

    manual = _parse_cookie_string(str(config.get("monitor", "douyin_cookie", default="") or ""))
    if manual:
        return "manual", len(manual)
    login_file = login_cookie_file()
    if login_file.exists():
        n = _count_netscape(login_file)
        if n:
            return "login", n
    best = 0
    for f in cookie_dir().glob("douyin_*.txt"):
        if f.name != LOGIN_COOKIE_NAME:
            best = max(best, _count_netscape(f))
    if best:
        return "auto", best
    return "none", 0


def overview() -> dict:
    """账号总览（脱敏：无 Cookie 明文、无目录绝对路径）"""
    source, count = _detect_cookie_source()
    profile = str(config.get("monitor", "profile_path", default="") or "").strip()
    return {
        "login": read_login_status(),
        "cookie": {"configured": count > 0, "source": source, "count": count},
        "profile": {
            "configured": bool(profile),
            "dir_name": Path(profile).name if profile else "",
            "exists": bool(profile) and Path(profile).exists(),
        },
    }


# ─── 手动写入 ───────────────────────────────────────────────
def save_cookie(cookie_str: str) -> dict:
    """手动写入 Cookie：写配置 + 同步导出 Netscape 文件（程序可直接复用）"""
    from ..adapters.douyin import _parse_cookie_string

    raw = (cookie_str or "").strip()
    if len(raw) > MAX_COOKIE_CHARS:
        raise ValueError(f"Cookie 过长（>{MAX_COOKIE_CHARS} 字符），请检查粘贴内容")
    cookies = _parse_cookie_string(raw)
    if not cookies:
        raise ValueError("Cookie 格式无效，应为 'k1=v1; k2=v2' 形式")
    config.update_section("monitor", {"douyin_cookie": raw})
    _atomic_write(login_cookie_file(), _cookies_to_netscape(_cookie_dict_to_list(cookies)))
    return {"ok": True, "count": len(cookies)}


def save_profile_path(path: str) -> dict:
    """手动写入登录态浏览器目录"""
    p = (path or "").strip()
    config.update_section("monitor", {"profile_path": p})
    return {"ok": True, "dir_name": Path(p).name if p else ""}


# ─── Cookie 导出 ────────────────────────────────────────────
def export_cookie_from_profile(profile_path: str = "") -> dict:
    """从登录态浏览器目录读取 Cookie 并导出 Netscape 文件（不返回明文）"""
    target = resolve_profile_path(profile_path)
    if not Path(target).exists():
        raise RuntimeError("登录态浏览器目录不存在，请先点击「打开登录窗口」完成登录")
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("未安装 playwright，无法从浏览器目录导出 Cookie") from exc

    cookies: list[dict] = []
    last_err: Exception | None = None
    with sync_playwright() as p:
        ctx = None
        for channel in ("chrome", "msedge"):
            try:
                ctx = p.chromium.launch_persistent_context(
                    user_data_dir=target, channel=channel, headless=True,
                    args=["--disable-blink-features=AutomationControlled", "--no-first-run"],
                )
                break
            except Exception as exc:  # 换下一个系统浏览器
                last_err = exc
                continue
        if ctx is None:
            raise RuntimeError(f"无法打开浏览器目录：{last_err}")
        try:
            cookies = list(ctx.cookies())
        finally:
            try:
                ctx.close()
            except Exception:
                pass

    cookies = [c for c in cookies if "douyin" in str(c.get("domain", ""))]
    if not cookies:
        raise RuntimeError("该浏览器目录中没有抖音登录态 Cookie（可能尚未登录）")
    _atomic_write(login_cookie_file(), _cookies_to_netscape(cookies))
    return {"ok": True, "file": LOGIN_COOKIE_NAME, "count": len(cookies)}


# ─── 启动登录 ───────────────────────────────────────────────
def start_login(profile_path: str = "") -> dict:
    """启动可见浏览器登录窗口（独立子进程，不阻塞主服务）"""
    if not bool(config.get("account", "enabled", default=True)):
        raise RuntimeError("账号登录功能已在配置 config.account.enabled 中关闭")
    target = resolve_profile_path(profile_path)
    # 记住目录，登录成功后关注监控的目录抓取自动复用
    config.update_section("monitor", {"profile_path": target})

    current = read_login_status()
    if current["phase"] in _RUNNING_PHASES and _pid_alive(_read_json(status_file()).get("helper_pid")):
        return {"ok": True, "phase": current["phase"], "already_open": True}
    if not LOGIN_SCRIPT.exists():
        raise RuntimeError("登录脚本缺失：scripts/douyin_login.py")

    Path(target).mkdir(parents=True, exist_ok=True)
    kwargs: dict = {
        "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
        "stdin": subprocess.DEVNULL, "cwd": str(ROOT),
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True

    timeout = int(config.get("account", "login_timeout_sec", default=900) or 900)
    proc = subprocess.Popen(
        [sys.executable, str(LOGIN_SCRIPT),
         "--profile", target, "--status-file", str(status_file()),
         "--cookie-file", str(login_cookie_file()),
         "--timeout", str(timeout)],
        **kwargs,
    )
    write_login_status(phase="launching_login", ready=False, verified_at=None,
                       helper_pid=proc.pid, error=None, dir_name=Path(target).name)
    print(f"[account] 已启动登录窗口 pid={proc.pid}（等待扫码）")
    return {"ok": True, "phase": "launching_login", "already_open": False}
