# creator-insight · 代码维基（Code Wiki）

> 面向开发者/维护者的**代码层面**文档，与 `docs/` 下的产品规格互补。
> 产品设计、需求决策、Prediction/Evidence Schema 详见根目录 `docs/` 各规格文档；本文档聚焦**已落地代码的架构、模块、函数、依赖和运行方式**。

- 语言：Python 3.13（后端，FastAPI）+ Vue 3 + TypeScript + Vite + TDesign（前端）
- 存储：SQLite（单文件，WAL 模式），可选写 Obsidian 库
- 默认服务：`http://127.0.0.1:8781`
- 当前版本：`0.6.0`（`app/main.py` `FastAPI(version="0.6.0")` 与 `api_status`）

---

## 一句话架构

一个**本地优先、可证据溯源、按"预测生命周期"运转的博主可信度追踪系统**：
粘贴视频链接 / 分享文案 → 平台 Adapter 抓取 → 字幕/Whisper 转写 → AI 解构（总结 / 观点 / 预测）→ 人工确认 → 到期采集证据 + AI 初判 → 人工/自动锁定 → 博主可信度统计与 Obsidian 沉淀 → 语义检索。

核心数据链路：

```
Creator → Content → Transcript → Claim
                    └─────────→ Prediction → Evidence → Verification → Creator Reliability
```

---

## 文档索引

| 文档 | 内容 | 对应代码 |
|------|------|---------|
| [01-architecture.md](01-architecture.md) | 整体架构分层、核心数据链路、运行时时序 | `app/` |
| [02-module-map.md](02-module-map.md) | 目录结构全景 + 模块职责一览 | 全仓库 |
| [03-backend-core.md](03-backend-core.md) | 配置、数据库、领域模型、FastAPI 入口、Adapter 注册表、AI 提供方 | `app/config.py` `app/db.py` `app/models.py` `app/main.py` `app/adapters/*` `app/ai/*` |
| [04-backend-services.md](04-backend-services.md) | 服务层：抽取管线、转写、验证引擎、Obsidian、监控、通知、头像、语义检索 | `app/services/*` |
| [05-frontend.md](05-frontend.md) | 前端工程、页面、API client | `src/*` |
| [06-data-model.md](06-data-model.md) | SQLite 全表结构、ER 关系、预测状态机 | `app/db.py` `app/models.py` |
| [07-api-reference.md](07-api-reference.md) | REST API 端点清单（含请求/响应要点） | `app/main.py` `app/api_semantic.py` |
| [08-dependencies.md](08-dependencies.md) | 第三方依赖、`config.json` 配置项、内外依赖关系 | `requirements.txt` `config/*` `package.json` |
| [09-run-and-test.md](09-run-and-test.md) | 安装、配置、启动（后端/前端）、测试脚本、常见排坑 | `start.bat` `scripts/*` |

---

## 技术栈速览

| 层 | 技术 |
|----|------|
| 后端框架 | FastAPI + Uvicorn（同步逻辑用 `asyncio.to_thread` 包裹） |
| ORM/Schema | Pydantic v2（`models.py`），SQLite 原生 `sqlite3` 手写 SQL |
| AI | OpenAI 兼容云端（DeepSeek / NVIDIA NIM 系）+ Ollama 本地兜底，由 `AIGateway` 路由 |
| 视频下载/cli | `yt-dlp`；抖音免配置 cookie 用 `playwright` 驱动系统 Chrome/Edge |
| 转写 | `faster-whisper`（本地兜底）+ FFmpeg 提音频 |
| 语义检索 | `sentence-transformers` 本地向量化，模型经 ModelScope / HuggingFace 下载 |
| 前端 | Vue 3（`<script setup>`）+ TypeScript + Vite + TDesign Vue Next + ECharts |
| 存储 | SQLite（`data/creator_insight.db`）+ Obsidian Markdown（可选） |

---

## 快速浏览代码入口

- **HTTP 入口**：[app/main.py](../app/main.py)（全部 `/api/*` 路由）+ [app/api_semantic.py](../app/api_semantic.py)
- **建表/迁移**：[app/db.py](../app/db.py)
- **核心业务（验证闭环/画像）**：[app/services/verification.py](../app/services/verification.py)
- **抽取管线**：[app/services/pipeline.py](../app/services/pipeline.py)
- **平台适配**：[app/adapters/](../app/adapters/)
- **前端 API 封装**：[src/api/client.ts](../../src/api/client.ts)