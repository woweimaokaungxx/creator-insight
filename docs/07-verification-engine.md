# 07 · Verification Engine

## 流程（到期队列 → 证据 → AI 判定 → 人工确认）

```
[Scheduler] 每日扫：SELECT * FROM prediction WHERE status='active' AND due_at <= now()
        │
        ▼
  预测 → status='due'
        │
        ▼
[EvidenceCollector]
  ├─ 抓 prediction-time baseline（如果还没有）→ relation='baseline'
  ├─ 调 SearchProvider 拉第一批 evidence（query 由 AI 生成，覆盖正/反）
  ├─ 调 MarketDataProvider 拉结构化数据（金融标的）→ relation='primary'
  └─ 每条写到 evidence 表，标明 published_at / collected_at / relation
        │
        ▼
  预测 → status='verifying'
        │
        ▼
[VerificationEngine.analysis]   ← AI 推理（强模型）
  ├─ 读取 prediction + evidence（按 published_at 排序）
  ├─ 强制只用 published_at >= prediction_at 的 evidence 计算 post-time 事实
  ├─ 强制只用 published_at <= prediction_at 的 evidence 建立当时共识
  ├─ 输出 ai_verdict + ai_score + ai_confidence + ai_reasoning_json
  │    ai_reasoning_json 结构:
  │    {
  │      "post_time_facts": [{"evidence_id","fact","supports":"for|against|mixed"}],
  │      "judgment": "...",
  │      "uncertainty_reasons": ["..."]
  │    }
  └─ 写入 verification.ai_*
        │
        ▼
  预测 → status='ai_verified' → status='human_review'
        │
        ▼
[UI]  弹到"待复核"队列
        │
        ▼
[Human] 点确认 / 修改 verdict → 写入 verification.human_*
        │
        ▼
  预测 → status='final'，verification.locked=1
        │
        ▼
[Recalc] 触发 creator_reliability 重算（异步，可延迟）
```

## Verdict 枚举与计分

| verdict | 含义 | 计入正确率 |
|---------|------|-----------|
| correct | 预测成立 | 1.0 |
| partial | 部分成立（方向对幅度差 / 部分兑现） | 0.5 |
| incorrect | 预测失败 | 0.0 |
| inconclusive | 无法判断（证据不足） | 不计入 |
| invalid | 预测本身非法（抽取错误） | 不计入 |

## AI 与人工的职责分离

| 角色 | 输出 | 优先级 |
|------|------|--------|
| AI | ai_verdict + ai_score + ai_confidence + ai_reasoning_json | 只作参考 |
| Human | human_verdict + human_score + human_notes | **最终** |

```
final_verdict = human_verdict（若有） else ai_verdict
```

**V0.1-V0.2：所有预测必须人工确认（human_review 不可跳过）。**
**V0.3+：auto_apply_eligible=1 的硬预测可自动 final，但保留 24h 撤销窗口。**

## 人工复核 UI 应展示什么（让人类 30 秒内判断）

1. **预测原话**（raw_text + 视频时间戳链接）
2. **时间窗口**（prediction_at → due_at）
3. **结构化数据**（如果是金融标的：当时价格 vs 现在价格）
4. **AI 推理链**（ai_reasoning_json 可折叠展开）
5. **证据列表**（每个 evidence 的 source/标题/发布时间/relation）
6. **一键判定按钮**：正确 / 部分正确 / 错误 / 无法判断 / 无效

## 防止"事后诸葛亮"的三道闸

1. **Evidence 时间戳强制分层**：post-time 事实只用 `published_at >= prediction_at` 的 evidence
2. **Prediction-time baseline 入库时立即拉**：未来 verification 时可与当下证据对比
3. **ai_reasoning_json 全文留档**：人工复盘时可看到 AI 用了哪些 evidence

## 提前结算（可选，V0.2 后期）

- 若预测在 due 前已"显然失败/成功"（如标的已触达幅度上限），UI 提供"提前结算"按钮
- 提前结算须人工确认，记录 `verification.human_notes` 注明"提前结算于 X"
- 统计时正常计入（不因提前而有偏差）

## 自动化升级路径

```
V0.1-V0.2  人工 100%
    │
    ▼
V0.3       硬预测自动（auto_apply_eligible=1）+ 24h 撤销窗口
    │
    ▼
V0.4+      软预测也尝试 AI 初判，但永不跳过人工（置信度高于阈值仅缩短人工时间）
```
