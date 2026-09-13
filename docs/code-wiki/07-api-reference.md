# 07 · REST API 参考

所有端点前缀 `/api`，JSON。定义于 [app/main.py](../../app/main.py) 与 [app/api_semantic.py](../../app/api_semantic.py)。
请求体为 Pydantic 模型（见 [03-backend-core.md](03-backend-core.md)）。

## 内容摄入 / 异步任务

| 方法 | 路径 | 说明 | 请求体 |
|------|------|------|--------|
| POST | `/api/ingest` | 提交摄入，立即返回 `task_id`；`mode`=single/all | `IngestRequest{input, use_whisper, mode}` |
| GET | `/api/tasks/{task_id}` | 查询任务状态/进度/结果 | — |
| GET | `/api/tasks?limit=` | 任务列表（倒序） | — |

## 预测

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/predictions?status=&creator_id=` | 预测列表（最多 200） |
| GET | `/api/predictions/{id}` | 详情（prediction+verification+evidence） |
| POST | `/api/predictions/{id}/review` | 人工确认 active/invalid（需填 due_at）`PredictionReviewRequest` |
| DELETE | `/api/predictions/{id}` | 删除预测及关联数据 |

## 验证闭环

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/scheduler/run` | 手动触发到期扫描 |
| GET | `/api/verification/queue?status=` | 验证队列 |
| GET | `/api/verification/{id}` | 验证详情 |
| POST | `/api/verification/{id}/run` | 收集证据 + AI 初判 |
| POST | `/api/verification/{id}/evidence` | 手工加证据 `ManualEvidenceRequest` |
| POST | `/api/verification/{id}/human` | 人工锁定 `HumanVerificationRequest` |
| POST | `/api/verification/{id}/undo-auto-apply` | 撤销硬预测自动过（24h 窗口） |

## 创作者 / 内容

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/creators` | 创作者列表（含 content_count） |
| GET | `/api/creators/{id}` | 可信度画像 + 已验证预测明细 |
| POST | `/api/creators/{id}/avatar/refresh` | 重新下载头像 |
| POST | `/api/creators/avatars/backfill` | 批量补齐缺头像创作者 |
| GET | `/api/contents?limit=` | 视频列表（含互动数据 + 解析 raw_meta） |

## 订阅监控（V0.4）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/subscriptions` | 关注列表 |
| POST | `/api/subscriptions` | 加入关注 `SubscriptionRequest` |
| GET | `/api/subscriptions/{id}` | 详情 |
| PATCH | `/api/subscriptions/{id}` | 暂停/恢复/改间隔/自动处理 `SubscriptionUpdateRequest` |
| DELETE | `/api/subscriptions/{id}` | 取消关注（软删除） |
| POST | `/api/subscriptions/{id}/check` | 立即抓取 |
| GET | `/api/subscriptions/{id}/videos` | 该源已入库视频 |
| GET | `/api/crawl_tasks?limit=` | 抓取任务记录 |
| POST | `/api/monitor/tick` | 手动触发监控调度检查 |

## 通知（V0.4）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/notifications?limit=&unread_only=` | 通知列表 + 未读统计 |
| POST | `/api/notifications/read` | 标记已读（传 id 单条 / 不传全部）`NotificationReadRequest` |
| DELETE | `/api/notifications` | 清空通知 |
| POST | `/api/notifications/test` | 发送测试通知 `NotificationTestRequest` |

## 语义检索（V0.6）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/search?query=&limit=` | 语义检索（content + prediction） |
| POST | `/api/search/index` | 为视频建索引 `IndexRequest` |
| POST | `/api/search/index/predictions` | 为预测建索引 |
| GET | `/api/semantic/settings` | 模型设置（安装/激活/进度） |
| POST | `/api/semantic/select?model_id=` | 切换激活模型 |
| POST | `/api/semantic/download?model_id=&source=` | 后台下载模型 |
| GET | `/api/semantic/download/{model_id}` | 下载进度 |
| POST | `/api/semantic/delete?model_id=` | 删除模型及其索引 |

> 以上语义路由定义在 [app/api_semantic.py](../../app/api_semantic.py)（`semantic_router`），由 `main.py include_router` 挂载。

## 系统

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/dashboard` | 各预测状态计数 + verification_queue |
| GET | `/api/status` | 应用/版本/AI 云端与本地/Obsidian/语义/DB 路径 |

## 静态资源

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/` | 返回 `static/vue/index.html`（Vue 模块化前端） |
| GET | `/assets/*` | Vue 构建产物（JS / CSS） |
| GET | `/static/*` | 静态目录挂载 |
| GET | `/avatars/*` | `data/avatars/` 头像静态暴露 |
| GET | 其它前端子路由 | **SPA 回退**：返回 `static/vue/index.html`，支持直接访问/刷新 `/monitoring`、`/predictions` 等（`/api`、`/assets`、`/static`、`/avatars` 前缀除外，仍返回 JSON 404） |