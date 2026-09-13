"""V0.7 测试：系统设置（配置中心）。

覆盖：
1. 脱敏读取：密钥只返回掩码，响应不含明文；secrets_configured 正确标记
2. 掩码保留：提交掩码值 / 空值 → 原值不变
3. 深合并：只提交子字段不丢失同段其它字段（update_section 是浅更新的坑）
4. 写入生效并落盘到配置文件
5. 热重载：改 model / base_url 后 ai_gateway 原地生效（引用不变）
6. AI 用量上限：达到上限后拒绝调用；告警阈值只推一次
7. 非法输入拒绝：不可写段 / 段值非对象 / 空请求体
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

os.environ["PYTHONIOENCODING"] = "utf-8"
os.environ["CI_APP__DATA_DIR"] = tempfile.mkdtemp(prefix="ci_v07_")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.ai.provider import ai_gateway  # noqa: E402
from app.config import config  # noqa: E402
from app.services import settings  # noqa: E402

# 配置写入隔离到临时文件，避免污染真实 config/config.json
config.path = Path(tempfile.mkdtemp(prefix="ci_v07_cfg_")) / "config.json"

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


# ── 基准配置 ──
config.update_section("ai", {"cloud": {
    "provider": "openai-compatible", "api_key": "sk-abcdefghijklmn",
    "base_url": "https://api.example.com/v1", "model": "model-1", "max_tokens": 4096,
}})
settings.reload_runtime()

# ── 1. 脱敏读取 ──
print("── 1. 脱敏读取 ──")
data = settings.get_settings()
payload = json.dumps(data["config"], ensure_ascii=False)
check("响应不含明文 API Key", "sk-abcdefghijklmn" not in payload)
masked_key = data["config"]["ai"]["cloud"]["api_key"]
check("密钥返回掩码", "****" in masked_key and masked_key != "sk-abcdefghijklmn")
check("secrets_configured 标记已配置", data["secrets_configured"].get("ai.cloud.api_key") is True)
check("secrets_configured 未配置项为假", data["secrets_configured"].get("search.tavily_api_key") is False)
check("返回 12 个可配置段", len(data["config"]) == 12)
check("含运行时状态", set(data["runtime"].keys()) >= {"ai_cloud", "ai_local", "obsidian_active", "ai_usage_today"})
check("含重启生效字段清单", "app.port" in data["restart_required_fields"])

# ── 2. 掩码保留 ──
print("── 2. 掩码保留（留空/掩码 = 不修改）──")
settings.update_settings({"ai": {"cloud": {"api_key": masked_key}}})
check("提交掩码 → 原值不变", config.get("ai", "cloud", "api_key") == "sk-abcdefghijklmn")
settings.update_settings({"ai": {"cloud": {"api_key": ""}}})
check("提交空串 → 原值不变", config.get("ai", "cloud", "api_key") == "sk-abcdefghijklmn")

# ── 3. 深合并（浅更新陷阱）──
print("── 3. 深合并：部分更新不丢字段 ──")
settings.update_settings({"ai": {"cloud": {"model": "model-2"}}})
cloud = config.get("ai", "cloud")
check("model 已更新", cloud.get("model") == "model-2")
check("api_key 未丢失", cloud.get("api_key") == "sk-abcdefghijklmn")
check("base_url 未丢失", cloud.get("base_url") == "https://api.example.com/v1")
check("max_tokens 未丢失", cloud.get("max_tokens") == 4096)

settings.update_settings({"ai": {"daily_call_warn": 50}})
check("同段标量字段可写", config.get("ai", "daily_call_warn") == 50)
check("同段其余字段仍在", config.get("ai", "cloud", "model") == "model-2")

# ── 4. 落盘 ──
print("── 4. 写入落盘 ──")
saved = config.path.read_text(encoding="utf-8")
saved_obj = json.loads(saved)
check("配置文件已写入 model-2", saved_obj["ai"]["cloud"]["model"] == "model-2")
check("配置文件中 api_key 为明文（落盘为真实配置）",
      saved_obj["ai"]["cloud"]["api_key"] == "sk-abcdefghijklmn")

# ── 5. 热重载 ──
print("── 5. 热重载（原地生效，引用不变）──")
before = ai_gateway
settings.update_settings({"ai": {"cloud": {"model": "model-3", "base_url": "https://api.hot/v1"}}})
provider = ai_gateway._cache.get("cloud")
check("ai_gateway 引用未变（原地更新）", ai_gateway is before)
check("热重载后 model 生效", provider is not None and provider.model == "model-3")
check("热重载后 base_url 生效", provider is not None and provider.base_url == "https://api.hot/v1")

# ── 6. 用量上限与告警 ──
print("── 6. AI 用量上限 ──")
from app.ai.provider import _guard_usage, _load_usage  # noqa: E402

usage_file = config.data_dir / "ai_usage.json"
if usage_file.exists():
    usage_file.unlink()

settings.update_settings({"ai": {"daily_call_limit": 2, "daily_call_warn": 1}})
notified: list = []
import app.ai.provider as provider_mod  # noqa: E402
provider_mod._push_usage_warning = lambda count, threshold: notified.append((count, threshold))

_guard_usage()   # 第 1 次：达到告警阈值
_guard_usage()   # 第 2 次：达到上限
check("用量计数为 2", _load_usage().get("count") == 2)
check("达到告警阈值推送一次", notified == [(1, 1)])
try:
    _guard_usage()   # 第 3 次：超限
    check("超过上限被拒绝", False)
except RuntimeError as exc:
    check("超过上限被拒绝", "上限" in str(exc))

settings.update_settings({"ai": {"daily_call_limit": 0}})
check("上限设为 0 后可继续调用", _guard_usage() is None)

# ── 7. 非法输入 ──
print("── 7. 非法输入拒绝 ──")
for bad, label in (({"unknown_section": {"a": 1}}, "不可写配置段"),
                   ({"ai": "not-a-dict"}, "段值非对象"),
                   ({}, "空请求体"),
                   ({"ai": {"cloud": "not-a-dict"}}, "把嵌套段写成标量")):
    try:
        settings.update_settings(bad)
        check(f"拒绝 {label}", False)
    except ValueError:
        check(f"拒绝 {label}", True)

check("嵌套段未被破坏", isinstance(config.get("ai", "cloud"), dict))

print(f"\n{'=' * 46}")
print(f"V0.7 系统设置：{PASS} 通过 / {FAIL} 失败")
print(f"{'=' * 46}")
sys.exit(1 if FAIL else 0)
