# 11 · MVP 范围

## V0.1（必须 · 6-8 周）— 一次性输入 + 结构化沉淀

**目标**：手动输入一个视频 → 转写 → AI 总结 → Claims → Predictions → SQLite → Obsidian Markdown

| 必须做 | 可以不做 |
|--------|---------|
| ✅ 抖音 + B 站 Adapter（含分享文案解析） | ❌ 其他平台 |
| ✅ 手动 URL + 分享文案入口 | ❌ 自动监控 |
| ✅ 优先原生字幕 + Whisper 本地兜底 | ❌ 云端 ASR |
| ✅ AI 总结 + Claim 抽取 + Prediction 抽取（严格：标的+方向+时间齐备） | ❌ 时间模糊预测的容错 |
| ✅ Baseline Snapshot（Prediction 入库时立即拉"预测时点相关证据"） | ❌ 自动 Evidence Collector（V0.2） |
| ✅ SQLite 8 张表（含 event_log） | ❌ creator_reliability 实时重算 |
| ✅ 极简 UI：粘链接 → 转写 → 提取 → 列表查看 | ❌ 待复核队列 / Analytics |
| ✅ Obsidian Markdown 写出（Verification + 关键 Prediction 摘要） | ❌ 反向双向同步 |

**V0.1 验收标准**：
- 粘一个抖音链接 → 5 分钟内产出 Transcript + 3-5 条 Claim + 1-2 条 Prediction
- Obsidian Vault 中出现对应 Markdown 文件，人工区可编辑
- Prediction 入库时生成 Baseline Snapshot

## V0.2（必须 · 3-4 周）— 到期 + 验证

**目标**：Prediction 到期检测 → Evidence 收集 → AI 验证 → 人工确认 → 写回

| 必须做 |
|--------|
| ✅ Scheduler（每日扫 due prediction） |
| ✅ Evidence Collector（AI 搜索 + 财经 API 至少各一） |
| ✅ Verification Engine（按 07 文档流程） |
| ✅ Verification Queue UI |
| ✅ 人工复核（每条必过，100%） |
| ✅ Obsidian Verification 报告 |

## V0.3（可选 · 推荐 · 4-6 周）— 统计与画像

| 必须做 |
|--------|
| ✅ Creator Reliability 重算（on demand） |
| ✅ Analytics 页面 |
| ✅ Dataview 推荐查询（写入 Obsidian） |
| ✅ 置信度校准（前提：样本 ≥ 30） |
| ✅ 硬预测自动过（auto_apply_eligible=1）+ 24h 撤销窗口 |

## V0.4（可选）— 自动监控

- 关注博主 → 新视频自动处理 → 定时任务 → 通知（邮件/微信/webhook）
- 季度趋势统计

## V0.5（远期）— 生态扩展

- 更多平台（快手/小红书/视频号/YouTube）
- 更多 AI（Claude/Gemini/本地模型）
- 更多搜索源（Perplexity/SerpAPI）
- 更多知识库（Notion/Logseq）
- 插件体系

## 明确不做（任何时候）

- 公开分享/多人协作（个人工具）
- AI 投资建议（只做验证，不做推荐）
- 全自动验证且不可撤销（永远留人工后门）
- 绕过平台验证码/反爬（合规底线）
