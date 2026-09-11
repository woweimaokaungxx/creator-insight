# creator-insight · 项目状态总结

> 博主观点 → 预测 → 事实验证 → 可信度追踪系统
> 更新日期：2026-08-26（V0.4 通知环节 落地）

---

## 〇、当前状态总览

```
服务运行中：http://127.0.0.1:8781（v0.6.0，AI 云端已连接）
代码规模：约 3700 行
测试：131 项全绿（smoke 16 + v02 15 + v03 43 + v04 22 + v05 27 + ai_evidence 8）
数据库：2 条预测待确认 / 1 条已 final
```

---

## 一、已完成 ✅

### 1. 产品定义（8-23）

| 项 | 结论 |
|----|------|
| 定位 | 博主预测验证与可信度追踪系统（非视频转文字工具） |
| 数据链路 | Creator → Content → Transcript → Claim → Prediction → Evidence → Verification → Reliability |
| 平台 | 抖音 + B 站（Adapter 可扩展） |
| 领域 | 财经 + 科技 + 职场科普 |
| 形态 | 本地 Web App（FastAPI + SQLite + 浏览器 UI） |
| AI | DeepSeek 官方（云端）+ Ollama 本地兜底（混合） |
| Obsidian | 只写 Verification + 预测摘要，frontmatter + AUTO/HUMAN 双层标记防覆盖 |
| 人工复核 | V0.1-V0.2 每条 100% 人工；V0.3 起硬预测自动过（AI 高置信直接锁定，24h 撤销窗口） |
| 文档 | `docs/` 下 16 份完整规格（README 索引） |

### 2. V0.1 代码（8-23 ~ 8-24）

```text
H:\workbuddytest\creator-insight\
├── start.bat                  # 双击启动
├── requirements.txt
├── config/
│   ├── config.json            # DeepSeek 官方 key 已配置
│   └── config.example.json    # 模板（key 留空）
├── app/
│   ├── main.py                # FastAPI 入口（ingest/review/scheduler/verification/dashboard）
│   ├── db.py                  # 9 张表 SQLite（WAL + busy_timeout）
│   ├── models.py              # Pydantic Schema（Prediction/Evidence/Verification）
│   ├── config.py              # 配置加载（支持 # 注释 JSON）
│   ├── adapters/
│   │   ├── base.py            # PlatformAdapter 抽象 + 注册表
│   │   ├── douyin.py          # 抖音（分享文案/长链解析 + yt-dlp 下载 + 主页目录抓取）
│   │   └── bilibili.py        # B站（API 412 时 yt-dlp 兜底）
│   ├── ai/
│   │   ├── provider.py        # AIGateway（DeepSeek 官方 + Ollama 兜底）
│   │   └── prompts.py         # 5 组任务 Prompt（总结/Claim/预测/时间/验证含证据链）
│   └── services/
│       ├── pipeline.py        # 总结→Claim→预测→入库（三段提交防锁）
│       ├── verification.py    # V0.2 验证闭环 + V0.3 画像统计/硬预测自动过（900+ 行，核心）
│       ├── obsidian.py        # Obsidian 写出器（双层标记）
│       ├── transcribe.py      # Whisper 转写公共服务（ingest/关注监控共用）
│       ├── creator_monitor.py # V0.4 关注监控（订阅/目录抓取/审核/增量/自动处理/调度）
│       └── notify.py          # V0.4 通知中心（local/Webhook/邮件 + 触发点）
├── static/index.html          # 单页 UI（dashboard/待确认/验证队列/复核）
├── scripts/
│   ├── smoke_test.py          # 16 项冒烟测试
│   ├── v02_test.py            # 15 项 V0.2 状态机测试
│   ├── v03_test.py            # 43 项 V0.3 测试（硬预测自动过/24h 撤销/博主画像）
│   ├── v04_test.py            # 22 项 V0.4 测试（通知通道/触发点集成）
│   ├── v05_test.py            # 27 项 V0.5 测试（异步 ingest 任务/简体文案保存）
│   ├── ai_evidence_test.py    # 8 项 AI 生成证据链测试（兼容 V0.3 自动过）
│   └── ingest_demo.py         # 演示数据入库脚本
└── docs/                      # 16 份规格文档
```

### 3. V0.2 验证闭环（8-24）

| 模块 | 结果 |
|------|------|
| Prediction 人工确认 | `pending_review → active/invalid`，支持补填 `due_at` |
| Scheduler | 周期扫描，也可调用 `/api/scheduler/run`，到期自动标记 `due` |
| Baseline Snapshot | 每条新预测入库即写入 `relation=baseline` |
| Evidence Collector | Tavily 正/反查询 + 可选 yfinance + UI 手工证据 |
| Verification Engine | 只使用预测后证据；AI 生成证据兜底 |
| Human Review | 五种 verdict，人工锁定后不可修改 |
| Reliability | 锁定后重算基础正确率与按标的类型统计 |
| Obsidian | AI 初判/人工锁定后写 Verification 报告并保留 HUMAN 区 |
| 历史兼容 | V0.1 的 `extracted` 记录启动时迁移到 `pending_review` |

新增接口：

```text
POST /api/predictions/{id}/review
POST /api/scheduler/run
GET  /api/verification/queue
GET  /api/verification/{id}
POST /api/verification/{id}/evidence
POST /api/verification/{id}/run
POST /api/verification/{id}/human
GET  /api/dashboard
```

### 4. AI 生成证据链兜底（8-24 晚）

**需求**：不配 Tavily 也能验证——让 AI 在回复里直接返回证据链（URL/标题/发布时间/摘要）。

| 改动 | 说明 |
|------|------|
| `prompts.py` | VERIFY_SYSTEM 新增 `ai_generated_evidence` 输出要求 |
| `verification.py` | 无外部证据时也调用 AI 生成证据链；入库 `source=ai_knowledge`、`credibility=0.4`；发布时间强制 ≥ 预测时间（防幻觉时间） |
| `index.html` | 证据列表显示 `[AI生成·未核实]` 标签 |
| `scripts/ai_evidence_test.py` | 新增 6 项测试 |

**实测**（2024 年黄金预测，AI 知识范围内）：verdict=correct（score 1.0, conf 0.85），生成 2 条证据（Reuters + 世界黄金协会，带 URL/时间）✅

### 5. 实测验证（全部通过）

| 项 | 结果 |
|----|------|
| 平台解析（抖音文案/长链、B站 BV/av） | ✅ |
| B 站元数据抓取（API 412 → yt-dlp 兜底） | ✅ |
| Whisper 转写（faster-whisper 本地） | ✅ |
| AI 完整抽取（DeepSeek 真实调用） | ✅ 18 秒完成 |
| 预测入库（黄金/上证/房价 3 条，带置信度） | ✅ |
| Obsidian 双层保护（人工区不被覆盖） | ✅ |
| V0.1 冒烟测试 | ✅ 16/16 |
| V0.2 状态机测试 | ✅ 15/15 |
| V0.3 测试（自动过/撤销/画像） | ✅ 43/43 |
| V0.4 测试（通知通道/触发点） | ✅ 22/22 |
| V0.5 测试（异步任务/简体化） | ✅ 27/27 |
| AI 证据链测试 | ✅ 8/8 |
| 服务 HTTP 验收（含通知中心 API） | ✅ http://127.0.0.1:8781 |

### 6. 过程中排掉的坑

1. **NVIDIA NIM 免费 key 不稳定**（504/断连/限流）→ 换 **DeepSeek 官方 API，快约 40 倍**（18s vs 10-30min）
2. `extra_body` 参数位置（OpenAI SDK 客户端参数 vs HTTP 直连顶层）
3. `database is locked`（AI 调用期间持写事务）→ pipeline 拆三段：基础数据先 commit → AI 无事务 → 结果再 commit
4. 代理长连接断开（127.0.0.1:7890）→ 直连优先 + 代理兜底双通道
5. 3 个 Schema 校验 bug（`subject.symbol` / `magnitude.unit` / `is_relative` 允许 None 兜底）
6. Whisper 强制 zh 导致英文内容空转写 → language=None 自动检测
7. 旧进程占端口导致新代码 404 → netstat 找 PID + Stop-Process 再重启

### 7. 案例库（8-24）

已填充 3 个真实可验证案例到 `docs/16-case-template.md`（附来源 URL）：

| 案例 | 博主 | 预测内容 | 验证时间 |
|------|------|---------|---------|
| 价格 | 清崎（富爸爸作者） | 黄金 $27,000 / 比特币 $250,000 / 白银 $100 | 2026-12-31 |
| 宏观 | 瑞银 Pingle | 下半年两次各 25bp 降息、失业率 4.5%、SPX 7500 | 2026-12-31 |
| 政策 | 任泽平 | 一线限购 1 年外环放开、销售面积 -6.1% | 2027-03 / 2029-03 |

### 8. V0.4 关注监控·抓视频环节（8-24，参考 douyin-creator-distill「关注与更新」）

把「手动粘贴单条链接」升级为「把博主加入关注 → 定期增量抓主页视频目录 → 新视频自动入库」。

| 模块 | 说明 |
|------|------|
| `subscription` 表 | 关注规则：source_key（sec_user_id）唯一、检查间隔、自动处理开关、基线 videoId 集、软删除 |
| `crawl_task` 表 | 抓取任务记录（触发方式/状态/共/新增/错误） |
| `douyin.py` 主页目录抓取 | `resolve_creator`（主页链接/分享文案/作品链接 → sec_user_id）+ `fetch_creator_videos`（`/aweme/v1/web/aweme/post/` 分页） |
| JSON 审核 | 参考 json-audit：videoId 唯一、必填字段、异作者检测、数量对账（空目录=风控告警） |
| 增量发现 | baseline 记录历史 videoId 集，仅上报新视频；元数据写入 content 表（UNIQUE 去重） |
| 自动处理 | `auto_process` 开启时后台串行走完整管线（下载+Whisper 转写+AI 抽取+Obsidian） |
| 调度器 | 后台线程每分钟 tick，串行处理到期订阅，避免并发风控 |
| 前端 ⑥ 关注监控 | 加入关注/立即检查/暂停/恢复/改间隔/取消关注/视频列表/任务记录，60s 自动刷新 |

新增接口：

```text
POST /api/subscriptions              # 加入关注（解析+建订阅+首次抓取）
GET  /api/subscriptions              # 关注列表
GET  /api/subscriptions/{id}         # 详情
PATCH /api/subscriptions/{id}        # 暂停/恢复/改间隔/自动处理开关
DELETE /api/subscriptions/{id}       # 取消关注（软删除，保留历史资产）
POST /api/subscriptions/{id}/check   # 立即抓取
GET  /api/subscriptions/{id}/videos  # 该关注源已入库视频（含是否已转写）
GET  /api/crawl_tasks                # 抓取任务记录
POST /api/monitor/tick               # 手动触发一次调度检查
```

配置（`config.json` → `monitor` 段）：`enabled` / `max_pages` / `per_page` / `douyin_cookie`（可选，被风控时在浏览器登录态拷贝 cookie 填入即可）。

### 9. V0.3 博主画像 + 硬预测自动过（8-25）

| 模块 | 说明 |
|------|------|
| 置信度分桶 | `accuracy_by_confidence_band`：按 [0-30%, 30-60%, 60-80%, 80-100%] 分桶统计正确率（docs/08） |
| 领域/期限/标的 分桶 | subject type → 领域（宏观/指数/个股/市场…）；due_at−prediction_at → 短/中/长期；逐桶标注样本是否充足 |
| Brier 校准 | `calibration_score` = mean((outcome − confidence)²)，outcome 按 correct=1 / partial=0.5 / incorrect=0；<0.18 良好 / ≤0.25 一般 / >0.35 差 |
| 样本量门槛 | `verified_count < 30` 时输出警告"统计仅供参考"，分桶 n<30 标注"样本不足" |
| 校准散点 | 画像页 SVG 散点图：x=AI 置信度，y=实际结果（虚线=理想校准，点在线上方=过度自信），点数≥3 才绘制 |
| 硬预测自动过 | `auto_apply_eligible` 且 AI 判定 correct/incorrect 且 `ai_confidence ≥ 0.85`（可配）→ 系统代填 human_verdict 直接锁定，预测状态 → final |
| 24h 撤销窗口 | `auto_applied_at` 时间戳；`undo-auto-apply` 接口窗口内撤销退回人工复核，超窗锁定不可篡改 |
| 画像 API/前端 | `GET /api/creators/{id}` 返回画像 + 已验证预测明细（含校准点）；⑤ 创作者列表点「画像」展开 |

新增接口：

```text
GET  /api/creators/{id}                        # 博主画像（统计+预测明细）
POST /api/verification/{id}/undo-auto-apply    # 撤销硬预测自动过（24h 窗口）
```

配置（`config.json` → `verification` 段）：`auto_apply: true`（开关）、`auto_apply_min_confidence: 0.85`、`auto_apply_undo_hours: 24`。

### 10. 抖音免配置下载（8-26，参考 video-transcribe 技能包）

**问题**：抖音对匿名请求风控，`/api/ingest` 处理抖音链接时报「Fresh cookies needed」，无法下载也无法抓元数据。

**方案**：把 `video-transcribe` 的 playwright 自动生成 cookie 机制移植进 `douyin.py`，实现免手动配置 cookie。

| 改动 | 说明 |
|------|------|
| 三级 cookie 策略 | 手动配置 → 本地缓存（`data/cookies/`）→ playwright 驱动系统 Chrome/Edge 现场生成并缓存 |
| 元数据兜底 | 详情 API 被风控时，从页面 `og:title`（标题）、`data-e2e="user-info"`（作者名）、`data-e2e="detail-video-publish-time"`（发布时间）缓存兜底 |
| 下载重试 | yt-dlp 匿名失败后自动用生成 cookie 的 `--cookiefile` 重试 |
| 依赖 | `playwright` 已加进 requirements.txt（用系统 Chrome/Edge，无需下载自带浏览器） |

**实测**（抖音链接 `v.douyin.com/liL9OqKOhe0/`，博主「笨鸟怎么飞」黄金观点视频）：
- ✅ 自动生成 40 个 cookie
- ✅ 标题/作者/发布时间正确提取（详情 API 被风控）
- ✅ yt-dlp 无水印下载（24.28 MB）
- ✅ Whisper 转写（zh，1871 字）
- ✅ AI 抽取入库：1 条预测（黄金，conf 0.9，pending_review）+ 多条观点
- ✅ 全量回归 96 项通过

### 11. 异步 ingest 任务（8-26，解决长视频超时）

**问题**：`/api/ingest` 是同步阻塞接口，长视频（如 90MB / 12 分钟口播）转写+AI 耗时 15+ 分钟，HTTP 请求必然超时，用户误以为"链接不好使"。

**方案**：改为异步任务架构——提交即返回 `task_id`，后台线程处理，前端轮询进度，完成推通知。

| 改动 | 说明 |
|------|------|
| `POST /api/ingest` | 提交即返回 task_id + 初始状态（0.03s，不再阻塞） |
| `GET /api/tasks/{id}` | 查询任务：status / stage / progress / message / result / error |
| `GET /api/tasks` | 任务列表（倒序） |
| 内存任务表 | `_INGEST_TASKS` dict + 锁，含完整状态机 pending→running→success/error |
| 阶段进度 | parse→fetch_meta→transcript→download→transcribe→analyze→done，progress 0→1 |
| 完成通知 | 成功时推「视频处理完成」通知（category=auto_process，含标题/预测数） |
| 前端 | 表单提交后轮询进度（转写阶段 8s 间隔），阶段中文提示"转写中请耐心等待"，完成渲染结果 |
| 订阅自动处理 | `processVideo` 也改异步轮询 |

**实测**：
- ✅ 提交 90MB 长视频：立即返回 task_id（0.03s），进度 parse→download→transcribe 实时更新
- ✅ V0.5 测试 25/25：任务创建/状态机/成功路径（result+通知）/失败路径/任务列表
- ✅ 全量回归 121 项通过（smoke 16 + v02 15 + v03 43 + v04 22 + v05 25）

### 12. AI 简体化 + 文案落地（8-27）

**需求**：Whisper 转写直出为繁体，用户需要所有返回内容为简体，且要保存"AI 核对后的简体文案"和"AI 总结"两个文件。

**方案**：不装本地转换库，让 AI 负责简体校对（用户指定）。

| 改动 | 说明 |
|------|------|
| `TASK_SIMPLIFY` | 新增简体化 AI 任务：转写文本（繁体/错字）→ 简体校对版（转简体、修错字、按语义分段、存疑用〔〕标注） |
| 所有 AI prompt | 强制"输出使用简体中文"（总结/Claim/预测/验证），后续抽取都用简体文本输入 |
| `transcript` 表 | 新增 `text_full_simplified` 列存简体版 |
| ingest 流程 | 段2先调简体化 → 后续 AI 用简体文本 → 简体写库 |
| 文件落地 | 处理完成自动保存到 `data/transcripts/`：`{标题}_简体校对版.md` + `{标题}_AI总结.md` |
| 前端 | 完成结果页展示"简体校对版逐字稿"可折叠 + 已保存文件列表 |

**实测**（朱雀火箭视频转写）：
- 繁体 `就在那邊，朱雀3號遙二運載火箭正在落地，這次我們得到許可來見證...`
- 简体 `就在那边，朱雀3号遥二运载火箭正在落地。这次我们得到许可来见证...`
- ✅ 转简体 + 自动分段加标点 + 修错字
- ✅ V0.5 测试 27/27（含简体文案保存断言）
- ✅ 全量回归 123 项通过

### 13. V0.6 视频互动数据 + 前端（8-27）

**需求**：抓取视频的点赞/转发/评论/收藏数量，参考 douyin-creator-distill 前端的作品互动数据展示；并做了新前端工程。

| 改动 | 说明 |
|------|------|
| `content` 表 | 新增 `digg_count`/`comment_count`/`share_count`/`collect_count` 4 列（含 ALTER 迁移） |
| `ContentInfo` | 新增 4 个互动字段，抖音/B站 adapter 抓取并映射 |
| 抖音详情 API 兜底修复 | `_ensure_cookies` 之前只返回 Netscape cookie **文件**，httpx 用空 dict 仍被风控；现解析文件为 dict，详情 API 带真实 cookie 拿到完整 statistics |
| 存量回填 | 启动时 + 脚本把已有视频的互动数据回填（影视飓风火箭视频：赞 62.5万/评 7709/转 7.1万/藏 3.7万） |
| `/api/contents` | 返回互动字段 + 解析 raw_meta |
| 前端视频库 | `Library.vue`：视频卡片网格展示点赞/评论/转发/收藏 + 平台/搜索/筛选 |
| 前端工程 | Vue3+TDesign+ECharts 骨架、API client 封装、Dashboard/Predictions/Creators/Monitoring/Notifications/Library 六页全部接真实接口 |

**实测**：
- ✅ 抖音带 cookie 详情 API 返回真实 statistics（赞/评/转/藏）
- ✅ `/api/contents` 返回 3 条真实视频互动数据
- ✅ 全量回归 123 项通过

**功能流程建议**（参考 douyin-creator-distill）：
1. **爆款拆解**：基于互动数据（赞/评/转/藏）+ 转写文本，AI 分析 Hook/结构/情绪，输出爆款规律报告
2. **选题顾问**：从爆款报告生成带来源证据的选题建议
3. **博主智能体**：生成博主画像 + 审阅新稿（明确"非本人"）
4. **智能检索**：跨视频语义检索（Embedding），本地召回 + 模型精排
5. **互动趋势快照**：按天保存互动数据快照，观察增长趋势（参考账号经营）
6. **证据层审核**：按视频 ID 去重、审核抓取 JSON 的完整性，异常锁定转写

### 14. 本地转写增强 + doctor 诊断（8-27，参考 douyin-creator-distill）

**需求**：参考 douyin-creator-distill 的配置过程和本地 AI 转写功能，增强 creator-insight 的本地转写。

| 改动 | 说明 |
|------|------|
| `transcribe.py` 增强 | FFmpeg 提取 16kHz 单声道 wav → faster-whisper → 落盘 md/json/srt → 断点续转缓存 |
| 断点续转 | 同一媒体（路径+大小+时间）命中缓存 json 直接返回，秒级（实测 60.6s → 0.0s） |
| 产物落盘 | 正文 md / 逐段 json（含 text_full）/ 时间轴 srt 到 `data/transcripts/whisper/` |
| 配置增强 | `transcription` 段新增 `defaultProvider`/`whisper.language`/`whisper.retain_media`/`artifacts_dir` |
| `scripts/doctor.py` | 一键诊断：配置文件/AI key/FFmpeg/faster-whisper/playwright/yt-dlp/数据目录/后端可启动 |
| 文档 | `docs/17-配置与本地转写.md`：配置过程 + 本地转写链路 + 云端/Whisper 续接规划 |

**实测**：
- ✅ doctor 诊断 12 项全通过（AI key/ffmpeg/whisper/playwright/yt-dlp 全部就绪）
- ✅ 本地转写 60.6s（21MB 视频），产物 md/json/srt 齐全
- ✅ 断点续转命中缓存 0.0s
- ✅ 回归 smoke 16 + v05 27 通过

### 15. 批量抓取博主目录（8-28，登录态 Chrome）

**问题**：抖音主页目录 API 需要 `a_bogus` 签名 + 登录态，纯 httpx 必被风控返回空（诊断确认）。

**方案**：参考 douyin-creator-distill，用登录态 Chrome profile + Playwright 拦截目录 API。

| 改动 | 说明 |
|------|------|
| `_fetch_videos_via_browser` | `launch_persistent_context`（系统 Chrome channel）打开登录态 profile → 访问主页滚动加载 → 拦截 `aweme/post` 响应拿完整 aweme_list |
| `fetch_creator_videos` 回退 | httpx 目录 API 被风控返回空 → 自动回退浏览器抓取 |
| JSON 审核增强 | `_audit_catalog` 新增互动数据（赞/评/转/藏）完整度统计 + 警告 |
| 配置 | `monitor.profile_path`（登录态 Chrome user-data-dir） |
| 文档 | `docs/17-配置与本地转写.md` 新增批量抓取章节 |

**实测**（影视飓风）：
- ✅ httpx 失败 → 自动回退浏览器
- ✅ 抓取 **39 个作品**，每条含赞/评/转/藏（如"可回收火箭"赞129万/评28109/转11.2万）
- ✅ JSON 审核通过（去重/必填/作者/互动完整度）
- ✅ 调度器自动触发：`monitor_tick` 到点自动抓取，增量入库（39→57 作品，+18 新视频）
- ✅ 订阅昵称兜底：主页链接解析失败时用目录真实昵称修正（显示"影视飓风"）
- ✅ 回归 smoke 16 + v04 22 + v05 27 通过

### 16. 语义检索（8-28，参考 douyin-creator-distill 智能检索）

**需求**：加入 Qwen3-Embedding-0.6B 本地语义检索 + 配置页面 + 使用流程。

**实现**（复用已有 `app/services/semantic/` 包 + 扩展 content 索引）：
| 改动 | 说明 |
|------|------|
| 模型接入 | `semantic_search.model_root` 指向参考项目 `runtime/models/embedding`，复用 Qwen3-Embedding-0.6B（1024维/已安装） |
| 索引视频 | `index_contents()`：把视频「标题+互动+简体转写」分块 → 本地向量 → 存 `semantic_index`（target_type=content） |
| 检索 | `search()` 支持 content + prediction 两种 target，按相似度排序 |
| API | `/api/search`、`/api/search/index`、`/api/search/index/predictions`、`/api/semantic/settings`、`select`、`download` |
| 前端 | 视频库加**语义搜索**开关（关键词/语义）+ 语义检索设置页（Semantic.vue：模型选择/下载/建索引/说明） |
| 迁移 | transcript 表补 `text_full_simplified` 列 |

**实测**（Qwen3-Embedding-0.6B）：
- ✅ 索引 62 个视频、65 分块
- ✅ 搜「火箭」→ 精确召回影视飓风火箭/卫星视频（相似度 0.58/0.55/0.54）
- ✅ 搜「黄金」→ 召回老陈讲财经黄金分析 + 笨鸟黄金观点
- ✅ HTTP `/api/search` 正常
- ✅ 回归 smoke 16 + v05 27 通过

### 17. 摄取范围选项（8-28，单视频 / 博主全部）

**需求**：提交链接后，可选仅解析单个视频，或解析该博主全部视频。

| 改动 | 说明 |
|------|------|
| `IngestRequest.mode` | `single`（默认）/ `all` |
| `_process_one_video` | 提取单视频处理逻辑（字幕/转写/AI/Obsidian/保存文件）复用 |
| `_run_ingest_job_all` | mode=all：resolve_creator 解析博主 → fetch_creator_videos 批量抓目录 → JSON 审核 → 逐个处理，汇总成功/失败 |
| `api_ingest` | 传 mode 到后台线程 |
| 前端 | 内容摄入表单加「摄取范围」单选（仅该视频 / 该博主全部），all 模式有耗时提示 |

**实测**（影视飓风）：
- ✅ mode=all 提交 → 抓取 57 条视频目录 → 逐条处理（`[1/57] 处理《Ai可以取代我...》`）
- ✅ 单视频模式（默认）不受影响
- ✅ 回归 smoke 16 + v05 27 通过

### 18. 删除预测功能（8-28）

**需求**：预测列表可删除单条预测。

| 改动 | 说明 |
|------|------|
| `DELETE /api/predictions/{id}` | 删除预测本体 + 关联数据（verification/evidence/semantic_index/event_log）+ 解绑修订子预测 |
| 前端 | Predictions 页 operation 列加「删除」按钮（t-popconfirm 确认，提示会删除关联数据） |

**实测**：
- ✅ 删除返回 `{ok, deleted}`
- ✅ 预测本体 + 关联数据全清理（evidence/verification/semantic 均 0）
- ✅ 回归 smoke 16 + v03 43 通过

### 19. 预测抽取 P0 优化（8-29，意图门控 + 后处理过滤 + 全文抽取）

**需求**：预测抽取内容不准确（如"别幻想黄金暴涨"被误判为 event_no 预测）。

| 改动 | 说明 |
|------|------|
| `EXTRACT_PREDICTION_SYSTEM` | 重写为"意图分析器"：三步判断（是否预测未来/提取要素含推断/量化把握）+ 反例门控（告诫/反事实/修辞/历史回顾）+ few-shot 示例 |
| `_is_plausible_prediction` | 后处理硬过滤：告诫词（别幻想/别指望）→丢弃；模糊时间词（长期/最终/迟早）→丢弃；方向未知+无幅度+无时间 →丢弃 |
| `_with_offsets` | 全文抽取不截断（>30000 字取结尾段落），避免长视频漏抽后半段预测 |
| `NEGATION_WORDS`/`VAGUE_TIME_WORDS` | 告诫词表 + 泛泛时间词表 |

**实测**：
- ✅ P0 过滤 8/8（告诫句/劝阻句/反讽句/泛泛断言全过滤，真预测保留）
- ✅ 真实 AI：黄金告诫视频从"误判 event_no"→ 正确抽出 0 条
- ✅ 全量回归 131 项通过（smoke 16 + v02 15 + v03 43 + v04 22 + v05 27 + ai_evidence 8）

### 20. 预测置信度 P1-1 优化（8-29，AI 直接出置信度）

**需求**：让 AI 结合语境直接输出置信度（替代静态查表），静态表覆盖不全且不结合语境。

| 改动 | 说明 |
|------|------|
| `EXTRACT_PREDICTION_SYSTEM` | 增加 `confidence_score` 评估规则：语气词基准 + 否定/保留表达降档 + 具体性加分 + inferred 上限，明示"不要机械抄语气词" |
| `CONFIDENCE_DOWNGRADE_WORDS` | 否定/保留词表（不会/不可能/很难说/说不准） |
| `_apply_intent_heuristics` | 否定词命中 → 置信减半；direction=inferred → 置信上限 0.6 |
| 置信度优先级 | AI 输出 > 静态查表兜底（`_estimate_confidence` 仅当 AI 未给时用） |

**实测**：
- ✅ 否定词降置信（0.8→0.4）、推断方向上限（0.8→0.6）、组合（0.9→0.45）
- ✅ 回归 smoke 16 + v03 43 通过

### 21. 幅度结构化 P1-2（8-29，AI 漏填时规则提取）

**需求**：幅度字段常为空（AI 偷懒不填），需要结构化提取。

| 改动 | 说明 |
|------|------|
| `EXTRACT_PREDICTION_SYSTEM` | 幅度 unit 映射规则：ratio（涨超10%→0.10）/threshold（站上2450）/absolute/range（3500~3800）+ 至少/最多/左右处理 |
| `_normalize_magnitude` | 代码兜底：AI 漏填时从原话正则提取（涨跌幅/目标位/跌破/区间/翻倍），无数字不编造 |

**实测**：
- ✅ 涨超10%→ratio0.10、站上2450→threshold、跌破3.8%→threshold、3500~3800→range、翻倍→ratio1.0、无数字→null、AI已给→不动（7/7）
- ✅ 回归 smoke 16 + v03 43 通过

### 22. 时间解析 P2-1 + 博主画像 P2-2（8-29）

**P2-1 时间解析增强**：
| 改动 | 说明 |
|------|------|
| `PARSE_TIME_SYSTEM` | 相对时间推断规则：周一/明天/下周/下月/明年 → 相对推算；未来30天/X年内 → 加窗；本季度/下季度/Q3 → 对应时点。只有事件驱动（等政策落地）才标 fuzzy |
| **关键修复** | `parse_time` 之前是死代码从未被调用！`_insert_prediction` 现在真正调用它解析 `parsed_end`（此前所有时间都标 fuzzy → due_at=None） |

**P2-2 博主画像上下文**：
| 改动 | 说明 |
|------|------|
| `_creator_profile_context` | 按博主名查历史验证记录（verified/correct），拼进 prompt 校准置信度 |
| `extract_predictions(creator_name)` | ingest 时传博主名，有画像则追加"博主历史正确率"上下文 |

**实测**：
- ✅ 「未来30天」→ 2026-09-28、「明年」→ 2028-01-01、「周一」→ 最近周一、「一年内」→ +1年、「等政策落地后」→ 保持 fuzzy
- ✅ 回归 smoke 16 + v03 43 + v05 27 通过

### 23. V0.4 通知环节（8-26）

| 模块 | 说明 |
|------|------|
| `notification` 表 | 通知发送记录：channel（local/webhook/email）、category（new_videos/auto_process/auto_applied/manual）、status（sent/failed）、error、已读时间戳 |
| `notify.py` | 通知服务：local 站内写库；webhook 向 `notify.webhook_url` POST JSON；email 用标准库 smtplib（SSL/TLS）；未配置的通道明确 failed 且不影响其他通道 |
| 触发点 1 | 订阅抓取发现新视频 → `new_videos` 通知（标题含博主名与数量） |
| 触发点 2 | 自动处理（下载+转写+AI）成功/失败 → `auto_process` 通知（失败带原因） |
| 触发点 3 | 硬预测自动锁定 → `auto_applied` 通知（含 verdict/置信度/撤销时长） |
| 前端 | header 铃铛角标（unread）+ 下拉通知中心：最近 20 条、点击单条已读、全部已读、清空、发送测试通知；30s 自动刷新 |
| HTTP 验收 | 空列表 → 测试发送 → 入库 unread=1 → 全部已读 unread=0 → 清空，全通过 |

新增接口：

```text
GET    /api/notifications            # 通知列表（limit / unread_only）+ unread 统计
POST   /api/notifications/read       # 标记已读（传 id 单条 / 不传全部）
DELETE /api/notifications            # 清空通知记录
POST   /api/notifications/test       # 人工触发测试通知（验证各通道）
```

配置（`config.json` → `notify` 段）：`enabled` / `channels`（如 `["local","webhook"]`）/ `webhook_url` / `smtp`（host/port/user/password/from_name/to/use_ssl）。

---

## 二、已知限制 ⚠️（设计使然）

| 限制 | 说明 |
|------|------|
| **AI 生成证据只对历史预测有效** | AI 知识有截止日期；对知识范围外的近期预测，DeepSeek 诚实返回 inconclusive + 空证据（不编造）。近期预测需真实搜索（Tavily）或人工证据 |
| **Tavily 未配置** | 搜索框留空，验证近实时预测时需人工贴证据 |
| **硬预测自动过依赖 AI 置信度** | auto_apply 仅当 AI 置信 ≥0.85 且判定确定时生效；AI 置信本身是"软"的，校准散点图样本积累后可评估其可靠性 |

---

## 三、待完成 📋

| 优先级 | 事项 | 说明 |
|--------|------|------|
| 🟡 可选 | 配 Tavily key | 解锁真实新闻搜索（10 分钟注册，免费 1000 次/月） |
| 🔵 可选 | 配 Webhook / 邮件通道 | `config.json` → `notify` 段填 `channels` / `webhook_url` / `smtp` 即可启用 |
| 🔵 收尾 | 处理库里 2 条待确认预测 | 数据库还剩 2 条 pending_review |
| 🔵 收尾 | 画像页待样本积累 | 博主已验证样本不足 30 时画像仅作参考 |
| 🟢 进行中 | **V0.4 定时任务 UI 化** | 调度器已在后端运行；剩余：前端展示下次检查时间/最近抓取任务 |

### 路线图

| 版本 | 内容 |
|------|------|
| V0.3 | 博主统计画像（正确率/分桶/样本量门槛/置信度校准）、硬预测自动过 |
| V0.4 | 自动监控博主新视频 + 定时任务 + 通知 |
| V0.5 | 更多平台（快手/小红书/视频号）、多模型、多搜索源、插件体系 |

---

## 四、当前可做

服务运行在 **http://127.0.0.1:8781**（v0.2.0，已加载最新代码）。完整流程：转写 → AI 抽取 → 人工确认 → 到期验证（AI 生成证据兜底）→ 人工锁定 → 可信度统计，已端到端验证。

**下一步选择**：

- **a** — 处理库里 2 条待确认预测（清理历史数据）
- **b** — 开始 V0.3 博主可信度画像
- **c** — 先这样，今天收工
