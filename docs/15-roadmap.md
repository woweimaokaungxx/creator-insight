# 15 · 路线图

## 版本时间线

| 版本 | 主题 | 时长 | 核心交付 |
|------|------|------|---------|
| **V0.1** | 单次输入 → 结构化沉淀 | 6-8 周 | 抖音+B站 Adapter、转写、AI 抽取、SQLite、Obsidian 写出、Baseline Snapshot |
| **V0.2** | 到期 → 验证 | 3-4 周 | Scheduler、Evidence Collector、Verification Engine、人工复核队列 |
| **V0.3** | 统计与画像 | 4-6 周 | Creator Reliability、Analytics、置信度校准、硬预测自动过 |
| **V0.4** | 自动监控 | 4-6 周 | 关注博主、新视频自动处理、通知 |
| **V0.5** | 生态扩展 | 持续 | 多平台、多 AI、多搜索源、多知识库、插件体系 |

## V0.1 里程碑拆分（6-8 周）

| 周 | 里程碑 |
|----|--------|
| 1 | 项目脚手架（FastAPI + SQLite + config 体系） |
| 2 | PlatformAdapter 接口 + 抖音 Adapter（含分享文案解析） |
| 3 | B 站 Adapter + TranscriptService（字幕 + Whisper 兜底） |
| 4 | AIProvider（DeepSeek-V3）+ 总结/Claim/预测抽取 Prompt |
| 5 | Prediction Schema 落地 + Baseline Snapshot |
| 6 | 极简 UI（粘链接 → 转写 → 提取 → 列表） |
| 7 | ObsidianAdapter（frontmatter + auto/human 双层） |
| 8 | 联调 + 验收（20 条真实视频测试） |

## 关键里程碑决策点

| 决策点 | 触发条件 | 决策内容 |
|--------|---------|---------|
| 是否进入 V0.2 | V0.1 验收通过 + 已有 ≥ 20 条真实 Prediction | 启动 Scheduler 与验证流程 |
| 是否进入 V0.3 | V0.2 后已有 ≥ 30 条 final verdict | 做统计与画像 |
| 置信度校准启用 | 某置信度桶样本 ≥ 30 | 显示 Brier Score |
| 硬预测自动过 | V0.3 上线 + 用户信任度提升 | 打开 auto_apply_eligible |
| 多平台扩展 | V0.4 稳定运行 2 个月 | 按用户需求优先级加平台 |

## 里程碑验收标准

### V0.1 验收
- [ ] 粘贴抖音链接 → 5 分钟内产出 Transcript + Claims + Predictions
- [ ] Obsidian Vault 出现对应 Markdown，人工区可编辑
- [ ] Baseline Snapshot 随 Prediction 入库生成
- [ ] SQLite 9 张表结构稳定

### V0.2 验收
- [ ] 到期预测自动进入队列
- [ ] Evidence 收集含正/反两面 + 结构化数据
- [ ] 人工复核 30 秒内可完成一条
- [ ] final_verdict 锁定后不可篡改

### V0.3 验收（2026-08-25 完成 ✅）
- [x] 博主画像正确显示（含样本量）—— `GET /api/creators/{id}` + 前端「画像」面板
- [x] 硬预测自动过 + 24h 撤销窗口—— `auto_apply`（AI 置信 ≥0.85 自动锁定）+ `undo-auto-apply`
- [x] 置信度校准散点图（样本够时）—— 前端 SVG，校准点 ≥3 时绘制，虚线=理想校准

## 不做的时间线承诺

- 不承诺"3 个月全功能"——按里程碑推进，每步可交付
- 不承诺"自动监控从第一天就有"——V0.4 才做
- 不承诺"全自动验证"——人工复核永远保留后门
