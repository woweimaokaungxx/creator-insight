# 02 · 核心数据模型

## 数据链路

```
Creator → Content → Transcript → Claim → Prediction → Evidence → Verification → Creator Reliability
```

## 关系图

```
                ┌──────────────┐
                │   Creator    │  博主实体（平台+ID+领域）
                └──────┬───────┘
                       │ 1:N
                       ▼
                ┌──────────────┐
                │   Content    │  内容作品（视频/文章/播客）
                └──────┬───────┘
                       │ 1:1
                       ▼
       ┌───────────────────────────────┐
       │   Transcript（含 baseline）   │  原始逐字稿 + 媒体 ref（不可篡改）
       └────────────────┬──────────────┘
                        │ 1:N
              ┌─────────┴─────────┐
              ▼                   ▼
        ┌──────────┐        ┌──────────────────┐
        │  Claim   │        │  Prediction      │  ◀── 产品核心
        │ (观点)   │        │  (可证伪预测)    │
        └──────────┘        └────────┬─────────┘
                                    │ 1:N（含 revisions）
                  ┌─────────────────┴──────────────┐
                  ▼                                ▼
            ┌─────────────┐                ┌────────────────┐
            │  Baseline   │                │   Evidence     │
            │  Snapshot   │                │ (到期证据)     │
            │ (预测时快照) │                └────────┬───────┘
            └─────────────┘                         │ N:M
                                                    ▼
                                            ┌──────────────────┐
                                            │  Verification    │  AI → 人工 → final
                                            └────────┬─────────┘
                                                     │ 1:1
                                                     ▼
                                            ┌──────────────────┐
                                            │ CreatorReliability│
                                            │  (聚合视图)       │
                                            └──────────────────┘
```

## 各实体职责

| 实体 | 定义 | 关键规则 |
|------|------|---------|
| **Creator** | 博主 | 平台+ID 唯一；领域标签可多选 |
| **Content** | 单条内容 | 平台+videoId 唯一；保存原始 JSON 证据 |
| **Transcript** | 逐字稿 | **永不可被 AI 总结覆盖**；带时间戳分段 |
| **Claim** | 观点/判断（不可证伪） | 保留但不进入验证管线 |
| **Prediction** | 可证伪预测（核心） | 时间+标的+方向三者齐备；raw_text 永不可变 |
| **Baseline Snapshot** | 预测发表时的信息快照 | 防止事后诸葛亮（详见 07） |
| **Evidence** | 验证证据 | 必须带 published_at + collected_at 双时间戳 |
| **Verification** | 验证结果 | ai_verdict + human_verdict 分离；final 锁定 |
| **Creator Reliability** | 博主可信度聚合 | **计算结果，不是原始数据**；on-demand 重算 |

## 时间模型（关键）

每个 Prediction 涉及 4 个时间点：

```
prediction_at        证据发表的    prediction_at        验证发生
 (预测发表时间)       baseline      (证据发布时间)      (verification_at)
    │                   │              │                   │
    ▼                   ▼              ▼                   ▼
────●───────────────────●──────────────●───────────────────●────▶ 时间轴
    │                   │              │                   │
    │←─ 预测前信息 ────────→│←─ 预测后信息 ────→│←─ 验证中 ─────→│
    │   (可用于判断      │   (可用于验证     │               │
    │    当时共识)       │    预测正确性)   │               │
```

**核心原则：Verification Engine 只允许用 `published_at >= prediction_at` 的证据判定预测；`published_at < prediction_at` 的证据只用于建立"当时共识"，绝不用于判定。**
