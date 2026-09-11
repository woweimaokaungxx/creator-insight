# 08 · 依赖关系与配置

## 8.1 第三方依赖

### Python（[requirements.txt](../../requirements.txt)）
**必需**

| 依赖 | 用途 |
|------|------|
| `fastapi` / `uvicorn` | Web 框架与 ASGI 服务 |
| `httpx` | 调用 AI/搜索/平台 API（流式 + 双通道代理重试） |
| `pydantic>=2.8` | 请求/领域模型校验 |
| `python-dotenv` | 环境变量辅助 |
| `yt-dlp` | 视频/音频下载（B 站、抖音无水印） |
| `playwright` | 抖音免配置 cookie 生成（驱动系统 Chrome/Edge，无需下载自带浏览器） |

**可选（按需安装）**

| 依赖 | 用途 | 对应功能 |
|------|------|---------|
| `faster-whisper` | 本地 Whisper 转写（体积大） | 字幕兜底 |
| `akshare` / `yfinance` | 结构化财经行情 | 验证证据兜底 |
| `modelscope` / `huggingface-hub` | 下载 embedding 模型 | 语义检索下载器 |
| `sentence-transformers` + `torch`(CPU) | 本地向量化 | 语义检索 embedder |

### 前端（[package.json](../../package.json)）
`vue@3.5`、`tdesign-vue-next@1.10`、`tdesign-icons-vue-next`、`echarts`（依赖）；`vite@6`、`@vitejs/plugin-vue`、`typescript`、`vue-tsc`（开发依赖）。

### 系统工具
- **FFmpeg**：`services/transcribe.py` 提取音频（16kHz 单声道 wav）。
- **Ollama**（可选本地兜底）：`ai/provider.py` OllamaProvider，`http://127.0.0.1:11434`。

## 8.2 配置项（[config/config.json](../../config/config.json)，模板见 config.example.json）

| 配置段 | 关键项 | 说明 |
|--------|--------|------|
| `app` | `host/port/data_dir/log_level` | 服务地址与数据根目录 |
| `ai.cloud` | `api_key/base_url/model/max_tokens` | 云端大模型（DeepSeek 兼容） |
| `ai.local_fallback` | `enabled/base_url/model` | Ollama 本地兜底 |
| `ai.task_model_map` | 每任务 `cloud|local` | 任务→模型路由 |
| `search` | `provider/tavily_api_key/max_results` | 联网搜索（Tavily） |
| `market_data` | `cn/us` | 财经数据源（akshare/yfinance） |
| `transcription` | `prefer/defaultProvider/whisper.*/artifacts_dir` | 转写通道与产物目录 |
| `platforms` | `douyin/bilibili.enabled` | 平台开关 |
| `obsidian` | `vault_path/root_folder/write_enabled/sync_*` | Obsidian 写入门 |
| `verification` | `auto_apply/auto_apply_min_confidence/auto_apply_undo_hours/human_review_required/sample_size_threshold/scheduler_enabled/scheduler_interval_sec` | 验证引擎策略 |
| `monitor` | `enabled/max_pages/per_page/douyin_cookie/profile_path` | 关注监控与目录抓取 |
| `notify` | `enabled/channels/webhook_url/smtp.*` | 通知通道 |
| `semantic_search` | `enabled/active_model/model_root/source/model_path/dimension/device/batch_size` | 语义检索 |

## 8.3 模块间依赖（代码层）

```
main.py ──► config / db / adapters(get_adapter, guess_platform) / models
         ──► services.{creator_monitor, notify, obsidian, pipeline, transcribe, verification}
         ──► services.semantic.semantic_service / api_semantic.router
pipeline ──► adapters.base(ContentInfo) / ai(*) / db / models(PredictionIn) / verification(_ensure_baseline) / avatar
verification ──► ai(VERIFY_SYSTEM, TASK_VERIFY, ai_gateway) / config / db / notify(懒引入)
creator_monitor ──► adapters(get_adapter, resolve_creator, fetch_creator_videos) / transcribe / pipeline / notify / db
transcribe ──► config / (faster-whisper / FFmpeg)
obsidian ──► config
semantic.service ──► embedder / models / download / db / config
avatar ──► config
ai.provider ──► config（httpx）
api_semantic ──► services.semantic
```

> 语义检索统一走 `semantic/embedder.py` + `SemanticService`（V0.8 清理：删除遗留的 `services/embedding.py`）。

## 8.4 反向/循环依赖规避

- `verification._maybe_auto_apply` / `_notify_auto_applied` 通过**函数内 lazzy import** 引入 `notify`，避免模块顶层循环依赖。
- `main._process_one_video` 内 lazzy import `creator_monitor._audit_catalog`（mode=all 用），降低启动耦合。
- AI 层不 import services，仅通过 config 构造 provider，保证"AI 与业务只传数据"。