# 05 · 前端工程（Vue3 + Vite + TDesign）

## 工程概览

- 技术：Vue 3（`<script setup>` + TS）、Vite 6、TDesign Vue Next、ECharts、TDesign Icons。
- 脚本：`npm run dev`（Vite dev，端口 5173，`/api` 代理到 `http://127.0.0.1:8781`）；`npm run build`（`vue-tsc -b && vite build`，产物到 `static/vue` 由 FastAPI 托管）；`npm run preview`；`npm run typecheck`。

## 入口与根组件

- [src/main.ts](../../src/main.ts)：`createApp(App).use(TDesign).mount('#app')`。
- [src/App.vue](../../src/App.vue)：无 vue-router，基于 `window.location.pathname+search` 做**手写路由映射**。
  - `PageKey = dashboard/predictions/creators/monitoring/notifications/library/semantic`。
  - `normalizeRoute` / `handleNavigate`（pushState）/ `handlePopState`（popstate 监听）。
  - 各页面参数：如 `/predictions?status=xxx` 传给 `Predictions` 的 `initial-status`。
- [src/layouts/Layout.vue](../../src/layouts/Layout.vue)：整体壳——左侧菜单(TDesign Menu) + 顶栏（页标题 + 全局搜索框 + 通知铃铛角标 + 用户下拉）。
  - 通知角标每 30s 刷新 `getNotifications(20)`；"全部已读"调 `markNotificationsRead(null)`。

## API 封装 — [src/api/client.ts](../../src/api/client.ts)

统一 `request<T>(path, options)` fetch 封装（`BASE='/api'`，非 2xx 抛 `Error(detail)`），并导出全部后端调用 + 对应 TS 接口类型。

**类型接口**：`Prediction`、`IngestTask`、`IngestResult`、`DashboardData`、`CreatorRow`、`CreatorReliability`、`CreatorProfile`、`Subscription`、`NotificationRow`、`ContentItem`、`CrawlTask`、`SemanticResult`、`SemanticModel`、`SemanticSettings`。

**函数分组**
- ingest/任务：`submitIngest(input, useWhisper, mode)`、`getTask`、`getTasks`。
- 预测：`getPredictions`、`getPrediction`、`deletePrediction`、`reviewPrediction`、`getVerificationQueue`、`getVerification`、`runVerification`、`submitHumanReview`、`undoAutoApply`。
- 仪表盘/创作者/内容：`getDashboard`、`getCreators`、`getCreatorProfile`、`getContents`。
- 订阅监控：`getSubscriptions`、`addSubscription`、`updateSubscription`、`deleteSubscription`、`checkSubscription`、`getSubscriptionVideos`、`getCrawlTasks`。
- 通知：`getNotifications`、`markNotificationsRead`、`clearNotifications`、`testNotification`。
- 系统：`getStatus`、`runScheduler`。
- 语义：`semanticSearch`、`semanticIndexContent`、`semanticIndexPredictions`、`getSemanticSettings`、`semanticSelectModel`、`semanticDownloadModel`。

## 页面组件（[src/pages/](../../src/pages/)）

| 页面 | 职责 |
|------|------|
| `Dashboard.vue` | 提交与仪表盘：粘贴链接/分享文案表单（含"仅该视频/博主全部"范围单选、Whisper 开关），提交后轮询 ingest 任务进度；各预测状态计数。 |
| `Predictions.vue` | 预测生命周期管理：按状态过滤（待确认/验证队列/全部）；确认 active/invalid、填 due_at；运行验证、提交人工 verdict、撤销自动过；删除预测（t-popconfirm 二次确认）。 |
| `Creators.vue` | 创作者可信度画像：列出创作者（含头像），点开看正确率/Brier 校准/分桶统计（ECharts 校准散点）。 |
| `Monitoring.vue` | 自动监控：关注管理（加入/立即检查/暂停/恢复/改间隔/取消）、视频列表、抓取任务记录。 |
| `Notifications.vue` | 通知设置：列表/已读/全部已读/清空/发送测试。 |
| `Library.vue` | 视频库：视频卡片网格（点赞/评论/转发/收藏 + 平台/搜索/筛选），含语义搜索开关（关键词/语义模式）。 |
| `Semantic.vue` | 语义检索设置页：模型选择/下载进度/建索引/说明。 |

## 与后端的连接方式

- 开发态：`vite.config.ts` 的 `server.proxy['/api'] → http://127.0.0.1:8781`，前端 5173，后端 8781。
- 生产态：`npm run build` 输出到 `static/vue`，由 FastAPI 同源托管（`/` 与 `/static`）。