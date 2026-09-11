# 01 · 整体架构与数据流

## 分层架构

### Mermaid 架构总览

```mermaid
flowchart TD
    UI["UI（Vue3 + TDesign）<br/>Dashboard · Predictions · Creators<br/>Monitoring · Notifications · Library · Semantic"]

    API["FastAPI 入口 app/main.py (+ api_semantic.py)<br/>· 异步 ingest 任务中心（内存任务表 + 后台线程 + 前端轮询）<br/>· 每个 /api 端点薄封装<br/>· 启动时 init_db + 两个后台调度循环"]

    UI -- "HTTP (/api) · Vite dev 走 /api 代理<br/>数据永远来自 SQLite" --> API

    subgraph ADAPT["适配层（只负责获取）"]
        A["PlatformAdapter<br/>douyin / bilibili"]
        DLP["yt-dlp / playwright<br/>（下载 / cookie / 反爬）"]
    end

    subgraph AILAYER["AI 层"]
        AI["AIProvider<br/>DeepSeek / Ollama（AIGateway 路由）"]
        PR["prompts.py<br/>6 组任务 Prompt"]
    end

    subgraph SVC["服务层（核心业务）"]
        S["Services<br/>pipeline · verification · transcribe<br/>creator_monitor · notify · obsidian"]
    end

    SEM["SemanticService<br/>建索引 / 检索 / embedder"]

    API --> A
    API --> AI
    API --> S
    API --> SEM
    A --> DLP
    AI --> PR
    AI -->|"输出结构化 JSON"| S

    subgraph DATA["数据层"]
        DB[("SQLite data/creator_insight.db<br/>WAL + busy_timeout")]
        OBS["ObsidianAdapter"]
        MED["data/（媒体 / 转写 / 头像 / 模型）"]
    end

    S --> DB
    SEM --> DB
    DB --> OBS
    DB --> MED
```

> 下方保留一份 ASCII 版供无法渲染 Mermaid 的编辑器使用。

```
┌──────────────────────────────────────────────────────────────────┐
│  UI（Frontend：Vue3 + TDesign）                                    │
│  Dashboard · Predictions · Creators · Monitoring ·               │
│  Notifications · Library · Semantic                              │
│  ── 只与 FastAPI /api 交互，数据永远来自 SQLite（不缓存 Markdown）   │
└──────────────────────────────┼───────────────────────────────────┘
                               │ HTTP (/api) · Vite dev 走 /api 代理
                               ▼
┌──────────────────────────────────────────────────────────────────┐
│  FastAPI 入口  app/main.py (+ app/api_semantic.py)                │
│  · 异步 ingest 任务中心（内存任务表 + 后台线程 + 前端轮询）            │
│  · 每个 /api 端点薄封装：参数 Pydantic 校验 → 调 Service → 返回 dict   │
│  · 启动时：init_db 建表/迁移、启动两个后台调度循环                    │
└───────┬───────────────┬───────────────────┬───────────────────┬───┘
        │               │                   │                   │
        ▼               ▼                   ▼                   ▼
┌───────────────┐ ┌──────────────┐ ┌─────────────────┐ ┌────────────────┐
│ PlatformAdapter│ │ AIProvider    │ │  Services 层    │ │  SemanticService│
│ douyin/bilibili│ │ DeepSeek /   │ │ pipeline│verify │ │ 建索引/搜索      │
│ 只负责"拿数据"  │ │ Ollama(AIGate│ │ transcribe│...  │ │ embedder        │
└───────┬───────┘ │ way 路由)     │ └───────┬─────────┘ └───────┬────────┘
        │         └───────┴───────┘         │                   │
        │            ▲ 其输出做结构化 JSON    │                   │
        ▼            │                      ▼                   ▼
  ┌─────────┐  ┌─────┴───────────┐  ┌──────────────────────────────┐
  │ yt-dlp  │  │ prompts.py      │  │  SQLite data/creator_insight.db│
  │playwright│ │ 6 组任务 Prompt │  │  WAL + busy_timeout           │
  └─────────┘  └─────────────────┘  └───────┬──────────────┬────────┘
                                            │              │
                                            ▼              ▼
                                     ObsidianAdapter   data/（媒体/转写/头像/模型）
```

## 设计原则（代码中的体现）

1. **Adapter 只问"我能拿到什么"**：`PlatformAdapter` 抽象只定义"获取"，反爬/cookie 全藏在实现类内部，不触碰 DB/AI/验证（见 [02-module-map.md](02-module-map.md)）。
2. **AI / 证据 / 验证之间只传数据，不传对象**：`pipeline` 产出的 Prediction 以 JSON/dict 落库；`verification` 从 DB 重新读取，再拼 JSON 给 AI，保证可审计。
3. **AI 调用期间绝不持有写事务**：`ingest_pipeline` 拆"段1 基础数据快速 commit → 段2 AI（无事务）→ 段3 新连接短事务"三段，规避 `database is locked`（[03-backend-core.md](03-backend-core.md)）。
4. **所有状态变更写 Event Log**：`log_event` 记录 `prediction/evidence/verification/content` 的关键事件，可回放（[03-backend-core.md](03-backend-core.md)）。
5. **长任务"提交即返回 + 后台线程 + 前端轮询"**：ingest 与模型下载都采用该模式，避免 HTTP 超时。

## 核心运行时时序

### 1) 内容摄入（单视频 / 博主全部）

```mermaid
sequenceDiagram
    participant U as 前端
    participant API as FastAPI main.py<br/>(异步任务中心)
    participant AD as PlatformAdapter
    participant DLP as yt-dlp / playwright
    participant WS as Whisper 转写
    participant P as ingest_pipeline
    participant O as Obsidian

    U->>API: POST /api/ingest
    API-->>U: task_id（≈0.03s 立即返回）
    Note over API,P: 后台线程 _run_ingest_job
    API->>API: guess_platform(raw) → get_adapter(platform)
    alt mode = single
        API->>AD: parse_input → fetch_content_meta
    else mode = all
        API->>AD: resolve_creator → fetch_creator_videos → _audit_catalog
    end
    Note over AD,P: _process_one_video
    AD->>DLP: fetch_transcript（优先平台字幕）
    alt 无字幕 且 use_whisper
        AD->>DLP: download_media
        DLP->>WS: whisper_transcribe
        WS-->>P: transcript_text
    end
    AD->>P: ingest_pipeline(content, transcript)
    P->>O: write_prediction（可选）
    Note over API: _save_transcript_files（简体版 + AI 总结 → data/transcripts）
    Note over API: 成功 → status=success + 推送通知
    U-->>API: GET /api/tasks/{id} 轮询进度
```

### 2) 抽取管线（`ingest_pipeline`，三段式）

```mermaid
sequenceDiagram
    participant P as ingest_pipeline
    participant DB as SQLite
    participant AI as AIProvider<br/>(DeepSeek / Ollama)

    rect rgb(220,240,255)
    Note over P,DB: 段1 基础数据（快速提交 → 释放写锁）
    P->>DB: _upsert_creator / _upsert_content / _upsert_transcript
    P->>DB: commit（立即释放写锁）
    end

    rect rgb(235,255,235)
    Note over P,AI: 段2 AI 抽取（无事务）
    P->>AI: simplify_transcript（繁体→简体）
    P->>AI: summarize_transcript
    P->>AI: extract_claims
    P->>AI: extract_predictions
    Note over P: 每次 AI 调用间 sleep 3s 限流缓冲；<br/>失败逐个 try/except 兜底，不中断整体
    end

    rect rgb(255,245,225)
    Note over P,DB: 段3 新连接短事务
    P->>DB: 更新 transcript.text_full_simplified
    P->>DB: _insert_claims
    P->>DB: _insert_prediction × N（时间解析 +<br/>compute_auto_apply + _ensure_baseline 快照）
    P->>DB: commit
    end
```

### 3) 验证闭环（`services/verification.py` 为核心）

```mermaid
sequenceDiagram
    participant H as 人工 / 前端
    participant V as VerificationEngine
    participant DB as SQLite
    participant AI as AIProvider
    participant TV as Tavily
    participant YF as yfinance

    H->>V: review_prediction(active, due_at)
    V->>DB: prediction.status = active

    Note over V,DB: scheduler 周期扫描 / POST /api/scheduler/run
    V->>DB: scan_due_predictions → status = due

    H->>V: run_verification(id)
    V->>DB: status = verifying
    V->>DB: collect_evidence：baseline 快照
    V->>TV: Tavily 正/反检索 + 去重
    V->>YF: 行情（可选）
    Note over V: 拆分 post_time / prediction_time 证据（强制隔离）
    V->>AI: AI 判读（VERIFY_SYSTEM，仅用 post_time；<br/>无外部证据时 AI 生成证据链 credibility=0.4）
    V->>DB: 写 verification 表
    alt 硬预测自动过
        Note over V: _maybe_auto_apply（auto_apply_eligible +<br/>确定判定 + AI 置信 ≥ 0.85）
        V->>DB: status = final（locked=1）
        V->>H: _notify_auto_applied 通知
    else
        V->>DB: status = human_review
    end
    H->>V: submit_human_review(五种 verdict, locked)
    Note over V: 锁定后 recompute_creator_reliability → 刷新 creator_reliability
    V->>DB: Obsidian 写验证报告（可选）
```

### 4) 自动监控 + 通知（启动即运行的后台循环）

```mermaid
sequenceDiagram
    participant M as creator_monitor
    participant AD as PlatformAdapter<br/>(目录抓取)
    participant DB as SQLite
    participant P as ingest_pipeline
    participant N as notify

    Note over M: app 启动：init_db → backfill_missing_baselines<br/>→ _backfill_content_stats
    Note over M: _scheduler_loop（验证到期扫描，每 scheduler_interval_sec）
    Note over M: start_monitor（每分钟 tick 串行处理到期订阅）

    loop 每分钟 tick（串行，避免并发风控）
        M->>AD: 抓主页目录
        AD->>M: 目录 JSON
        M->>DB: _audit_catalog（JSON 审核）→ 增量发现新视频
        M->>DB: 新视频入库
        M->>N: 新视频通知
        alt auto_process 开启
            M->>P: 自动转写管线
            P->>N: 成功 / 失败通知
        end
    end
```

## 数据在模块间的流动方向

```mermaid
flowchart LR
    AD["PlatformAdapter"] -->|content / transcript| DB[("SQLite")]
    TS["TranscriptService"] -->|FFmpeg + faster-whisper| DB
    AIP["AIProvider（云端 / 本地模型）"] --> DB
    TV["Tavily"] --> ES["EvidenceService"] --> DB
    ES -->|可选行情| YF["yfinance"]
    VE["VerificationEngine"] --> DB
    VE --> AIP
    OBS["ObsidianAdapter"] --> DB
    OBS -->|"frontmatter + AUTO/HUMAN 双层"| VT["Obsidian Vault"]
    SS["SemanticService"] --> EMB["embedder"] -->|semantic_index| DB
```