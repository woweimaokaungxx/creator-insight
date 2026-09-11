# creator-insight 项目历史与改进记录（CHANGELOG）

> 博主观点 → 预测 → 事实验证 → 可信度追踪系统
> 本文记录项目从 2026-08-23 起的全部开发历程、每版改动与 V0.8 清理内容。
> 最后更新：2026-08-29（V0.8 清理 + 提示词增强）

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
- AI：云端 DeepSeek 为主 + 本地 Ollama 兜底
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

## 五、当前项目结构（V0.8 清理后）

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

## 六、测试体系

| 脚本 | 覆盖 | 数量 |
|------|------|------|
| `smoke_test.py` | V0.1 平台解析 + 入库 + auto_apply | 16 |
| `v02_test.py` | 验证状态机：确认→到期→AI→人工锁定 | 15 |
| `v03_test.py` | 硬预测自动过 + 24h 撤销 + 画像分桶/Brier | 43 |
| `v04_test.py` | 通知中心多通道 + 触发点 | 22 |
| `v05_test.py` | 异步 ingest 任务 + 简体文案保存 | 27 |
| `ai_evidence_test.py` | AI 生成证据链兜底 | 8 |
| **合计** | | **131** |

> 测试使用 `CI_APP__DATA_DIR` 临时目录，不污染真实库。

---

## 七、已知限制（设计使然）

- **AI 生成证据只对历史预测有效**：AI 知识有截止日期；近期预测需真实搜索或人工证据
- **Tavily 未配置**：搜索框留空，近实时预测需人工贴证据
- **硬预测自动过依赖 AI 置信度**：置信本身是"软"的，靠校准散点图样本积累评估
- **语义检索 422**：模型未安装/未建索引时会返回 422（属业务状态，非故障）

---

## 八、待办 / 路线图

| 优先级 | 事项 |
|--------|------|
| 🟡 可选 | 配 Tavily key 解锁真实新闻搜索 |
| 🔵 可选 | 配 Webhook / 邮件通道 |
| 🔵 收尾 | 处理库里待确认预测 |
| 🟢 规划 | V0.5 更多平台 / 多模型 / 多搜索源 / 插件体系 |
| 🟢 建议 | 爆款拆解 / 选题顾问 / 互动趋势快照 / 博主智能体 |
