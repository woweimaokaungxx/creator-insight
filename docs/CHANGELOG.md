# creator-insight 项目历史与改进记录（CHANGELOG）

> 博主观点 → 预测 → 事实验证 → 可信度追踪系统
> 本文记录项目从 2026-08-23 起的全部开发历程、每版改动与清理内容。
> 最后更新：2026-09-14（V0.15 AI 总结版本化存储 + V0.14 B 站适配器补齐：UP 主投稿目录 / wbi 签名 / 登录态 + V0.13 工程健壮性 + V0.12 系统设置页）

---

## 一、项目是什么

**一句话**：把视频里博主说的"嘴上预测"转化为可验证、可统计、可复盘的结构化数据，到期后联网/AI 收集证据验证，人工确认后统计博主历史正确率，最终用 Obsidian 沉淀为个人决策辅助知识库。

**核心数据链路**：
```
Creator → Content → Transcript → Claim → Prediction → Evidence → Verification → Creator Reliability
```

**产品决策**（2026-08-23）：
- 平台：抖音 + B 站（Adapter 可扩展）
- 领域：财经 + 科技 + 职场科普
- 输入：手动粘贴 URL 或分享文案
- 转写：原生字幕优先 + Whisper 本地兜底
- AI：云端 OpenAI 兼容服务（当前小米 MiMo `mimo-v2.5`）+ 本地 Ollama 兜底
- 存储：SQLite 主存 + Markdown 视图
- 形态：本地 Web App（FastAPI + Vue3 前端）

---

## 二、版本时间线总览

| 版本 | 时间 | 主题 |
|------|------|------|
| V0.1 | 8-23 ~ 8-24 | 基础骨架：解析/转写/AI 抽取/入库 |
| V0.2 | 8-24 | 验证闭环：确认→到期→证据→AI 初判→人工锁定 |
| V0.3 | 8-25 | 博主画像（分桶/Brier 校准）+ 硬预测自动过 + 24h 撤销 |
| V0.4 | 8-26 | 关注监控 + 通知中心（local/Webhook/邮件） |
| V0.5 | 8-26 | 异步 ingest 任务（长视频不超时） |
| V0.5+ | 8-27 | AI 简体化 + 文案落地 + 本地转写增强 + doctor |
| V0.6 | 8-27 ~ 8-28 | 互动数据（赞/评/转/藏）+ Vue 模块化前端 + 语义检索 |
| V0.6+ | 8-28 | 摄取范围（单视频/博主全部）+ 删除预测 |
| V0.7 | 8-29 | 预测抽取三连优化：意图门控 / AI 置信度 / 幅度结构化 |
| V0.7+ | 8-29 | 时间解析激活 + 博主画像上下文校准 |
| V0.8 | 8-29 | 提示词增强（总结/观点抽取）+ 预测空值兜底 + 项目清理 |
| V0.9 | 9-10 | 预测展示统一：AI 总结为主 / 原话为辅 + 未总结标记 |
| V0.10 | 9-11 ~ 9-12 | 仪表盘真实创作者数据 + 头像放大 + 固定导航布局 |
| V0.11 | 9-12 | 抖音账号登录态（登录 + Cookie 四级策略）+ AI 切换小米 MiMo + SPA 路由回退 |
| V0.12 | 9-12 | 系统设置页（配置中心）+ 保存即生效热重载 + AI 每日用量上限与告警 |
| V0.13 | 9-13 | 工程健壮性：任务持久化 + 重试 + 重启断点恢复 + 错误分类 |
| V0.14 | 9-14 | B 站适配器补齐：UP 主投稿目录（wbi 签名）+ resolve_creator + 登录态 |
| V0.15 | 9-14 | AI 总结版本化入库（重跑不覆盖 + 多模型/提示词对比）+ 视频库查看总结与历史版本 |

---

## 三、各版本详细改动

### V0.1 基础骨架（8-23 ~ 8-24）

**目标**：从链接到结构化数据的最小闭环。

- `start.bat` 双击启动、`requirements.txt`
- `config/config.json`（DeepSeek 官方 key）+ 模板
- `app/main.py` FastAPI 入口（ingest/review/scheduler/verification/dashboard）
- `app/db.py` 9 张表 SQLite（WAL + busy_timeout）
- `app/models.py` Pydantic Schema（Prediction/Evidence/Verification）
- `app/adapters/`：base 抽象 + douyin（分享文案/长链解析 + yt-dlp 下载 + 主页目录抓取）+ bilibili（API 412 时 yt-dlp 兜底）
- `app/ai/`：provider.py（DeepSeek + Ollama 兜底）+ prompts.py（5 组任务 Prompt）
- `app/services/`：pipeline（三段提交防锁）、verification、obsidian、transcribe、creator_monitor、notify
- `static/index.html` 单页 UI
- `scripts/`：smoke_test（16 项冒烟测试）

**排掉的坑**：
1. NVIDIA NIM 免费 key 不稳定 → 换 DeepSeek 官方 API（快 40 倍）
2. `database is locked` → pipeline 拆三段：基础数据先 commit → AI 无事务 → 结果再 commit
3. 代理长连接断开 → 直连优先 + 代理兜底双通道
4. 3 个 Schema 校验 bug → `subject.symbol` / `magnitude.unit` / `is_relative` 允许 None 兜底
5. Whisper 强制 zh 导致英文空转写 → language=None 自动检测

### V0.2 验证闭环（8-24）

**目标**：预测从抽取到人工锁定完整状态机。

- Prediction 人工确认：`pending_review → active/invalid`，支持补填 `due_at`
- Scheduler：周期扫描，到期自动标记 `due`
- Baseline Snapshot：每条新预测入库即写 `relation=baseline`（时间隔离审计锚点）
- Evidence Collector：Tavily 正/反查询 + 可选 yfinance + UI 手工证据
- Verification Engine：只使用预测后证据；**无证据时 AI 生成证据链兜底**（`ai_generated_evidence`，credibility=0.4，发布时间强制 ≥ 预测时间防幻觉）
- Human Review：五种 verdict，人工锁定后不可修改
- Reliability：锁定后重算基础正确率
- Obsidian：AI 初判/人工锁定后写 Verification 报告，frontmatter + HUMAN 区双层保护
- 历史兼容：V0.1 的 `extracted` 记录迁移到 `pending_review`

### V0.3 博主画像 + 硬预测自动过（8-25）

**目标**：博主可信度量化 + 高置信预测自动锁定。

- 置信度分桶：`accuracy_by_confidence_band`（0-30% / 30-60% / 60-80% / 80-100%）
- 领域/期限/标的 分桶：subject type → 领域；due_at−prediction_at → 短/中/长期
- Brier 校准：`calibration_score` = mean((outcome − confidence)²)
- 样本量门槛：`verified_count < 30` 警告"统计仅供参考"
- 校准散点：SVG 散点图（x=AI 置信度，y=实际结果，虚线=理想校准）
- **硬预测自动过**：`auto_apply_eligible` + AI 判定 correct/incorrect + 置信 ≥0.85 → 系统代填 human_verdict 直接锁定 final
- **24h 撤销窗口**：`undo-auto-apply` 接口窗口内可撤回，超窗锁定不可篡改
- 画像 API：`GET /api/creators/{id}`

### V0.4 关注监控 + 通知中心（8-26）

**目标**：从"手动贴链接"升级为"关注博主 → 定期自动抓新视频"。

- `subscription` 表：关注规则（source_key 唯一/检查间隔/自动处理/基线 videoId 集/软删除）
- `crawl_task` 表：抓取任务记录
- `douyin.py` 主页目录抓取：`resolve_creator` + `fetch_creator_videos`（aweme/post 分页）
- JSON 审核：videoId 唯一/必填字段/异作者检测/数量对账
- 增量发现：baseline 对比，仅上报新视频
- 自动处理：`auto_process` 后台串行走完整管线
- 调度器：后台线程每分钟 tick
- **通知中心**：`notification` 表 + `notify.py`（local 站内/Webhook POST JSON/邮件 smtplib）
- 触发点：新视频发现 / 自动处理完成 / 硬预测自动锁定
- 前端铃铛角标 + 下拉通知中心

### V0.5 异步 ingest 任务（8-26）

**目标**：解决长视频同步接口必然超时（90MB/12 分钟视频转写 15+ 分钟）。

- `POST /api/ingest` 提交即返回 task_id（0.03s）
- `GET /api/tasks/{id}` 查询：status/stage/progress/message/result/error
- 内存任务表 `_INGEST_TASKS` + 锁，状态机 pending→running→success/error
- 阶段进度：parse→fetch_meta→transcript→download→transcribe→analyze→done
- 完成通知：成功推「视频处理完成」通知
- 前端表单轮询进度（转写阶段 8s 间隔）

### V0.5+ AI 简体化 + 文案落地（8-27）

**需求**：Whisper 直出繁体，用户要求所有返回为简体，且保存简体文案 + AI 总结两个文件。

- `TASK_SIMPLIFY` 新增简体化 AI 任务（转简体/修错字/按语义分段/存疑〔〕标注）
- 所有 AI prompt 强制"输出使用简体中文"
- `transcript` 表新增 `text_full_simplified` 列
- ingest 流程：先简体化 → 后续 AI 用简体文本
- 文件落地：`data/transcripts/` 存 `{标题}_简体校对版.md` + `{标题}_AI总结.md`

### V0.5+ 本地转写增强 + doctor（8-27）

- `transcribe.py`：FFmpeg 提取 16kHz 单声道 wav → faster-whisper → 落盘 md/json/srt
- 断点续转：同一媒体（路径+大小+时间）命中缓存直接返回（60.6s → 0.0s）
- `scripts/doctor.py`：一键诊断（配置文件/AI key/FFmpeg/faster-whisper/playwright/yt-dlp/数据目录/后端可启动）

### V0.6 互动数据 + Vue 前端（8-27 ~ 8-28）

**需求**：抓取视频点赞/转发/评论/收藏数量，参考 douyin-creator-distill 前端。

- `content` 表新增 `digg_count`/`comment_count`/`share_count`/`collect_count` 4 列
- 抖音 adapter 统计映射 + 详情 API 风控时 Playwright 页面兜底
- 抖音 cookie 修复：`_ensure_cookies` 之前只返回文件，httpx 仍被风控；现解析文件为 dict
- 存量回填脚本
- `/api/contents` 返回互动字段
- **Vue3 + TDesign + ECharts 模块化前端**：Dashboard/Predictions/Creators/Monitoring/Notifications/Library 六页

**抖音免配置下载**（V0.6 重要修复）：
- 三级 cookie 策略：手动配置 → 本地缓存 → playwright 驱动系统 Chrome 现场生成
- 元数据兜底：详情 API 风控时从页面 `og:title`/`data-e2e` 抓标题/作者/发布时间
- yt-dlp 匿名失败后自动用 cookie 重试

### V0.6+ 摄取范围 + 删除预测（8-28）

- `IngestRequest.mode`：single（默认）/ all（解析博主全部视频）
- `DELETE /api/predictions/{id}`：删除预测 + 关联数据（verification/evidence/semantic_index/event_log）

### V0.6+ 语义检索（8-28）

**需求**：跨视频语义检索（参考 douyin-creator-distill 智能检索）。

- 复用 `app/services/semantic/` 包，接入 Qwen3-Embedding-0.6B（1024 维）
- `index_contents()`：视频「标题+互动+简体转写」分块向量化存 `semantic_index`
- `search()` 支持 content + prediction 两种 target
- API：`/api/search`、`/api/search/index`、`/api/semantic/*`
- 前端视频库语义搜索开关 + Semantic.vue 设置页（模型选择/下载/建索引）

### V0.7 预测抽取三连优化（8-29）

**P0 意图门控**：博主"别幻想黄金暴涨"被误判为预测的问题。
- `EXTRACT_PREDICTION_SYSTEM` 重写为"意图分析器"：三步判断 + 反例门控（告诫/反事实/修辞/历史回顾）+ few-shot
- `_is_plausible_prediction` 硬过滤：告诫词/模糊时间词丢弃
- `_with_offsets` 全文抽取不截断（>30000 字取结尾段落）

**P1-1 AI 置信度**：让 AI 结合语境直接输出置信度。
- 评估规则：语气词基准 + 否定/保留表达降档 + 具体性加分 + inferred 上限
- `CONFIDENCE_DOWNGRADE_WORDS` 否定词表；`_apply_intent_heuristics` 置信调整

**P1-2 幅度结构化**：AI 漏填时规则提取。
- 幅度 unit 映射：ratio（涨超10%）/threshold（站上2450）/absolute/range（3500~3800）
- `_normalize_magnitude` 正则兜底提取，无数字不编造

### V0.7+ 时间解析 + 博主画像上下文（8-29）

**P2-1 时间解析激活**：
- `PARSE_TIME_SYSTEM` 相对时间推断规则（周一/明天/下周/未来30天/本季度/Q3）
- **关键修复**：`parse_time` 之前是死代码从未被调用！`_insert_prediction` 现在真正调用（此前所有时间都标 fuzzy → due_at=None）

**P2-2 博主画像上下文**：
- `_creator_profile_context` 按博主名查历史验证记录拼进 prompt 校准置信度

---

## 四、V0.8 本次改动详情（8-29）

### 1. 总结提示词增强（SUMMARIZE_SYSTEM）

**需求**：原提示词"200字以内的内容概要"过于简单，AI 为凑短会省略重要内容。

**改动**（`app/ai/prompts.py`）：
- 内容完整性优先：必须覆盖核心主题/主要观点/关键论据/结论建议，逐字稿再长也不能省略重要信息
- 篇幅：summary 目标 200 字左右（180~260 可接受），少于 150 字视为不合格
- 新增 `word_count` 输出字段（AI 自报中文字数，供代码校验）

**配套**（`app/services/pipeline.py`）：
- `summarize_transcript` 新增字数校验兜底：AI 返回 summary <150 字时打印 `word_count_warning`，不阻断流程

### 2. 观点抽取提示词增强（EXTRACT_CLAIM_SYSTEM）

**需求**：原提示词只输出 text/category/offset，过于简单。

**改动**（`app/ai/prompts.py`）：
- 新增 6 个元信息字段：`topic`（主题）/`stance`（支持|反对|中性）/`importance`（0-1 重要度，含三档标准）/`confidence`（博主肯定程度）/`support`（论据）/`key_phrase`（代表短句）
- 新增去重规则（反复强调只留最完整的一次）
- 按 importance 降序输出
- category 增加 `recommendation` 类型

**配套**（`app/db.py` + `app/services/pipeline.py`）：
- `claim` 表新增 6 列：topic/stance/importance/confidence/support/key_phrase
- V0.8 轻量迁移（`_ensure_column` 自动补列）
- `extract_claims` 字段规范化（importance/confidence 钳制 0-1、stance 白名单、topic 默认 general）+ 降序
- `_insert_claims` INSERT 同步写入新字段

### 3. 预测空值兜底修复（_sanitize_prediction_dict）

**问题**：AI 对"博主没说时间"输出 `time_expression_raw: null`，pydantic 对显式 None 不启用默认值 → 整条预测校验失败被丢弃。

**改动**（`app/services/pipeline.py`）：
- 新增 `_sanitize_prediction_dict()`：清洗 AI 返回 dict
  - 顶层字符串 None → 空串（raw_text/interpreted_intent/time_expression_raw/confidence_raw/inference_notes/speaker）
  - subject/time_window 整体 null 或内部字段 None → 兜底
  - magnitude 内部 unit/is_relative None → 兜底
  - conditions None → `[]`
  - 枚举非法值（subject.type/direction）→ `unknown`
- **实测**：demo 预测从 3 条 → 4 条，"科创50弹性最大"不再被丢弃

### 4. 前端切换回 Vue 模块化版

**问题**：FastAPI 根路由 `/` 指向旧版 `static/index.html`（htmx 单页），用户看到的不是模块化界面。

**原因**：项目里有新旧两套前端并存（`static/index.html` 旧单页 + `src/` Vue 模块化 + `static/vue/` 构建产物）。

**改动**（`app/main.py`）：
- 挂载 `/assets` → `static/vue/assets`（Vue 构建产物资源）
- 根路由 `/` 优先返回 `static/vue/index.html`，缺失时 503 提示重新构建

### 5. 项目清理（删除无用文件）

| 删除项 | 类型 | 理由 |
|--------|------|------|
| `scripts/_test_dl.py` | 临时调试脚本 | 一次性验证，下划线前缀 |
| `scripts/_test_parse.py` | 临时调试脚本 | 一次性验证 |
| `scripts/_test_sanitize.py` | 临时调试脚本 | 一次性验证 |
| `scripts/_test_semantic.py` | 临时调试脚本 | 一次性验证 |
| `app/services/embedding.py` | 死代码 | 生产代码零引用，语义检索已走 `semantic/` 包 |
| `static/index.html` | 旧版单页前端 | 被 Vue 模块化版完全覆盖 |
| `static/semantic_models.html` | 旧版语义管理页 | 被 Vue Semantic.vue 覆盖 |
| `data/creator_insight.db.bak_20260827_161200` | 旧数据库备份 | 主库仍在，备份过时 |
| `vite.config.js` / `vite.config.d.ts` | tsc 编译产物 | `vite.config.ts` 才是源 |
| `tsconfig.*.tsbuildinfo` | TS 构建缓存 | 会自动重建 |
| 源码 `__pycache__` | Python 缓存 | 会自动重建 |
| `main.py` 的 `EmbeddingConfigRequest` | 遗留类 | 无任何引用 |

**文档同步更新**：
- `docs/code-wiki/07-api-reference.md`：`/` 返回 `static/vue/index.html`
- `docs/code-wiki/08-dependencies.md`：删除 embedding.py 说明
- `docs/code-wiki/09-run-and-test.md`：访问地址更新
- `docs/18-语义检索与模型管理.md`：管理页改为 Semantic.vue

---

## 五、V0.9 ~ V0.12 改动详情（9-10 ~ 9-12）

### V0.9 预测展示统一为「AI 总结为主、博主原话为辅」（9-10）

**问题**：多处界面仍在展示博主**原话**（`raw_text`），句子口语化、可读性差。库里同时存在 `raw_text`（原话）与 `interpreted_intent`（AI 总结），但展示层优先用了前者。

**改动**：

| 位置 | 改动 |
|------|------|
| `src/pages/Predictions.vue` | 预测内容单元格主文本 = `interpreted_intent`；原话降级为灰色引用行；**缺总结的老数据显示「未总结」黄色标记** |
| `src/pages/Creators.vue` | 已验证预测表同步（主文本=总结，无总结打标记） |
| `src/pages/Library.vue` | 语义检索命中 prediction：主标题 = AI 总结，新增「来自视频：xxx」行，原话作灰色小字（悬停看全文） |
| `app/services/semantic/service.py` | 索引文本改为「**AI 总结优先 + 原话兜底**」（两者都索引，兼容不同措辞命中）；`search` 返回 prediction 补 `video_title` / `due_at` / `content_id` / `url` |
| `app/services/verification.py` | 验证队列查询补 `interpreted_intent` |
| `src/api/client.ts` | `SemanticResult` 新增 `video_title` / `due_at` / `content_id` |

### V0.10 仪表盘真实数据 + 头像放大 + 固定导航（9-11 ~ 9-12）

**仪表盘创作者卡片去假数据**：

- 问题：`Dashboard.vue` 的「创作者可信度画像」卡片是**硬编码假数据**（假博主"价值研究员 Leo"、字母头像、假统计），与库中真实创作者无关
- `app/main.py`：`/api/dashboard` 新增 `featured_creator`（优先取已验证样本最多者）与 `calibration_points`（逐条真实校准点）
- `src/pages/Dashboard.vue`：卡片改真实数据（真实头像 / 正确率 / 样本量 / Brier，无数据显示 `-`），补空态提示；分领域正确率图与校准散点图改真实数据源（无数据显示空态说明）
- `src/api/client.ts`：新增 `DashboardCreator` 类型，`DashboardData` 补 `featured_creator`
- `src/pages/Creators.vue`：列表头像由 40px 放大到 **56px**

**固定导航布局**（`src/layouts/Layout.vue`）：

- 整页锁死视口：`html/body` 禁止整页滚动，`.app-shell` 固定 `100vh`
- 左侧导航栏固定：`height:100vh + overflow-y:auto`（菜单过长时侧栏内部滚动，不随内容滚动）
- 顶栏固定：`.topbar` `flex:0 0 auto`
- 仅内容区滚动：`.content` `flex:1 + min-height:0 + overflow-y:auto`
- 效果：滚动右侧内容时，左侧导航与顶部栏保持不动

### V0.11 抖音账号登录态 + AI 厂商切换 + SPA 回退（9-12）

**抖音账号与登录态**（详见 `docs/20-账号与登录态.md`）：

- `scripts/douyin_login.py`：可见浏览器扫码登录 → 每 5 秒轮询判定（接口状态码 2483 = 未登录）→ 导出 Netscape Cookie → 写状态文件 → 自动关窗
- `app/services/account.py`：账号总览（**脱敏**）、Cookie 手动写入与 Netscape 导出、浏览器目录读写、登录子进程与状态管理
- Cookie 策略由三级升级为**四级**：手动配置 → **登录态文件** → 匿名缓存 → 现场生成；主页目录抓取优先复用登录态
- 6 个接口：`GET/POST /api/account`、`POST /api/account/cookie`、`POST /api/account/login`、`GET /api/account/login/status`、`POST /api/account/export-cookie`
- 前端 `src/components/DouyinAccountCard.vue`：挂在「自动监控」页顶部（状态徽标 + Cookie 写入 + 目录配置 + 登录轮询）
- **顺带修复两个隐患**：① `_cookie_file_to_dict` 默认参数会丢弃 session cookie（`expires=0`，而抖音登录态大量关键 Cookie 正是 session cookie）→ 改 `ignore_discard=True, ignore_expires=True`；② Windows 下 `write_text` 会把 `\n` 转 `\r\n` 污染 Cookie 值 → 统一 `newline="\n"`

**AI 厂商切换为小米 MiMo**：

- `config/config.json` 的 `ai.cloud`：`base_url` → `https://api.xiaomimimo.com/v1`、`model` → `mimo-v2.5`、`api_key` → MiMo 控制台申请
- 实测兼容（Bearer 鉴权 + 流式 + `response_format: json_object`），6 组 AI 任务全部可用

**SPA 路由回退**：

- `app/main.py` 新增 404 处理器：前端子路由（`/monitoring`、`/predictions` 等）返回 `index.html`，刷新不再 404；`/api`、`/assets`、`/static`、`/avatars` 前缀保持原行为

**技术栈文档校正**：`docs/12-tech-stack.md` 已与实现脱节（前端/ AI / 配置三处），本次同步更正。

### V0.12 系统设置页（配置中心）（9-12）

**问题**：修改 AI 密钥 / 通知 / Obsidian / 验证参数等配置**只能手改 `config/config.json`**，无任何界面。

**后端**：

- `app/services/settings.py`：脱敏读取（密钥只返回掩码）、掩码保留写入、**深合并**、热重载调度、AI 连通性测试
- `app/ai/provider.py`：新增 `AIGateway.reload()`（原地重建 provider）；**每日 AI 调用上限与告警**（补齐 `docs/14` 风险 #14：API 成本失控）
- `app/services/obsidian.py`：新增 `ObsidianAdapter.reload()`
- 3 个接口：`GET /api/settings`、`PATCH /api/settings`、`POST /api/settings/ai/test`

**前端**：

- 新增 `src/pages/Settings.vue` + `src/pages/settingsSchema.ts`（分组 schema，便于扩展字段）
- `App.vue` / `Layout.vue` / `client.ts` 注册「系统设置」页与左侧「系统」组菜单入口
- 页面含运行状态卡（云端/本地 AI、Obsidian、今日用量）+ 9 个分组表单 + 「测试 AI 连接」/「保存全部」

**关键设计（三个坑）**：

1. **保存即生效需要原地 reload**：`ai_gateway` / `obsidian` 在**导入时**就把配置缓存进对象；其它模块 `from ..ai.provider import ai_gateway` 已绑定引用，若重新赋值模块级变量则**不生效** → 采用实例 `reload()` 原地更新
2. **掩码保留**：密钥字段留空或提交掩码 → 保持原值；`null` → 显式清空（避免前端把掩码写进配置）
3. **浅更新陷阱**：`config.update_section` 是浅更新，直接提交 `{"cloud": {"model": "x"}}` 会**替换整个 cloud 子段**、丢失 api_key/base_url → 写入前深合并；并把「把嵌套段写成标量」判为非法

**实测**：

- ✅ V0.7 回归 29/29（脱敏 / 掩码保留 / 深合并 / 落盘 / 热重载 / 用量上限 / 非法输入）
- ✅ 页面「测试 AI 连接」→ `provider=cloud`，返回 `{"ok":true,"msg":"pong"}`
- ✅ 页面「保存全部」后：密钥未被掩码覆盖、12 个配置段完整、仅新增 2 个预期字段

---

## 六、当前项目结构（V0.8 清理后）

```
creator-insight/
├── start.bat                      # 双击启动
├── requirements.txt
├── package.json                   # Vue 前端依赖
├── vite.config.ts                 # 前端构建配置（产物 → static/vue）
├── config/
│   ├── config.json                # 运行配置（AI key / 监控 / 通知 / 语义）
│   └── config.example.json        # 模板
├── app/
│   ├── main.py                    # FastAPI 入口（所有 API）
│   ├── api_semantic.py            # 语义检索路由（/api/semantic）
│   ├── db.py                      # SQLite 建表 + 迁移
│   ├── models.py                  # Pydantic Schema
│   ├── config.py                  # 配置加载
│   ├── adapters/                  # 平台适配：base / douyin / bilibili
│   ├── ai/                        # provider.py + prompts.py
│   └── services/
│       ├── pipeline.py            # 总结→简体→Claim→预测→入库
│       ├── verification.py        # 验证引擎 + 自动过 + 画像
│       ├── obsidian.py / transcribe.py / avatar.py / notify.py / creator_monitor.py
│       └── semantic/              # 模型注册/下载/向量化/检索
├── src/                           # Vue3 + TDesign 前端源码
│   ├── App.vue + main.ts + layouts/Layout.vue
│   └── pages/                     # Dashboard/Predictions/Creators/Monitoring/Notifications/Library/Semantic
├── static/vue/                    # 前端构建产物（FastAPI 托管）
├── scripts/                       # 版本回归测试 + doctor + ingest_demo
│   ├── smoke_test.py / v02/v03/v04/v05_test.py / ai_evidence_test.py
│   ├── doctor.py / ingest_demo.py
├── docs/                          # 规格文档 + code-wiki + CHANGELOG
└── data/                          # SQLite / 媒体 / 转写产物 / cookie / 模型 / 头像
```

---

## 七、测试体系

| 脚本 | 覆盖 | 数量 |
|------|------|------|
| `smoke_test.py` | V0.1 平台解析 + 入库 + auto_apply | 16 |
| `v02_test.py` | 验证状态机：确认→到期→AI→人工锁定 | 15 |
| `v03_test.py` | 硬预测自动过 + 24h 撤销 + 画像分桶/Brier | 43 |
| `v04_test.py` | 通知中心多通道 + 触发点 | 22 |
| `v05_test.py` | 异步 ingest 任务 + 简体文案保存 | 27 |
| `v06_test.py` | 抖音账号与登录态：脱敏/配置与文件双写/四级优先级/登录状态机 | 26 |
| `v07_test.py` | 系统设置：脱敏/掩码保留/深合并/落盘/热重载/用量上限/非法输入 | 29 |
| `v08_test.py` | 任务持久化：汇总/吸收态/错误分类/断点恢复/待处理过滤/控制操作/续跑依据/**自动重试策略** | 84 |
| `v09_test.py` | B 站适配器：wbi 签名/Cookie 与匿名指纹/解析/字段映射/分页/风控退出 | 58 |
| `v10_test.py` | AI 总结版本化：重跑不覆盖/当前版本唯一/空总结不写/归一化/事务复用 | 33 |
| `auto_retry_test.py` | 自动重试执行层：真跑重试循环/上限/零重试场景/尝试记录/无明细兜底 | 31 |
| `reader_test.py` | 内容阅读：逐字稿简体优先与回退/历史总结 md 解析/回填版本化 | 31 |
| `delete_test.py` | 视频删除：影响面预览/级联清理完整性/隔离性/操作日志保留/幂等 | 38 |
| `ai_evidence_test.py` | AI 生成证据链兜底 | 6 |
| **合计** | | **459** |

> 测试使用 `CI_APP__DATA_DIR` 临时目录，不污染真实库。

---

## 八、已知限制（设计使然）

- **AI 生成证据只对历史预测有效**：AI 知识有截止日期；近期预测需真实搜索或人工证据
- **Tavily 未配置**：搜索框留空，近实时预测需人工贴证据
- **硬预测自动过依赖 AI 置信度**：置信本身是"软"的，靠校准散点图样本积累评估
- **语义检索 422**：模型未安装/未建索引时会返回 422（属业务状态，非故障）

---

## 九、待办 / 路线图

| 优先级 | 事项 |
|--------|------|
| 🟡 可选 | 配 Tavily key 解锁真实新闻搜索 |
| 🔵 可选 | 配 Webhook / 邮件通道 |
| 🔵 收尾 | 处理库里待确认预测 |
| 🟢 规划 | V0.5 更多平台 / 多模型 / 多搜索源 / 插件体系 |
| 🟢 建议 | 爆款拆解 / 选题顾问 / 互动趋势快照 / 博主智能体 |
| 🟠 规划 | V0.13 工程健壮性：任务持久化 / 重试与错误分类 / 重启断点恢复（设计文档见 `docs/22`、`docs/23`、`docs/adr/0001`） |

---

## 十、V0.13 工程健壮性契约文档（9-13）

本轮为**纯文档产出**（无代码改动），对标参考项目 `douyin-creator-distill` 的 docs 体系，把「工程健壮性」方向的借鉴点固化为契约文档。

**背景问题**：现有异步 ingest 任务是**纯内存**实现（`app/main.py` 的 `_INGEST_TASKS`），
服务重启即丢、失败即终止（`except` 直接置 `error`）、无重试、无单条断点、无历史记录。

| 新增文档 | 内容 |
|---------|------|
| `docs/22-任务与状态契约.md` | 任务 / 明细 / 尝试 / 产物的唯一身份；任务与单条的状态机与合法流转；**10 条不变量**；吸收态与终态保护；待处理集合公式；统计口径统一；与现状实现的 8 条差距对照；`ingest_task` / `ingest_task_item` / `ingest_attempt` 三表设计草案 |
| `docs/23-任务恢复与重试矩阵.md` | 错误三分类（`retryable` / `non_retryable` / `needs_action`）；12 个场景的策略矩阵（自动动作 / 最大次数 / 是否推进基线 / 用户入口）；重试上限与指数退避；暂停与继续语义；**重启断点恢复**；失败带入规则；**不可绕过原则**；用户入口与落地清单 |
| `docs/adr/0001-异步任务持久化.md` | 架构决策记录：为何从内存迁移到本地 SQLite（含「内存 + JSON 快照」「外部队列」两个备选方案的取舍），新建 `docs/adr/` 目录 |

**同步更新**：

- `docs/14-risk-register.md`：新增风险 #20「任务状态丢失」、#21「重试放大成本与风控」，应对均指向 `docs/23`
- `docs/15-roadmap.md`：版本时间线由「只写到 V0.5 的规划」补齐为 **V0.1 ~ V0.13 实际进度**，并标注 V0.13 规划中
- `README.md`：文档索引补 `docs/22`、`docs/23`、`docs/adr/`；Roadmap 补 V0.13
- `PROJECT_STATUS.md`：见 §29

**核心设计取舍**（后续编码须遵守）：

1. **吸收态**：任务 `success` 与明细 `completed` 不得被后续失败降级；重试只针对失败条目
2. **错误三分类**：`needs_action`（登录失效 / 验证码 / 403 / 429 / 配额）**立即停止，不重试也不切换通道绕过**——宁可停住等人工，也不把账号打到风控
3. **汇总方向唯一**：批次状态由明细汇总，不得反向覆盖明细事实
4. **口径不得混用**：批次进度读明细、全局计数读聚合，禁止用"最近 N 条任务"推断全局

---

## 十一、V0.13 工程健壮性落地（9-13）

契约文档（第「十」节）落地为代码。

**问题**：异步 ingest 任务是**纯内存**实现（`app/main.py` 的 `_INGEST_TASKS`），服务重启即丢、失败即终止（`except` 直接置 `error`）、无重试、无断点、无历史。

| 模块 | 内容 |
|------|------|
| `app/db.py` | 新增三表 `ingest_task` / `ingest_task_item` / `ingest_attempt`（`platform_vid` 唯一键 + 索引） |
| `app/services/task_store.py`（新增） | 任务与明细读写、**状态机守卫**、**错误三分类**、批次汇总、**启动断点恢复**、待处理过滤、尝试记录、控制操作 |
| `app/main.py` | 任务读写全部落库（**保留旧函数签名，调用点零改动**）；单条失败**不中断批次**；批量提交前**待处理过滤**；暂停检查；启动 `recover_interrupted()`；新增 3 个控制接口 |
| 前端 | `useIngestTasks.ts` 扩展状态工具；`Dashboard.vue` 支持 partial / waiting_for_action / paused 展示 + **暂停 / 继续剩余 / 重试失败项**；`Layout.vue` 顶栏徽标扩展 |
| `scripts/v08_test.py`（新增） | 62 项回归 |
| `scripts/v05_test.py` | 顺带修复 V0.9 遗留缺陷（FakePipeline 缺 `on_stage`）+ 同步状态断言 |

**落地要点**：

- **吸收态保护**：`completed` 明细与 `success` 任务不可被后续失败降级（实测被守卫拦下并打印拒绝日志）
- **错误三分类**：`needs_action`（登录失效 / 验证码 / 403 / 429 / 配额）→ 任务置 `waiting_for_action` 并**立即停止批次**，不重试、不切换通道绕过
- **重启断点恢复**：启动时 `running → queued`，**不增加业务重试次数**（不变量 #10），幂等可重复执行
- **待处理集合过滤**：批量提交跳过已完成/进行中，历史失败可重入；单条模式视为用户显式重处理意图（不过滤）
- **缺陷修复**：`raw_input` 未随任务持久化 → 会导致「重试/继续」失效，已修复并端到端复验

**验证**：全量回归 **246 项全绿**（新增 v08 62 项；v05 由坏转绿）；端到端提交/落库/分类/回读/控制路由全部通过；无 lint 错误。

---

## 十二、V0.14 B 站适配器补齐（9-14）

**问题**：B 站此前只实现单视频路径，博主维度完全缺失 —— `fetch_creator_videos` 未实现
（基类抛 `NotImplementedError`）、`resolve_creator` 不存在（`mode=all` 会 `AttributeError`）、无任何登录态。

**实现**（`app/adapters/bilibili.py`）：

| 能力 | 内容 |
|------|------|
| **wbi 签名** | `nav` 接口取 img_key/sub_key → 固定置换表生成 mixin_key → `wts` + `w_rid`(md5)；密钥缓存 1 小时 |
| **匿名指纹** | 自动调 `x/frontend/finger/spi` 取 `buvid3`/`buvid4` 并缓存（**解决了空间接口的 412**） |
| `resolve_creator` | 主页链接直接取 mid；视频链接 / BV 号经 `view` API 反查 `owner.mid` |
| `fetch_creator_videos` | `x/space/wbi/arc/search` 分页：`monitor.per_page` 钳制 1~50、`max_pages` 上限、`max_items` 截断、翻页限速 0.5s |
| Cookie 配置 | 新增 `platforms.bilibili.cookie`（含系统设置页「平台」分组 + 密钥脱敏） |
| 字幕 | 请求补 Cookie（此前完全匿名，实际拿不到字幕） |
| 请求头 | 补 `Origin`，降低 WAF 拦截概率 |

**互动数据说明**：空间接口只提供评论数；点赞/转发/收藏需逐条调 `view` API，
由 `platforms.bilibili.fetch_video_stats`（默认 false）控制是否补全。

**实测（真实网络）**：

- ✅ wbi 密钥成功获取；`fetch_creator_videos("2", max_items=3)` 返回 3 条真实投稿（含时长 / 评论数）
- ✅ `fetch_content_meta` 拿全四项互动数据（赞 86812 / 评 8760 / 转 7114 / 藏 11632）
- ✅ `resolve_creator(视频链接)` → `mid=2, name=碧诗`
- ✅ **无需用户 Cookie 即可抓取目录**（匿名指纹解决 412）；Cookie 主要用于字幕

**文档**：新增 `docs/24-平台适配器.md`（两平台获取逻辑、Cookie 策略、风控排错对照）。

**测试**：新增 `scripts/v09_test.py`（58 项，全离线：wbi 复算 / Cookie 与指纹 / 输入解析 / 字段映射 / 分页终止 / 风控安全退出）。

---

## 十三、V0.14+ 长视频转写进度修复（9-14）

**发现**：用 84 分钟 B 站视频（`BV1BoM76iEih`）实测，任务已下载音频（67.8MB）并进入转写，
但任务状态**一直停在「抓取视频元数据」8%**，前端看不到任何进展。

**根因**：单条模式的 `_process_one_video` 调用**漏传 `on_stage` 回调**（批量模式传了），
导致 `_report()` 全部空转，阶段与进度无法上报。

| # | 修复 | 文件 |
|---|------|------|
| 1 | 单条模式补 `on_stage` / `on_progress` 回调 | `app/main.py` |
| 2 | 新增 `_STAGE_PROGRESS` 阶段→进度映射（parse 2% / download 30% / transcribe 45% / analyze 75% / done 100%） | `app/main.py` |
| 3 | Whisper 新增 `on_progress(ratio, done_sec, total_sec)`（按片段结束时间 / 音频总时长） | `app/services/transcribe.py` |
| 4 | 任务消息显示「Whisper 转写 12.5/84.5 分钟（约剩 180 分钟）」（ETA 按已用时间/已完成比例估算） | `app/main.py` |
| 5 | 单条任务**开始时**就登记明细（此前只在成功后登记），使中断可被续跑识别 | `app/main.py` |
| 6 | 新增 `_resume_interrupted_tasks`：启动时重新驱动仍有待处理明细的任务（上限 3 个避免惊群）—— 补齐 `docs/22` §5 承诺 | `app/main.py` |

**实测对比**：

```
修复前：[3.5 分钟无变化] running | fetch_meta  |  8%    | 抓取视频元数据
修复后：          running | transcribe | 45.36% | Whisper 转写 1.0/84.5 分钟（约剩 183 分钟）
```

**顺带确认**：84 分钟视频用 CPU `small` 模型转写约需 **3 小时**（≈0.4x 实时），属**硬件限制**；
已写入 `docs/17-配置与本地转写.md` 的「耗时参考」表与三条加速建议（GPU / 更小模型 / 平台字幕）。

**测试**：`scripts/v08_test.py` 新增 6 项「断点续跑依据」断言（62 → 68）。全量回归 **310 项全绿**。

---

## 十四、Whisper GPU 加速（9-14）

**目标**：把本地转写从 CPU 切到 NVIDIA GPU，解决长视频「CPU 转写要 3 小时」的问题。

**实测对比**（同一 10 秒音频，`small` 模型）：

| 配置 | 转写耗时 | 速度 |
|------|---------|------|
| CPU + `int8` | 5.8s | 1.74x 实时 |
| **GPU(RTX 2060) + `float16`** | **1.4s** | **7.07x 实时** |

→ 约 **4 倍**加速；84 分钟视频预计从 ~3 小时降到 **~12 分钟**。

**关键问题与解决**：直接改 `device: cuda` 会报
`Library cublas64_12.dll is not found or cannot be loaded` —— 因为只有驱动、没有 CUDA 运行时库，
且 CTranslate2 **不会**自动搜索 pip 安装的 nvidia 包目录。

| # | 内容 |
|---|------|
| 1 | `app/services/transcribe.py` 新增 `setup_cuda_dlls()`：把 `site-packages/nvidia/*/bin` **同时**写入 `os.add_dll_directory()` 与 `PATH`（CTranslate2 内部可能用 `LoadLibraryA`，不读前者），仅 Windows 生效、幂等 |
| 2 | 在加载模型前按 `device ∈ {cuda, auto}` 自动调用（用户无需手工配环境变量） |
| 3 | 加载失败时日志带上 `device=` 便于排查 |
| 4 | 依赖：`pip install nvidia-cublas-cu12 nvidia-cudnn-cu12 nvidia-cuda-runtime-cu12`（**无需**系统级 CUDA Toolkit） |
| 5 | 配置：`transcription.whisper.device=cuda` + `compute_type=float16`；`config.example.json` 与 `requirements.txt` 补充说明与显存参考 |

**实测（项目链路）**：`whisper_transcribe()` 读取配置 → 注入 4 个库目录 → GPU 转写成功，
产物 md/json/srt 齐全、语言自动识别 `zh`。

**文档**：`docs/17-配置与本地转写.md` 新增「启用 GPU 加速」小节（前置条件、配置、pip 安装、常见报错表）。

---

## 十五、Whisper 批量推理：GPU 利用率与吞吐优化（9-14）

**问题**：GPU 转写时 `nvidia-smi` 显示利用率仅 **29%**（功耗 17W / 上限 90W、SM 645MHz），远未跑满。

**根因**：默认走 `WhisperModel.transcribe()` 的**逐段串行解码** ——
`condition_on_previous_text=True` 使每一段都依赖前一段输出，GPU 大部分时间在等 CPU。

**方案**：改用 faster-whisper 的 **`BatchedInferencePipeline`**（批处理替代串行）。

**实测**（RTX 2060 / `small` / `float16` / 600 秒音频，同一素材）：

| 方案 | 纯转写 | 速度 | GPU 峰值 | 文本 |
|------|-------|------|---------|------|
| A 当前（逐段串行） | 68.4s | 8.8x | 54% | 3043 字 |
| **B 批量推理 `batch_size=16`** | **15.2s** | **39.5x** | **99%** | 3067 字 |
| C 批量 + `beam_size=1` + `batch=24` | 7.0s | 85.1x | 97% | 3000 字 |

→ 采纳 **B**：**4.5 倍加速**、GPU 峰值打满、文本量与 A 基本一致。

**改动**：

| # | 内容 | 文件 |
|---|------|------|
| 1 | `batch_size > 0` 时用 `BatchedInferencePipeline`，异常自动回退逐段模式 | `app/services/transcribe.py` |
| 2 | 新增配置 `transcription.whisper.batch_size`（默认 16；0 = 关闭） | `config.json` / `config.example.json` |
| 3 | 设置页「本地转写」分组新增「批量推理批大小」 | `src/pages/settingsSchema.ts` |

**副作用（需知）**：批量模式下 VAD 分段更粗（片段数 263 → 23，约每段 26 秒），
SRT 时间戳粒度随之变粗；**文本完整性不受影响**。若需要细粒度时间轴（逐句回听），可设 `batch_size: 0` 回退逐段模式。

**对长视频的意义**：84 分钟视频转写由约 9.5 分钟（串行）降到 **约 3 分钟**（含模型加载）。

---

## 十六、设置页「GPU 加速」一键开关（9-14）

**需求**：开启 GPU 需分别改 `device`、`compute_type`、`batch_size` 三个字段，不够直观。

**实现**：为设置页引入**分组快捷开关**机制（schema 驱动、可复用于其它分组）：

| # | 内容 | 文件 |
|---|------|------|
| 1 | `GroupDef` 新增 `quickSwitch`：定义「判定条件 + 开时写入 + 关时写入」三组配置 | `src/pages/settingsSchema.ts` |
| 2 | 「本地转写（Whisper）」分组接入：**开** = `cuda` + `float16` + `batch_size 16`；**关** = `cpu` + `int8` + `batch_size 0` | 同上 |
| 3 | `Settings.vue` 渲染开关（含说明文字），切换时批量写入表单并提示「记得保存全部」 | `src/pages/Settings.vue` |

**交互验证**（浏览器实测）：

- 关闭 → 「运行设备」自动变 `cpu（通用）`、「精度类型」变 `int8（CPU 最快）`
- 打开 → 回切 `cuda（NVIDIA GPU，推荐）` + `float16（GPU 推荐）`
- 切换只改表单，**仍需点右上「保存全部」**才会落库（避免误改配置）

> 该机制是通用的：任何分组只要声明 `quickSwitch`，即可获得「一键切换多字段」能力。

---

## 十七、V0.15 AI 总结版本化入库（9-14）

**问题**：AI 总结（`summarize` 产出）此前**只写文件**（`data/transcripts/{标题}_AI总结.md`），**不入库**。同一批 AI 响应里其它产出（简体稿 / claims / predictions / 时间解析）全都入库了，只有总结漏了。后果：

- 前端无法展示（视频库、预测页都读接口，接口不返回总结）
- 语义检索索引不到（只索引库内文本）
- 删掉 `data/transcripts/` 即永久丢失

**决策**：在「`content` 表加两列」与「单独建表」之间选择**单独建 `content_summary` 表并版本化**——总结是 AI 生成产物，天然带「模型 + 提示词」上下文；换模型（如切到 MiMo）或改提示词后重跑，直接覆盖会让「哪个版本更好」永久失去依据。对齐参考项目「**重跑不覆盖，新报告即新版本**」。

**改动**：

| # | 内容 | 文件 |
|---|------|------|
| 1 | 新增 `content_summary` 表：`content_id` + `version` 唯一、`is_current` 标记当前版本、记录 `model` / `provider` / `prompt_hash` / `task_id` | `app/db.py` |
| 2 | 新增总结存储服务：版本递增、旧版降级、空总结不写、可复用外部事务 | `app/services/summary_store.py`（新） |
| 3 | 段 3 写入总结（与 claims / predictions 同批提交），`task_id` 由 `_process_one_video` 透传 | `app/services/pipeline.py`、`app/main.py` |
| 4 | 读取接口：`GET /api/contents/{id}/summary`（当前版本）、`/summary/versions`（历史版本） | `app/main.py` |
| 5 | 视频库卡片加「AI 总结」入口：弹窗展示版本徽标 / 模型 / 时间 / 正文 / 要点，多版本时可点击切换对比 | `src/pages/Library.vue`、`src/api/client.ts` |
| 6 | 新增契约文档 | `docs/25-内容总结与版本.md`（新） |

**关键设计**：

1. **重跑不覆盖**：只 `INSERT` 新版本，旧版正文永不 `UPDATE`，仅把 `is_current` 置 0
2. **当前版本唯一**：「降级旧版 + 插入新版」在同一事务内完成
3. **空总结不写**：避免空值占用版本号、留下版本空洞
4. **版本号按视频独立**：`UNIQUE(content_id, version)`，而非全局序列
5. **必须复用外部事务**：SQLite 单写者，`ingest_pipeline` 段 3 持有写事务时若另开连接写会立刻 `database is locked`
6. **md 文件仍照常写出**：文件供人工翻阅备份，库内记录供接口与检索消费，两者并存

**测试**：新增 `scripts/v10_test.py`（33 项：版本递增不覆盖 / 当前版本唯一 / 空总结不写 / 归一化 / 无总结老数据 / 版本隔离 / 事务回滚与提交 / 辅助函数）。

**浏览器实测**：写入两版总结后打开视频库 → 弹窗显示 `v2` + `mimo-v2.5` + 正文 + 3 条要点；展开「历史版本（2）」显示 `v2（当前）` 与 `v1`，点击 `v1` 可切回第一版正文对照（旧版内容完整保留）。验证数据随后已清理。

**存量数据**：本版本之前处理的视频没有总结记录（当时未入库，无法回溯）。重新处理该视频即可生成，页面显示空态而非报错。后续扩展方向见 `docs/25` §10。

---

## 十八、V0.13 补齐：单条自动重试（9-14）

**缺口**：`docs/23` §3 契约承诺「临时故障有限重试（退避，上限 3 次）」，但 V0.13 首版只做了**手动**「重试失败项」——单条失败即直接标记 `failed`，`retryable` 的临时故障（超时 / 连接中断）**不会自动恢复**，必须人工介入。

**改动**：

| # | 内容 | 文件 |
|---|------|------|
| 1 | 明细新增 `auto_retry_count` 列（仅自动重试计数，与 `attempt_count` 分离）；`init_db` 加轻量迁移 | `app/db.py` |
| 2 | 策略常量与判定：`MAX_AUTO_ATTEMPTS=3`、`RETRY_BACKOFF_SECONDS=(5,20,60)`、`should_auto_retry()` / `retry_backoff_seconds()` / `mark_auto_retry()` | `app/services/task_store.py` |
| 3 | 执行层 `_process_with_auto_retry()`：单条与批量共用，失败时退避重试 | `app/main.py` |
| 4 | 前端「自动重试」阶段中文映射 | `src/composables/useIngestTasks.ts` |
| 5 | 契约文档标注 §3 已落地，并更正 §9.2 表设计（补 `platform_vid` 与唯一键说明） | `docs/23`、`docs/22` |
| 6 | 回归测试：v08 §10 新增 16 项 + 新增执行层测试脚本 | `scripts/v08_test.py`、`scripts/auto_retry_test.py`（新） |

**关键设计**：

1. **两个计数器分工**：`attempt_count` 记**执行总次数**（成功 / 失败都计，由执行层**单点维护**，调用方不再 `inc_attempt`）；`auto_retry_count` 只记**自动重试**次数，承载 3 次额度
2. **为什么必须拆开**：重启恢复与用户手动「重试失败项」都是**合理重跑**，不应消耗自动重试额度 —— 否则服务重启几次后，本可自愈的条目会因额度耗尽被直接判失败
3. **`needs_action` 零重试**：登录失效 / 验证码 / 403 / 429 / 配额一律**立即抛出**，不重试、不切换通道绕过（§7 不可绕过原则）
4. **无明细 id 兜底**：单条任务在明细登记失败时拿不到 `item_id`，库内没有计数来源 → 执行层另维护进程内计数并与库内值取大，避免「额度恒为 0 → 无限重试」

**实测**：

- ✅ `scripts/v08_test.py` §10 新增 16 项（上限 3 次 / 退避序列 5-20-60 / 额度不被重启恢复与人工重试消耗 / `completed` 不被自动重试拉回），62 → **84 项**
- ✅ 新增 `scripts/auto_retry_test.py` **31 项**：真跑重试循环（首次成功零重试 / 2 次失败后成功 / 上限 4 次执行 / `needs_action` 零重试 / `non_retryable` 零重试 / 每次尝试逐条落库 / **无明细 id 仍受上限约束**回归）
- ✅ 全量回归 **390 项全绿**
- ✅ 无 lint 错误，前端构建通过

---

## 十九、内容阅读视图 + 存量总结回填（9-14）

**需求**：「AI 总结」与「简体校对版」这两个 md，希望**在前端就能阅读**，不用去翻 `data/transcripts/`。

**发现的两个缺口**：

1. **逐字稿没有接口**：简体校对版其实**早已入库**（`transcript.text_full_simplified`），也写了文件，但**没有任何接口暴露** → 前端读不到
2. **存量总结只在文件里**：V0.15 才让总结入库，之前处理的视频总结只存在于 md 文件中，页面显示空态

**改动**：

| # | 内容 | 文件 |
|---|------|------|
| 1 | 新增 `transcript_store.get_transcript()`：简体校对版优先、回退原始稿，并用 `text_kind` 标明返回的是哪一种 | `app/services/transcript_store.py`（新） |
| 2 | 新增接口 `GET /api/contents/{id}/transcript` | `app/main.py` |
| 3 | 新增一次性回填脚本：解析历史 `*_AI总结.md` 入库（预演 / `--apply` / `--force`，幂等） | `scripts/backfill_summaries.py`（新） |
| 4 | 视频库卡片按钮「AI 总结」→「**阅读**」，弹窗改为 **AI 总结 / 简体校对版** 双标签 | `src/pages/Library.vue`、`src/api/client.ts` |
| 5 | 新增测试 | `scripts/reader_test.py`（31 项） |

**关键设计**：

1. **回退判定看"有没有内容"而非"字段是否存在"**：早期 `text_full_simplified` 可能是 `NULL` 或空串，二者都应回退原稿
2. **前端据 `text_kind` 区分展示**：避免把繁体原稿误当成校对稿
3. **逐字稿刻意不版本化**：它是转写的原始材料（一条视频一份），重跑转写属"重做"而非"新版本"；与总结的版本化策略有意不同
4. **懒加载**：打开弹窗只拉总结（轻），**切到逐字稿标签才拉正文**（可能上万字），同一视频只拉一次
5. **回填匹配用同一个文件名函数**：文件 base 与 `_safe_filename(content.title)` 完全一致，可精确匹配无需模糊；未匹配的文件只跳过、不猜测
6. **回填标注来源**：`provider="file-backfill"` + `prompt_hash="file-backfill"`，与 AI 新生成的版本可区分；`model` 留空（文件里没有该信息）

**实测**：

- ✅ 回填预演：7 个文件中 **6 个精确匹配**（另 1 个是标题空格变体的重复文件，正确跳过）→ `--apply` 写入 6 条 `v1`
- ✅ 幂等：重复执行全部跳过（已写入 0 / 跳过 6）
- ✅ 接口实测：「爆肝2个月」→ 总结 `v1`（285 字 + 8 条要点）+ 逐字稿 `simplified` 11797 字（Whisper 本地转写）
- ✅ 浏览器实测：点「阅读」→ 弹窗双标签；「AI 总结」显示 `v1` + `file-backfill` + 要点 + 复制正文；切「简体校对版」显示 11797 字正文 + 来源 + 复制全文；无总结的视频显示「暂无 AI 总结」空态
- ✅ `scripts/reader_test.py` **31/31**；全量回归 **421 项全绿**
- ✅ 无 lint 错误，前端构建通过

---

## 二十、视频删除与数据清理（9-14）

**需求**：把「只抓了目录、没有逐字稿」的视频删掉，并**加上删除功能**。

**背景**：批量抓取博主目录会给每个作品建一条 `content`（标题 / 链接 / 互动数），
但只有**真正处理过**的视频才有 `transcript`。实测 72 条里有 **63 条**是这样只有元数据的空记录
（其中 **0 条**有预测、**0 条**有观点 —— 删除不会损失任何分析数据）。

**改动**：

| # | 内容 | 文件 |
|---|------|------|
| 1 | 新增删除服务：按依赖顺序清理下游数据，单事务 | `app/services/content_store.py`（新） |
| 2 | 新增 `DELETE /api/contents/{id}` 与 `GET /api/contents/{id}/delete-preview` | `app/main.py` |
| 3 | 阅读弹窗加「删除此视频」+ 二次确认（展示将连带删除的数据量） | `src/pages/Library.vue`、`src/api/client.ts` |
| 4 | 新增批量清理脚本「只抓了目录」的视频 | `scripts/cleanup_empty_contents.py`（新） |
| 5 | 新增测试 | `scripts/delete_test.py`（38 项） |

**为什么需要专门的删除服务**：SQLite 外键**没有 `ON DELETE CASCADE`**，且 `evidence`
同时挂在 `prediction_id` 与 `content_id` 上 —— 直接 `DELETE FROM content` 会被外键挡住
（`FOREIGN KEY constraint failed`）。必须**按依赖顺序**逐层清理：

```
evidence → verification → prediction → claim → content_summary → transcript → content
```

额外两个细节：

- **自引用先断开**：`prediction.parent_prediction_id` 指向同表，同批删除可能触发外键冲突 → 先把该字段置 `NULL`
- **操作日志保留**：`ingest_task_item.content_id` 只是弱引用（无外键），删除时**置 `NULL` 而非删行** —— 任务与尝试历史属于操作日志，不该因作品被删而消失

**清理脚本的安全设计**：

- **默认预演**：不加 `--apply` 只报告，不动数据
- **安全冗余**：即使没有逐字稿，只要还挂着预测或观点也**默认跳过**（`--include-linked` 才删）
- **判定口径**：`content` 无对应 `transcript`

**实测**：

- ✅ `scripts/delete_test.py` **38/38**：影响面预览（预测/观点/证据/验证/总结/逐字稿）/ 级联清理逐表无残留 / **不影响其它视频** / 任务明细保留且引用置空 / 重复删除幂等 / 空视频识别
- ✅ 清理执行：删除 **63** 条（连带预测 0 / 观点 0），剩余 **9** 条（均有逐字稿）
- ✅ 删除后完整性：预测 **28** / 观点 **170** / 证据 **28** / 总结 **6** 全保留；**孤儿记录 0**
- ✅ 浏览器实测：临时视频 → 点「阅读」→「删除此视频」→ 二次确认显示「没有分析数据，删除后不可恢复」→ 确认后提示成功、弹窗关闭、列表刷新（临时视频消失）
- ✅ 全量回归 **459 项全绿**
- ✅ 删除前已用 `sqlite3.backup` 完整备份数据库（含 WAL）
