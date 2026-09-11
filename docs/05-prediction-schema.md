# 05 · Prediction Schema（核心）

## 完整字段定义

```yaml
prediction:
  id: ULID
  content_id: ULID            # 关联 Content
  parent_prediction_id: ULID  # Revision 链（博主后来修改表述时创建新行）
  revision_no: 1

  # ─── 原始语言（永不可变）───
  raw_text: "黄金未来 30 天上涨超过 10%"
  speaker: "博主本人"
  start_offset: 234.5         # 秒
  end_offset: 242.1

  # ─── 抽取字段 ───
  subject:
    type: "commodity"         # stock|index|fx|commodity|macro_indicator|policy_event|company_event
    symbol: "XAU"             # 或 "上证指数"/"美联储7月议息"
    name: "黄金"
  direction: "up"             # up|down|flat|range|above|below|event_yes|event_no
  magnitude:
    min: 0.10                 # 10%
    max: 0.10                 # 如范围则是 [0.05, 0.15]
    unit: "ratio"             # ratio|absolute|range|threshold
    is_relative: true         # true=相对/百分比；false=绝对点位
  time_expression_raw: "未来 30 天"
  time_window:
    raw: "未来 30 天"
    parsed_start: 1755945600  # prediction_at + parsed_offset
    parsed_end: 1758537600
    granularity: "day"        # day|week|month|quarter|year|event_horizon|fuzzy
    is_fuzzy: false           # true → due_at 可空，标 requires_human_confirmation
    parsed_at: 1755945600
  conditions: []              # ["如果美元指数跌破 100"]
  confidence_raw: "大概率"
  confidence_score: 0.65      # AI 转换（calibration 依据）

  # ─── 生命周期 ───
  status: "extracted"         # 见状态机
  prediction_at: 1755945600
  due_at: 1758537600
  auto_apply_eligible: 0      # 1 = 硬预测可自动验证；0 = 必须人工
  prediction_baseline_evidence_id: ULID  # ← 关键，预测时快照
```

## Schema 设计原则

1. **`raw_text` 永不可变** —— Verification 之后就算推翻也只改 status，不动 raw_text
2. **任何时间字段都有 `*_raw` + `parsed_*` 双轨** —— raw 给人类读，parsed 给机器判断
3. **`subject` 用 JSON** —— 后期支持嵌套（"上证指数 + 突破 3500 + 持续 3 天"）
4. **`direction` 是枚举，不是自由文本** —— 避免 AI 写出"大概率上涨后回落"的复合方向
5. **`confidence_raw` 和 `confidence_score` 分开** —— 前者是博主原话，后者是 AI 估的概率。**置信度校准只信 score，校准反过来修正 raw → score 的映射**

## 提取门槛（Q7 决策：时间+标的+方向齐备才算）

Prediction 必须同时满足：

```
1. subject 有明确标的（type + symbol/name 非空）
   AND
2. direction ∈ 枚举值（非自由文本）
   AND
3. time_window.parsed_end 可解析 或 有事件触发点
```

缺少任一项 → 降级为 **Claim**（观点），不进预测管线。

## 时间抽取规则（时间模糊必须标记，不许 AI 瞎猜）

| 原始表达 | parsed_start | parsed_end | granularity | is_fuzzy |
|---------|-------------|------------|-------------|----------|
| "明天大盘会涨" | prediction_at | +1 天 | day | false |
| "未来 30 天" | prediction_at | +30 天 | day | false |
| "未来一年" | prediction_at | +1 年 | year | false |
| "未来几年" | prediction_at | +3 年 | year | **true**（默认3年，需人工确认）|
| "下半年" | prediction_at | 当年底 | quarter | false |
| "美联储 9 月议息" | 9 月议息日 | 议息日+1 | event_horizon | false |
| "等政策落地后" | 空 | 空 | fuzzy | **true**（必须人工指定）|

**规则：`is_fuzzy=true` 的预测，due_at 允许为空，UI 标"待人工确认时间"，绝不自动进入 due 队列。**

## 状态机

```
extracted ──→ pending_review ──┬──→ invalid（人工确认不是预测）
                              └──→ active ──→ due（Scheduler 扫描）
                                                  │
                                                  ▼
                                             verifying
                                                  │
                                                  ▼
                                             ai_verified ──→ human_review
                                                  │
                                                  ▼
                                              final（锁定）
                                              uncertain（可附加在 final 的备注）
```

| 状态 | 含义 | 谁能进入 |
|------|------|---------|
| extracted | AI 刚抽出 | system |
| pending_review | 待人工确认 Prediction 本身合法 | system |
| active | 合法，等待到期 | human |
| due | 已到期，待验证 | Scheduler |
| verifying | 证据收集中 | system |
| ai_verified | AI 给出初步 verdict | system |
| human_review | 等待人工确认 | system |
| final | 锁定 | human |
| invalid | 不是预测（错误抽取） | human |

## Revision 设计（防"后来改口"）

- 博主后来修改表述 → 新建一条 Prediction，`parent_prediction_id` 指向原条，`revision_no+1`
- **原条 raw_text 永远不变**；新条带 `prediction_created_at` 记录"改口时间"
- Verification 只对**最新有效 revision** 执行；但 Reliability 统计时，**原条与改口条分别计**（暴露"博主频繁改口"）

## auto_apply_eligible 判定规则（V0.1 硬编码在 AI 抽取 Prompt 输出时计算）

```
1. subject.type ∈ {'stock','index','fx','commodity','macro_indicator'}
   AND
2. magnitude 有 min/max/unit（不能为空）
   AND
3. direction ∈ 枚举值（非自由文本）
   AND
4. time_window.is_fuzzy = false
   AND
5. confidence_raw 不在 ['可能','或许','大概','说不定','也许']
=>
auto_apply_eligible = 1（硬预测，V0.3 起可自动验证）
```

不满足任一条件 → auto_apply_eligible = 0（软预测，永远人工复核）
