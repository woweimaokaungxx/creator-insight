# 03 · 系统架构

## 架构总览

```
┌─────────────────────────────────────────────────────────┐
│  UI（本地 Web / 浏览器 / localhost）                       │
│  Dashboard · Creators · Videos · Predictions · ...        │
└───────────────────────┬─────────────────────────────────┘
                        │ HTTP / SSE（任务流）
                        ▼
┌─────────────────────────────────────────────────────────┐
│  Scheduler（调度器·V0.2 引入）                            │
│  · 每日扫"due_prediction"                                │
│  · 每小时扫新视频（V0.4）                                 │
└───────────────────────┬─────────────────────────────────┘
                        │
   ┌─────────┬──────────┼─────────┬─────────┬──────────┐
   ▼         ▼          ▼         ▼         ▼          ▼
┌──────┐ ┌────────┐ ┌──────┐ ┌──────┐ ┌────────┐ ┌──────┐
│Plat- │ │Transc- │ │  AI  │ │Search│ │Evidenc │ │Verif │
│form  │ │ript    │ │Provi-│ │Provi-│ │e       │ │ication│
│Adapt-│ │Service │ │der   │ │der   │ │Service │ │Engine│
│er    │ │        │ │      │ │      │ │        │ │      │
└──┬───┘ └───┬────┘ └──┬───┘ └──┬───┘ └────┬───┘ └──┬───┘
   │         │         │        │          │        │
   └─────────┴─────────┴────────┴──────────┴────────┘
                         │
                         ▼
              ┌──────────────────────┐
              │  SQLite（主存储）      │
              │  + Documents/（备份）│
              └──────────┬───────────┘
                         │
                         ▼
              ┌──────────────────────┐
              │  KnowledgeBaseAdapt  │
              │  · ObsidianAdapter   │
              │  · 未来 NotionAdapter │
              └──────────────────────┘
```

## 十大模块职责

| # | 模块 | 职责 | 关键约束 |
|---|------|------|---------|
| 1 | **PlatformAdapter** | 获取 Creator / Content / Metadata / Transcript / 媒体 | 只负责"能拿到什么"，不关心"怎么拿"；反爬逻辑藏在内部 |
| 2 | **TranscriptService** | 字幕获取 + Whisper 兜底转写 | 原始逐字稿不可被覆盖；带时间戳分段 |
| 3 | **AIProvider** | summarize / extract_claim / extract_prediction / parse_time / verify_prediction | 可插拔；每条输出记录实际模型版本 |
| 4 | **SearchProvider** | 联网搜索（Tavily/Bing/SerpAPI） | 与 AIProvider 解耦；query 生成 vs 执行分离 |
| 5 | **EvidenceService** | 证据收集、去重、时间戳校验、来源可信度标注 | 强制 published_at/collected_at 双时间戳 |
| 6 | **VerificationEngine** | AI 初步判定 → 人工确认 → final 锁定 | 只用 post-time 证据判定；推理链必须留档 |
| 7 | **Scheduler** | 到期预测扫描、新视频检查 | V0.2 引入；进程内 APScheduler |
| 8 | **SQLite** | 主存储（9 张表） | 事件日志可回放 |
| 9 | **KnowledgeBaseAdapter** | Obsidian Markdown 写入 | frontmatter + auto/human 双层标记 |
| 10 | **UI** | Dashboard / Creators / Videos / Predictions / Queue / Analytics / Settings | 从 SQLite 读实时数据，不缓存 Markdown |

## 五条硬约束

1. **Adapter 接口只关心"我能拿到什么"，不关心"怎么拿"** —— 平台反爬逻辑藏在 adapter 内部
2. **AI / Search / Evidence / Verification 四者之间只能传数据，不能传对象引用** —— 保证可审计
3. **Evidence 必须有时间戳，Verification Engine 强制隔离 prediction-time / post-time 信息**
4. **UI 永远从 SQLite 读实时数据，不缓存 Markdown**
5. **所有状态变更（status、verdict、prediction_revision）必须写入 Event Log，可回放**

## 技术依赖方向

```
PlatformAdapter ──→ SQLite
TranscriptService ──→ SQLite + FFmpeg
AIProvider ──→ (API/本地模型) ──→ SQLite
SearchProvider ──→ (外部搜索API) ──→ EvidenceService ──→ SQLite
EvidenceService ──→ MarketDataProvider (akshare/yfinance/FRED)
VerificationEngine ──→ SQLite + AIProvider
KnowledgeBaseAdapter ──→ SQLite ──→ Obsidian Vault
```

## 部署形态

- V0.1-V0.4：本地 Web App，`python main.py` + 浏览器访问 localhost
- V0.5+：可选 Docker / NAS / 云服务器，架构不变
