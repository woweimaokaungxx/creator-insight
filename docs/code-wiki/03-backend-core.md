# 03 · 后端核心（配置 / 数据库 / 模型 / 入口 / 适配 / AI）

## 3.1 配置加载 — [app/config.py](../../app/config.py)

`class Config` 全局单例 `config`。

- 加载优先级：`config.example.json` 作 base → `config.json` 深合并覆盖 → 环境变量 `CI_` 前缀覆盖（`CI_<段>__<键>`）。
- JSON 允许行首 `#` 注释（`_load_json_with_comments` 过滤后再 `json.loads`）。
- `_deep_merge(base, override)`：递归合并，override 优先。
- 常用访问：
  - `config.get("app","port") / .host / .port / .data_dir / .db_path`（属性）
  - `config.get("ai","cloud","api_key")`、`config.get("verification","scheduler_enabled")` 等
- `update_section(section, values)`：回写 `config.json`（语义模型激活用它）。

## 3.2 数据库层 — [app/db.py](../../app/db.py)

- `get_conn()`：`sqlite3.connect` + `row_factory=Row` + `foreign_keys=ON` + `journal_mode=WAL` + `busy_timeout=30000`。
- `init_db()`：`executescript(SCHEMA_SQL)` 建 13 张表；随后 `_ensure_column` 做轻量列迁移（`content` 互动列、`transcript.text_full_simplified`、`prediction.interpreted_intent/intent_confidence/direction_source`、`creator.avatar_*`、`verification.auto_applied_at`）；并把历史 `extracted` 状态迁移为 `pending_review`。
- `log_event(conn, entity_type, entity_id, event_type, payload, actor="system")`：写 `event_log` 表（可审计/可回放）。
- 全表 DDL 见 [06-data-model.md](06-data-model.md)。

## 3.3 领域模型 — [app/models.py](../../app/models.py)

纯 Pydantic v2（`BaseModel`），不落库用 `PredictionIn`；库内/接口用其余模型。

- 枚举：`SubjectType` / `Direction` / `Granularity` / `PredictionStatus` / `Verdict`。
- 子结构：`Subject`（type/symbol/name）、`Magnitude`（min/max/unit/is_relative，含 `has_value`）、`TimeWindow`（raw/parsed_*/granularity/is_fuzzy/requires_human_confirmation）。
- 主模型：`PredictionIn`（AI 抽取原始输出）、`Prediction`（带状态库内模型）、`Evidence`、`Verification`、`ReliabilityStats`。
- 注意兜底：`symbol`/`unit`/`is_relative` 允许 `None`（AI 可能对政策类返回 null），已有默认值兜底。

## 3.4 FastAPI 入口 — [app/main.py](../../app/main.py)

- 挂载：CORS 全开、`/static`、`/avatars`（头像静态目录）；`include_router(semantic_router)`。
- **异步 ingest 任务中心**：
  - 内存 `_INGEST_TASKS: dict[str, dict]` + `threading.Lock`。
  - `_new_ingest_task()` / `_update_ingest_task()` / `get_ingest_tasks()` / `get_ingest_task()`。
  - 状态机：`pending → running → success/error`；阶段 `parse→fetch_meta→download/transcript→transcribe→analyze→done`，`progress 0→1`。
  - `_run_ingest_job(task_id, raw_input, use_whisper, mode)`：后台线程主流程（single/all 分支）。
  - `_run_ingest_job_all(...)`：mode=all，`resolve_creator → fetch_creator_videos → _audit_catalog → 逐条处理`，汇总成功/失败并推送通知。
  - `_process_one_video(adapter, content, use_whisper)`：字幕→(whisper)→`ingest_pipeline`→Obsidian→落盘文件。
  - `_save_transcript_files(...)`：写 `data/transcripts/{标题}_简体校对版.md` 与 `{标题}_AI总结.md`。
- **背景任务/启动钩子**：
  - `on_event("startup")`：`backfill_missing_baselines` → `_backfill_content_stats` → 启动验证调度 `_scheduler_loop`（`scheduler_enabled` 时）→ `creator_monitor.start_monitor()`。
  - `on_event("shutdown")`：`stop_monitor()` + cancel 调度任务。
  - `_backfill_content_stats()`：把历史 `raw_meta_json.stats` 回填到 `content` 互动列（V0.6）。
- **请求 Body 模型**：`IngestRequest`、`PredictionReviewRequest`、`HumanVerificationRequest`、`ManualEvidenceRequest`、`SubscriptionRequest`、`SubscriptionUpdateRequest`、`NotificationReadRequest`、`NotificationTestRequest`、`SearchRequest`、`IndexRequest`、`EmbeddingConfigRequest`。
- 全部 `/api` 端点清单见 [07-api-reference.md](07-api-reference.md)。

## 3.5 平台适配层 — [app/adapters/](../../app/adapters/)

### base.py — 抽象与注册表
- 数据类：`ParsedURL`、`CreatorInfo`、`ContentInfo`（含互动字段 `digg_count/comment_count/share_count/collect_count`）、`TranscriptResult`。
- `class PlatformAdapter(ABC)`：抽象方法 `parse_input` / `fetch_creator` / `fetch_content_meta` / `fetch_transcript`；可选 `download_media`、`fetch_creator_videos`。
- 注册表：`register(adapter_cls)` 装饰器把 `adapter_cls.platform` 登记；`get_adapter(platform)` 实例化；`guess_platform(raw)` 按域名+正则+BV号/口令判断平台。

### douyin.py — 抖音（`class DouyinAdapter`）
- 三级 cookie 策略（V0.6）：手动配置 → 本地 `data/cookies/douyin_{vid}.txt` 缓存 → `playwright` 驱动系统 Chrome/Edge 自动生成。
  - `_parse_cookie_string`：`k1=v1; k2=v2` → dict。
  - `_auto_cookie(vid)`：`sync_playwright` 启动浏览器访问抖音拿 `ctx.cookies()` 写 Netscape 文件。
  - `_ensure_cookies(vid)`：按优先级返回 `(cookie_dict, cookie_file)`。
  - `_cookie_file_to_dict` / `_any_cached_cookie`。
- 元数据：详情 API 被风控时，退从页面 `og:title` / `data-e2e` 元数据兜底（`_meta_cache`）。
- 下载：`download_media` 用 `yt-dlp`，匿名失败带 `--cookiefile` 重试。
- 目录抓取（V0.4/V0.6）：`resolve_creator`（主页/分享/作品→sec_user_id）、`_user_profile_api`、`_profile_api`（分页 aweme/post）、`_aweme_to_content`、`fetch_creator_videos`、`_fetch_videos_via_browser`（登录态 Chrome 拦截目录 API，a_bogus 风控时回退）。

### bilibili.py — B 站（`class BilibiliAdapter`）
- `parse_input`（BV/av/短链 b23.tv）、`_view_api`（x/web-interface/view）、`_subtitle_api`、`_meta_via_ytdlp`（API 412 时 yt-dlp 兜底），`fetch_content_meta` 含互动数据映射。

## 3.6 AI 层 — [app/ai/](../../app/ai/)

### provider.py
- 任务常量：`TASK_SUMMARIZE / TASK_EXTRACT_CLAIM / TASK_EXTRACT_PREDICTION / TASK_PARSE_TIME / TASK_VERIFY / TASK_SIMPLIFY`。
- `class AIProvider(ABC)`：`chat(system, user, task, json_mode=True, **kw) -> dict`。
- `class DeepSeekProvider`：OpenAI 兼容流式；`chat_template_kwargs`（NVIDIA NIM）；**双通道重试**（直连 trust_env=False ↔ 代理 trust_env=True 交替，retries 默认 3，重试间隔渐增）；`_stream_chat` / `_extract_json`（容忍 ```json 包裹与前后文字）。
- `class OllamaProvider`：本地 `/api/chat`，`format="json"`。
- `class AIGateway`：`_init_providers` 按配置建 cloud/local；`chat()` 返回 `(result, provider_name)`，按 `ai.task_model_map[task]` 路由顺序（默认 cloud→local），全失败抛 `RuntimeError`。全局单例 `ai_gateway`。

### prompts.py
6 组中文 Prompt，全部要求输出简体中文 JSON：`SIMPLIFY_SYSTEM`（繁体→简体校对）、`SUMMARIZE_SYSTEM`、`EXTRACT_CLAIM_SYSTEM`（观点/预测区分）、`EXTRACT_PREDICTION_SYSTEM`（意图判断、要素 explicit/inferred、宁抽模糊不漏、intent_confidence）、`PARSE_TIME_SYSTEM`、`VERIFY_SYSTEM`（只用 post-time 证据、AI 生成证据链 `ai_generated_evidence`）。