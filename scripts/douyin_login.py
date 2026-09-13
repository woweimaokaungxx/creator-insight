"""抖音可见浏览器登录脚本（独立进程运行）。

由 ``app/services/account.py`` 以子进程方式启动，也可手工执行:

    python scripts/douyin_login.py --profile <目录> --status-file <json> \
                                   [--cookie-file <txt>] [--timeout 900]

流程:

1. ``launch_persistent_context(headless=False)`` 打开**可见**浏览器（系统 Chrome/Edge）
2. 打开抖音首页，用户自行扫码或输入账号登录
3. 每 5 秒轮询登录态（页面内调抖音接口，未登录状态码 2483）
4. 登录成功 → 等 2 秒让浏览器落盘 → 导出 Netscape Cookie → 写状态 → 自动关窗
5. 超时 / 窗口被关闭 / 异常 → 写入对应阶段，供前端展示

登录态由专用浏览器目录持久化，下次直接复用，无需重复扫码。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

NOT_LOGGED_IN_CODE = 2483
POLL_INTERVAL_MS = 5000

# 页面上下文内调用抖音接口判断登录态（2483 = 未登录）
CHECK_JS = """(async () => {
  try {
    const r = await fetch(
      '/aweme/v1/web/general/search/single/?keyword=douyin&search_channel=user&offset=0&count=1&aid=6383&device_platform=webapp&channel=channel_pc_web',
      { credentials: 'include' }
    );
    const payload = await r.json();
    return { ok: r.ok, statusCode: payload && payload.status_code !== undefined ? payload.status_code : null,
             statusMsg: (payload && payload.status_msg) || '' };
  } catch (e) {
    return { ok: false, statusCode: null, statusMsg: String(e) };
  }
})()"""


def _read_status(path: Path) -> dict:
    try:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def _write_status(path: Path, **fields) -> None:
    """合并写入状态文件（保留主服务写入的其它字段）"""
    data = _read_status(path)
    data.update(fields)
    data["helper_pid"] = os.getpid()
    data["updated_at"] = int(time.time())
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _netscape(cookies: list[dict]) -> str:
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


def _atomic_write(path: Path, text: str) -> None:
    """原子写入（固定 \\n，防 Windows 转换污染 Cookie 值）"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def main() -> int:
    ap = argparse.ArgumentParser(description="抖音可见浏览器登录")
    ap.add_argument("--profile", required=True, help="Chrome user-data-dir（登录态目录）")
    ap.add_argument("--status-file", required=True, help="状态 JSON 路径")
    ap.add_argument("--cookie-file", default="", help="导出 Netscape Cookie 的路径")
    ap.add_argument("--timeout", type=int, default=900, help="等待登录超时秒数（默认 900）")
    args = ap.parse_args()

    status_path = Path(args.status_file)
    profile = args.profile

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        _write_status(status_path, phase="helper_error", ready=False,
                      error="未安装 playwright，无法打开登录窗口")
        print("[login] 未安装 playwright")
        return 2

    Path(profile).mkdir(parents=True, exist_ok=True)
    _write_status(status_path, phase="launching_login", ready=False, error=None)

    context = None
    status_code = None
    try:
        with sync_playwright() as p:
            last_err: Exception | None = None
            for channel in ("chrome", "msedge"):
                try:
                    context = p.chromium.launch_persistent_context(
                        user_data_dir=profile, channel=channel, headless=False,
                        args=["--disable-blink-features=AutomationControlled",
                              "--no-first-run", "--disable-default-apps"],
                    )
                    print(f"[login] 已用 {channel} 打开登录窗口")
                    break
                except Exception as exc:
                    last_err = exc
                    continue
            if context is None:
                _write_status(status_path, phase="helper_error", ready=False,
                              error=f"无法启动系统 Chrome/Edge：{last_err}")
                print(f"[login] 无法启动浏览器: {last_err}")
                return 3

            page = context.pages[0] if context.pages else context.new_page()
            page.goto("https://www.douyin.com/", wait_until="domcontentloaded", timeout=120000)
            print("[login] 请在浏览器窗口中扫码登录抖音…")
            _write_status(status_path, phase="waiting_for_login", ready=False, error=None)

            deadline = time.time() + max(60, args.timeout)
            verified = False
            while time.time() < deadline:
                page.wait_for_timeout(POLL_INTERVAL_MS)
                if page.is_closed():
                    _write_status(status_path, phase="browser_closed", ready=False, error=None)
                    print("[login] 浏览器窗口已关闭，登录未完成")
                    return 4

                result = page.evaluate(CHECK_JS) or {}
                status_code = result.get("statusCode")
                ready = status_code is not None and status_code != NOT_LOGGED_IN_CODE
                _write_status(status_path,
                              phase="login_ready" if ready else "waiting_for_login",
                              ready=ready, status_code=status_code,
                              status_msg=result.get("statusMsg") or "")
                if ready:
                    verified = True
                    print("[login] 检测到登录成功，正在保存登录态…")
                    break

            if not verified:
                _write_status(status_path, phase="helper_timeout", ready=False,
                              error=f"等待登录超时（{args.timeout} 秒）")
                print("[login] 等待登录超时")
                return 5

            # 给 Chrome 一点时间把 Cookie 落盘
            page.wait_for_timeout(2000)
            cookies = [c for c in context.cookies() if "douyin" in str(c.get("domain", ""))]
            count = 0
            if args.cookie_file and cookies:
                _atomic_write(Path(args.cookie_file), _netscape(cookies))
                count = len(cookies)
            _write_status(status_path, phase="login_ready", ready=True,
                          verified_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
                          status_code=status_code, cookie_count=count, error=None)
            print(f"[login] 登录成功，已导出 {count} 个 Cookie")
            return 0
    except Exception as exc:
        _write_status(status_path, phase="helper_error", ready=False, error=str(exc)[:300])
        print(f"[login] 登录失败: {exc}")
        return 1
    finally:
        if context is not None:
            try:
                context.close()
            except Exception:
                pass


if __name__ == "__main__":
    sys.exit(main())
