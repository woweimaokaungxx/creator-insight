"""V0.6 测试：抖音账号与登录态。

覆盖：
1. 账号总览脱敏：不含 Cookie 明文、不含浏览器目录绝对路径
2. 手动写入 Cookie：配置与 Netscape 文件双写
3. Netscape 格式：文件头 / TAB 分隔 / 键值 / 计数
4. 四级 Cookie 策略优先级：手动配置 > 登录态文件 > 匿名缓存
5. 登录状态文件读写 + 进程存活判定（进行中但进程已死 → 窗口已关闭）
6. 非法输入拒绝（空值 / 无 '=' / 超长）
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

os.environ["PYTHONIOENCODING"] = "utf-8"
os.environ["CI_APP__DATA_DIR"] = tempfile.mkdtemp(prefix="ci_v06_")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.adapters.douyin import DouyinAdapter  # noqa: E402
from app.config import config  # noqa: E402
from app.services import account  # noqa: E402

# 配置写入隔离到临时文件，避免污染真实 config/config.json
config.path = Path(tempfile.mkdtemp(prefix="ci_v06_cfg_")) / "config.json"

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


# ── 2. 手动写入 Cookie ──
print("── 2. 手动写入 Cookie（配置 + 文件双写）──")
COOKIE_A = "ttwid=SECRET123; passport_csrf_token=CSRF456"
res = account.save_cookie(COOKIE_A)
check("返回写入数量 2", res.get("count") == 2)
check("配置已写入 douyin_cookie", config.get("monitor", "douyin_cookie") == COOKIE_A)
login_cookie = account.login_cookie_file()
check("已生成 Netscape 文件", login_cookie.exists())

# ── 3. Netscape 格式 ──
print("── 3. Netscape 格式 ──")
text = login_cookie.read_text(encoding="utf-8")
check("文件头正确", text.startswith("# Netscape HTTP Cookie File"))
check("字段以 TAB 分隔", ".douyin.com\tTRUE\t/\tTRUE\t" in text)
check("含 ttwid 键值", "\tttwid\tSECRET123" in text)
check("行数与内容一致", account._count_netscape(login_cookie) == 2)

# ── 1. 账号总览脱敏（在写入含明文 Cookie 之后校验）──
print("── 1. 账号总览脱敏 ──")
ov = account.overview()
payload = json.dumps(ov, ensure_ascii=False)
check("总览不含 Cookie 明文", "SECRET123" not in payload and "CSRF456" not in payload)
check("总览不含目录绝对路径", str(account.cookie_dir().parent) not in payload)
check("cookie 来源为 manual 且计数 2", ov["cookie"]["source"] == "manual" and ov["cookie"]["count"] == 2)
check("目录字段只有名称（无分隔符）",
      not any(ch in (ov["profile"]["dir_name"] or "") for ch in (":", "\\", "/")))

# ── 4. 四级 Cookie 策略优先级 ──
print("── 4. 四级 Cookie 策略优先级 ──")
adapter = DouyinAdapter()

config.update_section("monitor", {"douyin_cookie": "manual=1"})
c1, f1 = adapter._ensure_cookies("vid1")
check("① 手动配置优先（无需文件）", c1.get("manual") == "1" and f1 is None)

config.update_section("monitor", {"douyin_cookie": ""})
login_cookie.write_text(
    "# Netscape HTTP Cookie File\n"
    ".douyin.com\tTRUE\t/\tTRUE\t0\tttwid\tLOGINVAL\n",
    encoding="utf-8",
)
c2, f2 = adapter._ensure_cookies("vid1")
check("② 登录态文件次优先", c2.get("ttwid") == "LOGINVAL" and f2 == login_cookie)

login_cookie.unlink()
auto_file = adapter._cookie_file("vid1")
auto_file.write_text(
    "# Netscape HTTP Cookie File\n"
    ".douyin.com\tTRUE\t/\tTRUE\t0\tttwid\tAUTOVAL\n",
    encoding="utf-8",
)
c3, f3 = adapter._ensure_cookies("vid1")
check("③ 匿名缓存第三优先", c3.get("ttwid") == "AUTOVAL" and f3 == auto_file)
auto_file.unlink()

check("主页抓取优先复用登录态文件",
      adapter._login_cookie_file() == account.login_cookie_file())

# ── 5. 登录状态文件读写 + 进程存活判定 ──
print("── 5. 登录状态与进程存活判定 ──")
check("当前进程判定为存活", account._pid_alive(os.getpid()))
check("不存在的 PID 判定为已退出", not account._pid_alive(999999))

account.write_login_status(phase="waiting_for_login", ready=False, helper_pid=os.getpid())
s1 = account.read_login_status()
check("写回 waiting_for_login（进程存活）", s1["phase"] == "waiting_for_login" and s1["ready"] is False)

account.write_login_status(phase="waiting_for_login", ready=False, helper_pid=999999)
s2 = account.read_login_status()
check("进行中但进程已死 → browser_closed", s2["phase"] == "browser_closed")

account.write_login_status(phase="login_ready", ready=True, helper_pid=os.getpid(),
                           verified_at="2026-01-01T00:00:00")
s3 = account.read_login_status()
check("已登录阶段 ready=True", s3["phase"] == "login_ready" and s3["ready"] is True)
check("保留验证时间", s3["verified_at"] == "2026-01-01T00:00:00")
check("状态文件位于 data 目录内", account.status_file().parent == config.data_dir)

# ── 6. 非法输入拒绝 ──
print("── 6. 非法输入拒绝 ──")
for bad, label in (("", "空字符串"), ("   ", "纯空白"), ("no-equals-sign", "缺少 '='"), ("x" * 30000, "超长")):
    try:
        account.save_cookie(bad)
        check(f"拒绝 {label}", False)
    except ValueError:
        check(f"拒绝 {label}", True)

print(f"\n{'=' * 46}")
print(f"V0.6 账号与登录态：{PASS} 通过 / {FAIL} 失败")
print(f"{'=' * 46}")
sys.exit(1 if FAIL else 0)
