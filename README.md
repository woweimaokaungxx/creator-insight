# creator-insight

**博主观点、预测、事实验证与可信度追踪系统**

> 本地优先的个人软件。把视频里的"嘴上预测"转化为可验证、可统计、可复盘的结构化数据，最终用 Obsidian 沉淀为个人决策辅助知识库。

## 一句话定位

一个**本地优先、可证据溯源、按预测生命周期运转的博主可信度追踪系统**——解析、转写、AI 解构博主内容，提取可证伪预测，到期后联网/API 收集证据验证，人工确认后统计博主历史正确率与置信度校准，并同步写入 Obsidian 知识库。

## 核心数据链路

```
Creator → Content → Transcript → Claim → Prediction → Evidence → Verification → Creator Reliability
```

## 已确认的产品决策（2026-08-23）

| # | 决策 | 结论 |
|---|------|------|
| Q1 | 产品目标 | 混合：投资决策辅助为主，博主研究 + 知识沉淀为辅 |
| Q2 | 第一阶段平台 | 抖音 + B 站 |
| Q3 | 博主类型 | 财经 + 科技 + 职场科普 |
| Q4 | 视频输入 | 手动粘贴 URL 或分享文案 |
| Q5 | Transcript | 原生字幕优先 + Whisper 本地兜底 |
| Q6 | AI 模型 | 混合：云端为主（DeepSeek-V3 / Claude），本地 Qwen2.5-7B 兜底 |
| Q7 | Prediction 门槛 | 时间 + 标的 + 方向 三者齐备才算预测 |
| Q8 | 证据来源 | AI 搜索 + 结构化财经 API（akshare/yfinance/FRED）混用 |
| Q9 | 正确率口径 | 先按基础命中率（correct + partial×0.5），未来升级 Brier Score |
| Q10 | 人工复核 | 所有预测统一进"到期队列"，到期后 AI 初步验证 + V0.1 100% 人工确认；auto_apply 预埋，V0.3 打开硬预测自动过 |
| Q11 | Obsidian 范围 | 只写 Verification 报告 + 关键 Prediction 摘要 |
| Q12 | Obsidian 防覆盖 | frontmatter + auto/human 双层标记 |
| Q13 | 数据存储 | SQLite 主存 + Markdown 视图 |
| Q14 | 软件形态 | 本地 Web App（Python FastAPI + 浏览器 UI） |
| Q15 | 规模 | 1 人 / 20 博主 / 200 视频每月 |

## 文档索引

| 文档 | 内容 |
|------|------|
| [docs/01-product-positioning.md](docs/01-product-positioning.md) | 产品定位与核心用户场景 |
| [docs/02-data-model.md](docs/02-data-model.md) | 核心数据模型 |
| [docs/03-architecture.md](docs/03-architecture.md) | 系统架构 |
| [docs/04-sqlite-schema.sql](docs/04-sqlite-schema.sql) | SQLite 表设计（DDL） |
| [docs/05-prediction-schema.md](docs/05-prediction-schema.md) | Prediction Schema |
| [docs/06-evidence-schema.md](docs/06-evidence-schema.md) | Evidence Schema |
| [docs/07-verification-engine.md](docs/07-verification-engine.md) | Verification Engine |
| [docs/08-accuracy-system.md](docs/08-accuracy-system.md) | Accuracy 统计系统 |
| [docs/09-obsidian-format.md](docs/09-obsidian-format.md) | Obsidian Markdown 结构 |
| [docs/10-ui-ia.md](docs/10-ui-ia.md) | UI 信息架构 |
| [docs/11-mvp-scope.md](docs/11-mvp-scope.md) | MVP 范围 |
| [docs/12-tech-stack.md](docs/12-tech-stack.md) | 推荐技术栈 |
| [docs/13-module-boundaries.md](docs/13-module-boundaries.md) | 模块边界 |
| [docs/14-risk-register.md](docs/14-risk-register.md) | 风险清单 |
| [docs/15-roadmap.md](docs/15-roadmap.md) | 路线图 |
| [docs/16-case-template.md](docs/16-case-template.md) | 真实案例模板（待用户填写） |

## 状态

- 阶段：**V0.2 验证闭环已完成**
- 当前链路：`粘贴链接 → 转写/抽取 → 人工确认 → 到期队列 → 证据 → AI 初判 → 人工锁定`
- 默认服务：`http://127.0.0.1:8781`
- 没有 Tavily key 时，仍可在验证队列中手工添加带发布时间的证据
