"""AIProvider 抽象 + 云端/本地实现（docs/13 · Q6=d）"""
from __future__ import annotations

import json
import time
from abc import ABC, abstractmethod
from typing import Any, Optional

import httpx

from ..config import config

TASK_SUMMARIZE = "summarize"
TASK_EXTRACT_CLAIM = "extract_claim"
TASK_EXTRACT_PREDICTION = "extract_prediction"
TASK_PARSE_TIME = "parse_time"
TASK_VERIFY = "verify"
TASK_SIMPLIFY = "simplify"  # 转写文本简体校对


class AIProvider(ABC):
    """AI 提供方抽象。所有任务返回结构化 JSON。"""
    name: str = "base"

    @abstractmethod
    def chat(self, system: str, user: str, task: str,
             json_mode: bool = True, **kw) -> dict[str, Any]:
        """调用模型，json_mode=True 时保证返回 dict"""
        ...


class DeepSeekProvider(AIProvider):
    """OpenAI 兼容云端实现（DeepSeek / NVIDIA NIM 等），流式 + 支持 chat_template_kwargs"""
    name = "deepseek"

    def __init__(self, api_key: str, base_url: str, model: str, max_tokens: int,
                 chat_template_kwargs: dict | None = None):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.max_tokens = max_tokens
        # NVIDIA NIM 系模型需要 chat_template_kwargs（如 thinking/reasoning_effort）
        self.chat_template_kwargs = chat_template_kwargs or {}

    def chat(self, system: str, user: str, task: str,
             json_mode: bool = True, **kw) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": kw.get("temperature", 0.2),
            "max_tokens": kw.get("max_tokens", self.max_tokens),
            "stream": True,   # NVIDIA 端流式更稳，非流式易超时断开
        }
        if self.chat_template_kwargs:
            payload["chat_template_kwargs"] = self.chat_template_kwargs
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        # 双通道重试：直连优先，代理兜底；NVIDIA 免费 key 有限流，重试间隔渐增
        max_retries = int(kw.get("retries", 3))
        timeout = float(kw.get("timeout", 600))
        last_err: Exception | None = None
        for attempt in range(max_retries):
            use_proxy = (attempt % 2 == 1)
            try:
                content = self._stream_chat(payload, headers, timeout,
                                            trust_env=use_proxy)
                if json_mode:
                    return self._extract_json(content)
                return {"text": content}
            except Exception as e:
                last_err = e
                if attempt < max_retries - 1:
                    import time as _time
                    _time.sleep(2.5 * (attempt + 1) + 3)  # 5s, 8s, 11s 渐增
        raise RuntimeError(f"DeepSeek 调用失败（{max_retries} 次重试）: {last_err}") from last_err

    def _stream_chat(self, payload: dict, headers: dict, timeout: float,
                     trust_env: bool = False) -> str:
        import json as _json
        parts: list[str] = []
        # trust_env=False：绕过系统代理直连（NVIDIA 长连接经代理易断开）
        # trust_env=True：走系统代理（直连 DNS 不通时兜底）
        with httpx.Client(timeout=timeout, trust_env=trust_env) as client:
            with client.stream("POST", f"{self.base_url}/chat/completions",
                               json=payload, headers=headers) as resp:
                resp.raise_for_status()
                for line in resp.iter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = _json.loads(data)
                        delta = chunk["choices"][0]["delta"]
                        if delta.get("content"):
                            parts.append(delta["content"])
                        # 可选：累加 reasoning_content（不影响输出）
                    except Exception:
                        continue
        return "".join(parts)

    @staticmethod
    def _extract_json(content: str) -> dict[str, Any]:
        import json as _json
        s = content.strip()
        # 去掉 ```json ... ``` 包裹
        if s.startswith("```"):
            s = s.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        # 提取第一个 { ... } 块（容忍前后有推理文字）
        start, end = s.find("{"), s.rfind("}")
        if start != -1 and end != -1 and end > start:
            s = s[start:end + 1]
        return _json.loads(s)


class OllamaProvider(AIProvider):
    """本地 Ollama 兜底（Qwen2.5 等）"""
    name = "ollama"

    def __init__(self, base_url: str, model: str):
        self.base_url = base_url.rstrip("/")
        self.model = model

    def chat(self, system: str, user: str, task: str,
             json_mode: bool = True, **kw) -> dict[str, Any]:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "options": {"temperature": kw.get("temperature", 0.2)},
        }
        if json_mode:
            payload["format"] = "json"
        try:
            with httpx.Client(timeout=kw.get("timeout", 300)) as client:
                resp = client.post(f"{self.base_url}/api/chat", json=payload)
                resp.raise_for_status()
                content = resp.json()["message"]["content"]
            if json_mode:
                # Ollama json 模式可能带 ```json 包裹
                content = content.strip()
                if content.startswith("```"):
                    content = content.split("\n", 1)[1].rsplit("```", 1)[0]
                return json.loads(content)
            return {"text": content}
        except Exception as e:
            raise RuntimeError(f"Ollama 调用失败: {e}") from e


# ─── AI 每日用量上限与告警（docs/14 风险#14：API 成本失控）──────
_USAGE_FILE_NAME = "ai_usage.json"


def _usage_file():
    return config.data_dir / _USAGE_FILE_NAME


def _load_usage() -> dict:
    """读取今日用量；跨日自动归零"""
    today = time.strftime("%Y-%m-%d")
    try:
        f = _usage_file()
        if f.exists():
            data = json.loads(f.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("date") == today:
                return data
    except Exception:
        pass
    return {"date": today, "count": 0, "warned": False}


def _save_usage(data: dict) -> None:
    try:
        _usage_file().write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception as exc:
        print(f"[ai] 用量计数写入失败: {exc}")


def current_usage() -> dict:
    """今日用量快照（供设置页展示）"""
    return _load_usage()


def _push_usage_warning(count: int, threshold: int) -> None:
    try:
        from ..services.notify import send_notification  # 延迟导入避免循环
        send_notification(
            title="AI 用量告警",
            body=f"今日 AI 调用已达 {count} 次（告警阈值 {threshold} 次），请注意成本。",
            category="manual",
        )
    except Exception as exc:
        print(f"[ai] 用量告警推送失败: {exc}")


def _guard_usage() -> None:
    """调用前校验每日上限；达到告警阈值时推送一次通知"""
    limit = int(config.get("ai", "daily_call_limit", default=0) or 0)
    warn = int(config.get("ai", "daily_call_warn", default=0) or 0)
    if limit <= 0 and warn <= 0:
        return
    usage = _load_usage()
    if limit > 0 and int(usage.get("count", 0)) >= limit:
        raise RuntimeError(
            f"已达每日 AI 调用上限（{limit} 次）。可在「系统设置」调整 ai.daily_call_limit，或次日再试"
        )
    usage["count"] = int(usage.get("count", 0)) + 1
    if warn > 0 and not usage.get("warned") and usage["count"] >= warn:
        usage["warned"] = True
        _push_usage_warning(usage["count"], warn)
    _save_usage(usage)


class AIGateway:
    """任务→模型路由（config.task_model_map）+ 云端/本地兜底"""

    def __init__(self):
        self._cache: dict[str, AIProvider] = {}
        self._init_providers()

    def reload(self) -> None:
        """重新读取配置重建 provider（设置页改配置后调用）。

        注意：必须**原地**更新（清空 + 重建），不能重新赋值模块级单例——
        其它模块在导入时已绑定该对象引用，重新赋值不会生效。
        """
        self._cache.clear()
        self._init_providers()

    def _init_providers(self):
        cloud_cfg = config.get("ai", "cloud", default={})
        if cloud_cfg.get("api_key"):
            self._cache["cloud"] = DeepSeekProvider(
                api_key=cloud_cfg["api_key"],
                base_url=cloud_cfg.get("base_url", "https://api.deepseek.com"),
                model=cloud_cfg.get("model", "deepseek-chat"),
                max_tokens=int(cloud_cfg.get("max_tokens", 8192)),
                chat_template_kwargs=cloud_cfg.get("chat_template_kwargs"),
            )
        local_cfg = config.get("ai", "local_fallback", default={})
        if local_cfg.get("enabled"):
            self._cache["local"] = OllamaProvider(
                base_url=local_cfg.get("base_url", "http://127.0.0.1:11434"),
                model=local_cfg.get("model", "qwen2.5:7b-instruct-q4_K_M"),
            )

    @property
    def has_cloud(self) -> bool:
        return "cloud" in self._cache

    @property
    def has_local(self) -> bool:
        return "local" in self._cache

    def chat(self, system: str, user: str, task: str,
             json_mode: bool = True, **kw) -> tuple[dict[str, Any], str]:
        """返回 (result, provider_name)，按 task_model_map 路由"""
        _guard_usage()   # 每日用量上限与告警（docs/14 风险#14）
        route = config.get("ai", "task_model_map", default={}).get(task, "cloud")
        order = []
        if route == "local":
            order = ["local", "cloud"]
        else:
            order = ["cloud", "local"]
        errors = []
        for name in order:
            provider = self._cache.get(name)
            if not provider:
                continue
            try:
                return provider.chat(system, user, task, json_mode=json_mode, **kw), name
            except Exception as e:
                errors.append(f"{name}: {e}")
        raise RuntimeError(f"所有 AI 提供方均失败: {'; '.join(errors)}")


ai_gateway = AIGateway()
