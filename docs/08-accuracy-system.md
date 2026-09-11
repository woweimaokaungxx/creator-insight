# 08 · Accuracy 统计系统

## 设计原则

> **accuracy 是计算结果，不是原始数据。** 数据库只存 `verdict` 与元数据，一切百分比由聚合视图现算或异步重算。

## 基础正确率

```python
# 三种"对"
correct      = final_verdict == 'correct'      # 1.0
partial      = final_verdict == 'partial'      # 0.5
incorrect    = final_verdict == 'incorrect'    # 0.0
inconclusive = final_verdict == 'inconclusive' # 不计入
invalid      = final_verdict == 'invalid'      # 不计入

# 基础正确率
verified_count = count(final_verdict in {correct, partial, incorrect})
base_accuracy  = (correct*1 + partial*0.5) / verified_count

# 关键：永远展示样本量
sample_size = verified_count
```

**绝不四舍五入隐藏样本量。UI 永远显示 `base_accuracy (n=verified_count)`。**

## 分桶统计（每桶 n<30 显示"样本不足"）

| 桶维度 | 说明 | 何时可用 |
|--------|------|---------|
| by_domain | 宏观 / 指数 / 个股 / 政策 | V0.3 |
| by_horizon | 短期(<1月) / 中期(1-6月) / 长期(>6月) | V0.3 |
| by_subject_type | index / stock / fx / commodity / macro | V0.3 |
| by_confidence_band | [0-0.3, 0.3-0.6, 0.6-0.8, 0.8-1.0] | V0.3+ |
| by_quarter | 季度趋势 | V0.4 |
| by_creator | 博主对比 | V0.3 |

## 置信度校准（Brier Score，样本量门槛）

```
Brier Score = mean( (actual_outcome - predicted_probability)^2 )
  - 每桶（如"大概率"=0.65-0.8 区间）至少 30 个样本才计算
  - 显示：Brier Score 0.18 良好 / 0.25 一般 / 0.35+ 差
  - 校准展示：散点图（博主声称概率 x 轴，实际发生频率 y 轴，对角线为完美校准）
```

**前提：只有 confidence_score 字段存在且样本量足够才有意义。V0.1 只埋数据，不做校准。**

## 统计学陷阱（不制造无意义指标）

| 陷阱 | 处理 |
|------|------|
| 样本量不足 | n<30 显示"样本不足"，不输出百分比 |
| 预测难度差异 | V0.5 前不做难度加权（标注为未来工作） |
| 时间跨度 | 分 horizon 桶，不做单一"长期准确率" |
| 部分正确 | 0.5 权重，但统计页同时显示 correct/partial/incorrect 原始计数 |
| 事后修改 | Revision 链分开统计；"原条 vs 改口条"分别展示 |
| 博主只讲成功 | 系统从 event_log 重建"全部预测"而非"博主提起的预测" |

## 博主可信度画像（目标形态）

```
博主 A
总体预测正确率：72%（n=181）
宏观预测：81%（n=32）   指数预测：74%（n=23）
个股预测：53%（n=47）   政策预测：86%（n=21）
短期预测：49%（n=41）   中期预测：68%（n=29）   长期预测：84%（n=19）
高置信预测：57%（n=14）  低置信预测：76%（n=25）
预测总数：238   已验证：181   待验证：57
置信度校准：较差（Brier 0.32）
擅长：宏观政策
弱项：短线个股
```

## 第一版实现哪些（MVP 决策）

| 指标 | V0.1 | V0.3 | V0.5+ |
|------|------|------|-------|
| base_accuracy + 样本量 | ✅ | ✅ | ✅ |
| by_domain / by_horizon / by_subject | — | ✅ | ✅ |
| by_confidence_band | — | 埋数据 | ✅ |
| Brier Score 校准 | — | 埋数据 | ✅（样本够才显示）|
| 难度加权 / 市场基准对比 | — | — | ✅ |
| 季节性 / 季度趋势 | — | — | ✅ |
