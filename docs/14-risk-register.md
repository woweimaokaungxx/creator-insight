# 14 · 风险清单

## 🟥 P0（必处理 · 上线前）

| # | 风险 | 应对 |
|---|------|------|
| 1 | 平台反爬 / 登录态失效 | Adapter 内置降级路径（公开接口 + 第三方解析 + Whisper），登录失效立刻暂停 |
| 2 | 字幕缺失 | Whisper 本地兜底（V0.1 已设计） |
| 3 | OCR/ASR 错误（"加薪"误识为"降息"） | Transcript 显示时间戳，Verification 阶段必须能"回听"（media_path 字段） |
| 4 | AI 幻觉（虚构证据） | Verification 必须 ai_evidence_ids 强制引用 evidence.id，禁止空指 |
| 5 | 证据时间污染（事后诸葛亮） | 07 文档三道闸：published_at 强制分层 + baseline 快照 + 推理链留档 |
| 6 | 预测模糊表述 | confidence_raw 留原文 + time_window.is_fuzzy 标人工 |
| 7 | 博主修订表述 | prediction_revision 链 + 永不修改 raw_text |

## 🟧 P1（上 V0.3 前处理）

| # | 风险 | 应对 |
|---|------|------|
| 8 | 样本量不足导致统计失真 | n<30 不显示百分比；明确"样本不足" |
| 9 | 选择性记忆（博主只讲成功预测） | 系统侧保留失败的 Prediction 即使博主视频后来不提——事件日志可回放 |
| 10 | 证据冲突（多家媒体说法不同） | Verification Engine 必须把矛盾写进 ai_reasoning_json，由人工裁定 |
| 11 | 标的多义性（上证指数 vs 上证 50） | subject.symbol + name 双字段，AI 抽取后人工在 pending_review 时确认 |
| 12 | 手动修改 Obsidian 区域被覆盖 | frontmatter + auto/human 双层标记 |
| 13 | 负面证据缺失（搜不到"没发生"） | EvidenceCollector 强制反方向 query + absent_evidence 概念（06 文档） |
| 14 | API 成本失控 | AI 调用加每日上限与告警（V0.12 已落地 `ai.daily_call_limit` / `daily_call_warn`） |
| 20 | **任务状态丢失**（服务重启后 `running` / `pending` 任务全部消失，前端轮询 404，用户重复提交并重复消耗 AI 额度） | 任务持久化 + 启动时 `running → queued` 断点恢复；见 `docs/23` §5、`docs/adr/0001` |
| 21 | **重试放大成本与风控**（失败无分类、无上限地重试，会重复烧 AI 额度并触发平台 403/429） | 错误三分类 + 单条重试上限 3 次 + 退避；`needs_action`（登录失效/验证码/限流/配额）**立即停止不绕过**；见 `docs/23` §1/§3/§7 |

## 🟨 P2（V0.4+ 考虑）

| # | 风险 | 应对 |
|---|------|------|
| 15 | 法律 / 平台 ToS | 仅个人使用 + 不公开发布 + 不绕过验证 |
| 16 | 模型升级行为变更 | 所有 AI 输出连同模型版本 hash 入档 |
| 17 | 博主内容被平台下架 | Baseline Snapshot 在原作者下架前已落本地 |
| 18 | 视频文件存储膨胀 | media_path 可选；本地文件定期清理策略 |
| 19 | Obsidian Vault 过大 | 只写 Verification + 摘要（Q11 决策），transcript 留 SQLite |

## 概率-影响矩阵（Top 5）

```
高影响 │  4 AI幻觉      5 证据时间污染
      │  1 平台反爬    3 OCR/ASR错误
 影响  │
低影响 │─────────────── 概率 ──────────────
      │  低            高
```

## 缓解优先级

1. **V0.1**：风险 2、3、6、7（字幕、ASR 错误、模糊预测、Revision）
2. **V0.2**：风险 1、4、5、13（反爬、幻觉、时间污染、负面证据）
3. **V0.3**：风险 8、9、10、11（统计陷阱、选择性记忆、证据冲突、多义性）
