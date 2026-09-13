"""系统设置服务：配置中心的读取（脱敏）/ 写入（掩码保留 + 热重载）/ AI 连通性测试。

**配置边界说明**（对齐 ``docs/13-module-boundaries.md`` 的「所有模块只能通过
SQLite 交换数据」）：该约束针对**模块间业务数据交换**（业务链路全程 SQLite）；
本模块读写的 ``config/config.json`` 是**单一配置源**，不参与业务数据交换，
不违反该约束。

**脱敏规则**：密钥类字段对外只返回掩码（``sk-cyy5****mvy1i``）；写入时

- 空字符串或掩码占位 → **保留原值**（前端不填即不动）
- ``null`` → **显式清空**
- 其它值 → 覆盖

**热重载**：配置写入后调用各运行时单例的 ``reload()`` **原地**更新
（不能重新赋值模块级单例——其它模块导入时已绑定引用，重新赋值不生效）。
"""
from __future__ import annotations

import copy
from typing import Any

from ..config import config

MASK = "****"

# 需要脱敏的字段：section -> [(嵌套路径...)]
SECRET_FIELDS: dict[str, list[tuple[str, ...]]] = {
    "ai": [("cloud", "api_key")],
    "search": [("tavily_api_key",)],
    "notify": [("smtp", "password"), ("webhook_url",)],
    "monitor": [("douyin_cookie",)],
}

# 可写入的配置段白名单（防止前端写入任意段）
WRITABLE_SECTIONS: tuple[str, ...] = (
    "app", "ai", "search", "market_data", "transcription",
    "platforms", "account", "monitor", "notify", "obsidian",
    "verification", "semantic_search",
)

# 需要重启服务才生效的字段
RESTART_REQUIRED: tuple[str, ...] = ("app.host", "app.port", "app.data_dir")


# ─── 工具 ───────────────────────────────────────────────────
def mask_secret(value: Any, keep: int = 4) -> str:
    """密钥掩码：保留首尾各 keep 位，中间打码"""
    s = str(value or "")
    if not s:
        return ""
    if len(s) <= keep * 2:
        return MASK
    return f"{s[:keep]}{MASK}{s[-keep:]}"


def _dig(node: Any, path: tuple[str, ...]) -> Any:
    cur = node
    for p in path:
        if not isinstance(cur, dict) or p not in cur:
            return None
        cur = cur[p]
    return cur


def _set(node: dict, path: tuple[str, ...], value: Any) -> None:
    cur = node
    for p in path[:-1]:
        if not isinstance(cur.get(p), dict):
            cur[p] = {}
        cur = cur[p]
    cur[path[-1]] = value


def _is_secret_path(section: str, path: tuple[str, ...]) -> bool:
    return any(path == p for p in SECRET_FIELDS.get(section, []))


def _should_keep_secret(value: Any) -> bool:
    """空串 / 掩码占位 → 表示「不修改」；``None`` 表示显式清空"""
    if value is None:
        return False
    s = str(value).strip()
    return (not s) or (MASK in s)


def _clean_values(section: str, values: dict, prefix: tuple[str, ...] = ()) -> dict:
    """递归清理：剔除 ``_`` 说明字段；密钥为空/掩码时不提交（保留原值）"""
    out: dict[str, Any] = {}
    for key, value in values.items():
        if key.startswith("_"):
            continue
        path = prefix + (key,)
        if isinstance(value, dict):
            nested = _clean_values(section, value, path)
            if nested:
                out[key] = nested
            continue
        if _is_secret_path(section, path) and _should_keep_secret(value):
            continue
        out[key] = value
    return out


# ─── 读取（脱敏）────────────────────────────────────────────
def _runtime_status() -> dict:
    from ..ai.provider import ai_gateway, current_usage
    from .obsidian import obsidian

    usage = current_usage()
    return {
        "ai_cloud": ai_gateway.has_cloud,
        "ai_local": ai_gateway.has_local,
        "obsidian_active": obsidian.active,
        "ai_usage_today": int(usage.get("count", 0)),
        "ai_usage_date": usage.get("date"),
    }


def get_settings() -> dict:
    """全部可配置段（密钥脱敏）+ 运行时状态"""
    out: dict[str, Any] = {}
    for section in WRITABLE_SECTIONS:
        val = config.get(section, default={})
        out[section] = copy.deepcopy(val) if isinstance(val, (dict, list)) else val

    secrets_configured: dict[str, bool] = {}
    for section, paths in SECRET_FIELDS.items():
        node = out.get(section)
        if not isinstance(node, dict):
            continue
        for path in paths:
            raw = _dig(node, path)
            if raw is None:
                continue
            _set(node, path, mask_secret(raw))
            secrets_configured[f"{section}.{'.'.join(path)}"] = bool(raw)

    return {
        "config": out,
        "secrets_configured": secrets_configured,
        "mask": MASK,
        "restart_required_fields": list(RESTART_REQUIRED),
        "runtime": _runtime_status(),
    }


# ─── 写入（掩码保留 + 热重载）──────────────────────────────
def _deep_merge(base: dict, patch: dict, path: str = "") -> dict:
    """深合并：只覆盖 patch 中出现的字段，保留其余原值。

    必须深合并——``config.update_section`` 是**浅更新**，若直接提交
    ``{"cloud": {"model": "x"}}`` 会把整个 cloud 子段替换掉，
    丢失 api_key / base_url / max_tokens。

    同时做类型校验：原值为对象但提交了标量 → 拒绝，避免破坏嵌套配置段。
    """
    out = dict(base)
    for key, value in patch.items():
        cur = out.get(key)
        if isinstance(cur, dict) and not isinstance(value, dict):
            raise ValueError(f"字段 {path}{key} 应为对象，收到 {type(value).__name__}")
        if isinstance(value, dict) and isinstance(cur, dict):
            out[key] = _deep_merge(cur, value, f"{path}{key}.")
        else:
            out[key] = value
    return out


def reload_runtime() -> dict:
    """热重载受配置影响的运行时单例（原地更新，引用不变）"""
    from ..ai.provider import ai_gateway
    from .obsidian import obsidian

    ai_gateway.reload()
    obsidian.reload()
    return {
        "ok": True,
        "ai_cloud": ai_gateway.has_cloud,
        "ai_local": ai_gateway.has_local,
        "obsidian_active": obsidian.active,
    }


def update_settings(patch: dict) -> dict:
    """局部写入配置：掩码/空值不修改；写入后热重载运行时"""
    if not isinstance(patch, dict) or not patch:
        raise ValueError("请求体必须是非空 JSON 对象")

    applied: list[str] = []
    for section, values in patch.items():
        if section not in WRITABLE_SECTIONS:
            raise ValueError(f"不可写入的配置段：{section}")
        if not isinstance(values, dict):
            raise ValueError(f"配置段 {section} 必须是对象")
        cleaned = _clean_values(section, values)
        if cleaned:
            existing = config.get(section, default={})
            merged = _deep_merge(existing if isinstance(existing, dict) else {}, cleaned)
            config.update_section(section, merged)
            applied.append(section)

    reload_runtime()
    return {"ok": True, "applied": applied, "settings": get_settings()}


# ─── AI 连通性测试 ──────────────────────────────────────────
def test_ai_connection() -> dict:
    """发一次最小真实请求，验证云端 AI 配置可用"""
    from ..ai.provider import TASK_SUMMARIZE, ai_gateway

    if not ai_gateway.has_cloud:
        raise RuntimeError("未配置云端 AI（ai.cloud.api_key 为空）")
    try:
        result, provider = ai_gateway.chat(
            "你是助手，只输出 JSON。",
            '请输出 JSON：{"ok": true, "msg": "pong"}',
            TASK_SUMMARIZE,
            retries=1,
            timeout=60,
        )
    except Exception as exc:
        raise RuntimeError(f"AI 连接失败：{str(exc)[:200]}") from exc
    return {"ok": True, "provider": provider, "sample": result}
