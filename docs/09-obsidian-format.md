# 09 · Obsidian Markdown 结构

## 文件夹布局

```
<你的 Obsidian Vault>/
  05_Creators/
    <creator_name>/
      _index.md              ← 博主主页（自动）
      _reliability.md        ← 博主画像（自动+手改）
      videos/
        2026-08-15_<title>.md   ← 每条 Content 一份（摘要）
      predictions/
        pred_<ULID>.md         ← 每条 Prediction 一份
      verifications/
        ver_<ULID>.md          ← Verification 报告
```

## 防覆盖机制（Q12 决策：frontmatter + auto/human 双层标记）

```
文件结构：
┌─────────────────────────────────────┐
│  frontmatter（YAML）                 │  ← 软件只改 schema_version / auto_generated
│  <!-- BEGIN AUTO -->                │  ← 软件可全文重生成
│  （自动生成内容）                    │
│  <!-- END AUTO -->                  │
│  <!-- BEGIN HUMAN -->               │  ← 软件永不触碰
│  （你的手工笔记/链接/反思）          │
│  <!-- END HUMAN -->                 │
└─────────────────────────────────────┘
```

| 区域 | 软件行为 | 你的行为 |
|------|---------|---------|
| frontmatter | 只更新 `schema_version`、`auto_generated` 字段 | 其他字段（tags/aliases）可自由编辑，软件不动 |
| AUTO 区域 | 每次同步**整体覆盖** | 不要编辑（编辑了也会被覆盖） |
| HUMAN 区域 | 永不触碰、永不删除 | 自由编辑、自由增长 |

**删除行为**：软件只删除"自己创建的、空的 AUTO 区域"；HUMAN 区域即使为空也保留。

## Verification Markdown 示例（完整）

```markdown
---
prediction_id: 01J8XH7V2R9K4N8Q3M6P5T2YBC
creator_id: 01J8X9ZWP9HGAZJ4T7KQV3F2N1C
content_id: 01J8X9ZWP9HGAZJ4T7KQV3F2N1D
verdict: partial
score: 0.5
verified_at: 2026-09-15
domain: ["finance","macro"]
horizon: "30d"
subject_symbol: "XAU"
schema_version: 0.1
auto_generated: true
---

<!-- BEGIN AUTO · DO NOT EDIT BELOW MANUALLY · 软件下次同步会覆盖 -->
# 验证报告：黄金 30 天内涨幅 ≥10%

**博主**：陈思进 · [主页](../_index.md)
**视频**：[2026-08-15 - 黄金新一轮上涨来了？](videos/2026-08-15_黄金新一轮上涨来了？.md)
**预测原话**（不可修改）：
> "黄金未来 30 天上涨超过 10%。"
> — [00:03:54] of video

## 时间窗口
- 预测于 2026-08-15
- 截止 2026-09-14
- 实际窗口 30 天

## 判定
- 实际金价从 $2,380 涨至 $2,475，+4.0%
- 阈值 10% 未达到，但方向正确

## AI 初步判定（partial, score=0.5）
依据证据：
- [1] Reuters, 2026-08-22「Fed signals dovish」— [reuters.com/...]
- [2] 上海黄金交易所公告, 2026-08-30 — [sge.com/...]
- [3] Bloomberg: Gold +4.1% in Aug — [bloomberg.com/...]

## 人工复核（已确认 partial）
[Human @ 2026-09-15 22:10] 方向对，幅度不对。博主显然在押注 Fed 转向，但时间窗估错。
<!-- END AUTO -->

<!-- BEGIN HUMAN · 此区域以下你可自由编辑 · 软件不会覆盖 -->
## 我的反思
- 陈思进在方向预测上一贯较强，但具体点位基本不准
- 关注他的宏观叙事，跟单不要跟具体数

## Dataview 查询建议
```dataview
TABLE verdict, score, verified_at
FROM "05_Creators/陈思进/predictions"
WHERE contains(domain, "macro")
SORT verified_at DESC
```
<!-- END HUMAN -->
```

## 同步策略

| 时机 | 触发 | 范围 |
|------|------|------|
| 新视频处理完 | 立即 | 写 videos/ + predictions/ |
| 验证完成 | 立即 | 写 verifications/ + 更新 _reliability.md |
| 数据修正 | 手动 | 重写对应 AUTO 区域 |
| Vault 初始化 | 首次 | 创建目录结构 + _index.md |

## 关键约定

1. **文件名永不包含平台视频 ID** —— 用 `日期_标题`，便于 Obsidian 检索
2. **每个 Prediction 独立文件** —— 便于 Dataview 查询和双链
3. **_reliability.md 由软件生成画像 + 你手工补充** —— AUTO 区域是统计表，HUMAN 区域是你的判断
4. **永远不写 raw transcript 到 Obsidian**（Q11 决策）—— 逐字稿留在 SQLite，Obsidian 只放摘要和验证
