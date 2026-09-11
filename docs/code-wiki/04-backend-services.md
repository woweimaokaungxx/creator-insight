# 04 · 后端服务层详解

## 4.1 抽取管线 — [app/services/pipeline.py](../../app/services/pipeline.py)

**函数清单**

| 函数 | 作用 |
|------|------|
| `simplify_transcript(text_full) -> str` | 交 AI 转简体校对版（繁体→简体、修错字、分段）；失败返回原文 |
| `summarize_transcript(text_full) -> dict` | AI 总结（200 字概要 + key_points），记录 provider |
| `extract_claims(text_full) -> list[dict]` | AI 抽取观点 Claim（只取博主本人、区分观点/预测） |
| `extract_predictions(text_full) -> list[PredictionIn]` | AI 抽取预测意图；补语气词概率、启发式标记、后处理过滤 |
| `parse_time(raw_time, prediction_at) -> dict` | AI 把文本时间解析为绝对时间戳（模糊→人工） |
| `compute_auto_apply(pred) -> bool` | 硬预测判定：结构化 type + 幅度 + 方向 + 非模糊时间 + 非模糊语气 |
| `ingest_pipeline(content, transcript_text, segments, transcript_source) -> dict` | **主流程**：三段式入库（见架构文档） |
| `_upsert_creator/_upsert_content/_upsert_transcript/_insert_claims/_insert_prediction` | 写库辅助（upsert + 回填互动数据） |
| `sync_creator_avatar(creator_id)` | 下载并回写头像路径（best-effort，URL 变更才重下） |

**关键子逻辑**
- `_with_offsets(text_full)`：>30000 字时切块只送结尾部分（预测常在总结处）。
- `_estimate_confidence(confidence_raw)`：语气词→概率（`CONFIDENCE_MAP`）。
- `_is_plausible_prediction`：丢弃无预测意图（`NEGATION_WORDS` 告诫/劝阻/反讽、完全不可验证）；保留"说得模糊"的（补 `needs_human_confirmation`）。
- `_apply_intent_heuristics`：direction 推断 / `intent_confidence<0.7` / 时间模糊 → 标 `needs_human_confirmation`。
- `_insert_prediction`：入库 + 时间解析（模糊→due_at=None，待人工）+ `compute_auto_apply` + `_ensure_baseline` 快照 + 写事件日志。

## 4.2 本地转写 — [app/services/transcribe.py](../../app/services/transcribe.py)

- `_find_ffmpeg()`：定位 ffmpeg。
- `extract_audio(media_path, target_dir) -> Path`：FFmpeg 提取 16kHz 单声道 wav。
- `whisper_transcribe(media_path, ...)`：核心。faster-whisper 本地转写；先算缓存键（路径+大小+时间），命中缓存 json 直接返回（秒级断点续转）；产物落盘 md/json/srt 到 `artifacts_dir`（`transcription.artifacts_dir`）。
- `_write_artifacts` / `_transcript_cache_key` / `_fmt_ts`：落盘与缓存辅助。
- `transcribe_to_content(...)`：兼容包装，供 ingest/监控调用。
- 配置：`transcription.whisper.model/device/compute_type/language/retain_media`。

## 4.3 验证引擎（核心）— [app/services/verification.py](../../app/services/verification.py)

**常量**：`VERDICTS`、`VERDICT_SCORE = {correct:1.0, partial:0.5, incorrect:0.0, inconclusive:None, invalid:None}`。

**主流程函数**
| 函数 | 作用 |
|------|------|
| `review_prediction(id, decision, due_at, notes)` | 人工确认：`pending_review→active`（补 due_at）/`invalid` |
| `scan_due_predictions(now) -> list[id]` | 把到期的 `active → due` |
| `collect_evidence(prediction_id) -> dict` | 收集一批证据：baseline 快照 + Tavily 正/反查询（去重）+ yfinance(可选) |
| `run_verification(prediction_id) -> dict` | **验证闭环**：状态→verifying → 收集证据 → 拆 post/pre-time → AI 判读 → 写 verification → 可能自动过 → 状态 final/human_review |
| `submit_human_review(id, verdict, notes, score)` | 人工锁定（五种 verdict，locked=1 后不可改） |
| `add_manual_evidence(...)` | 手工添加带发布时间的证据（未配 Tavily 时用） |
| `undo_auto_apply(id)` | 24h 撤销窗口内解除硬预测自动过，退回 human_review |
| `backfill_missing_baselines()` | 为历史预测补 baseline 快照 |
| `recompute_creator_reliability(creator_id)` | 重算博主画像（见下） |
| `get_creator_profile(creator_id)` | 画像：创作者 + 可靠性 + 已验证预测明细（含校准点） |
| `get_verification_detail(prediction_id)` | 聚合 prediction + verification + evidence |
| `list_verification_queue(status)` | 到期/验证队列列表 |

**画像统计（V0.3）**
- 分桶：`DOMAIN_BY_SUBJECT`（subject type→领域）、`CONFIDENCE_BANDS`（[0-30,30-60,60-80,80-100]）、`_horizon_label`（短/中/长期）。
- `base_accuracy = (correct + partial×0.5)/verified`；`calibration_score = mean((outcome−confidence)²)`（Brier）；`_bucket_stats` 标注 `insufficient`（样本 < threshold=30）。

**硬预测自动过**：`_maybe_auto_apply` 条件 = `auto_apply_eligible` + verdict∈{correct,incorrect} + `ai_confidence≥auto_apply_min_confidence(0.85)` → 系统代填 human_verdict、locked=1、记录 `auto_applied_at`，并 `_notify_auto_applied` 推送通知。

## 4.4 Obsidian 写出器 — [app/services/obsidian.py](../../app/services/obsidian.py)

- `class ObsidianAdapter`，单例 `obsidian`；`active = write_enabled 且 vault_path 存在`。
- 文档格式：`frontmatter` + `<!-- BEGIN AUTO -->...<!-- END AUTO -->`（可被覆盖）+ `<!-- BEGIN HUMAN -->...<!-- END HUMAN -->`（保留人工区）。
- `write_verification(...)`：写验证报告，增量更新时保留已有 human_body。
- `write_prediction(...)`：写预测摘要。
- 落盘路径：`{vault}/{root_folder}/Creators/{博主}/verification_{pred_id[:8]}.md`、`prediction_{pred_id[:8]}.md`。

## 4.5 关注监控 — [app/services/creator_monitor.py](../../app/services/creator_monitor.py)

- 订阅管理：`add_subscription` / `list_subscriptions` / `get_subscription` / `update_subscription` / `get_subscription_videos` / `remove_subscription`（软删除）。
- 抓取：`run_check(sub_id, trigger)`：抓目录 → `_audit_catalog`（JSON 审核：唯一/必填/异作者/数量对账/互动完整度）→ `_store_new_contents`（增量入库）→ 更新 baseline → `_notify_new_videos` + 触发 `_auto_process_videos`。
- 自动处理：`_auto_process_videos`：未转写则下载→`whisper_transcribe`→`ingest_pipeline`→通知成功/失败。
- 调度：`monitor_tick`（手动/后台触发）→ `_monitor_loop`（每分钟 tick，串行处理到期订阅）→ `start_monitor` / `stop_monitor`（后台线程）。
- 爬取任务：`_insert_crawl_task` / `_finish_crawl_task` / `list_crawl_tasks`。

## 4.6 通知中心 — [app/services/notify.py](../../app/services/notify.py)

- `send_notification(title, body, category, payload)`：按 `notify.channels` 分发到 local/webhook/email，返回逐通道结果。
- 通道实现：`_send_local`（写 `notification` 表）、`_send_webhook`（POST JSON 到 `notify.webhook_url`）、`_send_email`（标准库 smtplib SSL/TLS）；未配置通道记录 `failed` 不影响其他。
- `send_test` / `send_test_with`（人工触发测试）；`list_notifications`、`mark_read`、`clear_notifications`。
- 常见 `category`：`new_videos` / `auto_process` / `auto_applied` / `manual`。

## 4.7 头像 — [app/services/avatar.py](../../app/services/avatar.py)

- `download_avatar(avatar_url, platform, platform_id) -> rel_path`；`avatar_dir()`/`avatar_file_path()`；`_safe_ext` 依据 URL/Content-Type 定扩展名。写入 `data/avatars/`，经 `/avatars` 静态暴露。

## 4.8 语义检索子包 — [app/services/semantic/](../../app/services/semantic/)

- `service.py` `SemanticService`（单例 `semantic_service`）：
  - 环境/设置：`runtime` / `get_settings` / `set_active_model`。
  - 下载：`start_download`（后台线程+进度回调）、`get_download_state`、`delete_model`（删模型+清索引）。
  - 索引：`index_predictions`（文本=预测原文+博主+标题）、`index_contents`（文本=标题+互动+简体转写，`_chunk_text` 512/64 重叠分块）；向量 `_pack`（float32 小端）存 `semantic_index`。
  - 检索：`search(query, limit)`——查询向量与全部 `semantic_index` 向量点积（已归一化≈余弦）排序，按 content/prediction 补展示字段。
- `embedder.py`：`runtime_ready()` / `encode(model_id, texts)`（sentence-transformers，返回归一化 float32 向量）。
- `models.py`：`ModelDef` + `MODEL_REGISTRY`（`lightweight`=bge-small-zh-v1.5/512 维；`high_precision`=Qwen3-Embedding-0.6B/1024 维）；`installation_state`/`is_installed`（`.download-complete.json` + config.json + 权重）；`active_model_id`/`set_active_model`/`default_model_root`/`model_directory`（防越界）。
- `download.py`：`download_model(model_id, source)`——ModelScope 断点续传（Range）+ HuggingFace `snapshot_download`；写下载完成标记；设置 `NO_PROXY` 走国内源。