# 06 · Evidence Schema（核心）

## 完整字段定义

```yaml
evidence:
  id: ULID
  prediction_id: ULID       # 关联预测（baseline 时可空）
  content_id: ULID          # 来源内容

  source: "tavily"          # 'tavily'|'bing'|'akshare'|'fred'|'manual'|'baseline_snapshot'
  source_type: "news"       # 'news'|'official'|'market_data'|'filings'|'regulatory'
  url: "https://reuters.com/..."
  title: "Fed signals dovish shift"
  publisher: "Reuters"

  published_at: 1755900000  # 证据原始发布时间（核心 · 防事后诸葛亮）
  collected_at: 1756000000  # 我们抓到的时间
  is_official: 0            # 0/1（官方公告/央行/交易所 = 1）
  is_direct: 1              # 是否直接证据（一手数据 = 1，二手转述 = 0）
  credibility: 0.9          # 0-1 来源可信度

  summary: "美联储暗示鸽派转向..."    # AI 摘要
  raw_json: "{}"            # 原始内容（搜索 API 返回原文）
  relation: "primary"       # 'baseline'|'primary'|'contradicting'|'corroborating'|'context'

  collection_batch: "uuid"  # 同批次 UUID（一次验证 = 一批）
  created_at: 1756000000
```

## 来源类型与可信度基准

| source_type | 示例 | 默认 credibility | is_official |
|------------|------|-----------------|-------------|
| official | 央行公告、交易所、SEC、FRED、政府统计 | 0.95 | 1 |
| market_data | akshare/yfinance 行情、财报数据 | 0.90 | 0 |
| news | Reuters/Bloomberg/财新/第一财经 | 0.75 | 0 |
| filings | 公司公告、招股书 | 0.90 | 0 |
| regulatory | 监管政策文件 | 0.95 | 0 |
| 二手自媒体 | 博主转述、营销号 | 0.40 | 0 |

## Evidence 生命周期

```
baseline（预测时快照）
   ↓
primary（验证时收集）
   ↓
contradicting（与预测矛盾的证据）  ← 必须显式保留，不能只留正面证据
   ↓
corroborating（佐证）
   ↓
context（背景）
```

**关键：`relation='contradicting'` 的证据必须被收集并保留。** 只收集正面证据会导致系统性偏误（"看起来都对了"）。

## 时间模型（防"事后诸葛亮"三重闸）

| 时间戳 | 作用 |
|--------|------|
| `published_at` | 证据本身发布/数据日期。**核心字段**：决定证据属于"预测前"还是"预测后" |
| `collected_at` | 我们抓取的时间。用于审计"当时搜不到的东西现在搜到了" |
| `prediction_at`（在 prediction 表） | 预测发表时间 |
| `verification_at`（在 verification 表） | 验证发生时间 |

**Verification Engine 强制规则：**

```
判定预测只允许用：published_at >= prediction_at 的证据（post-time facts）
建立"当时共识"用：published_at <= prediction_at 的证据（prediction-time info）
两者绝不允许混淆。
```

## Evidence 收集流程

```
prediction.status = due
      │
      ▼
EvidenceCollector 启动
  ├─ 1. 抓 baseline 更新版（若 baseline 过 1 个月，重新抓预测时点快照）
  ├─ 2. SearchProvider 拉第一批（query 由 AI 生成，覆盖正/反两面）
  ├─ 3. MarketDataProvider 拉结构化数据（金融标的）
  └─ 4. 每条写入 evidence 表：published_at / collected_at / relation / credibility
      │
      ▼
VerificationEngine.analysis 读取
```

## 负面证据缺失问题（必须显式建模）

搜索"黄金涨到 4000"返回大量正面讨论；"没有涨到 4000"很难直接搜到。

**应对：**
1. EvidenceCollector 的 query 模板强制包含"反方向"查询（"黄金 未能 突破 4000" / "gold fails to reach 4000"）
2. Verification Engine 的判定逻辑包含 `absent_evidence` 概念：**"在 due 时点未达到阈值"本身就是证据**（来自 market_data，可信度最高）
3. AI 推理链必须回答："如果没有正面证据，是否说明预测失败？"（absent vs contradicting 区分）
