# 10 · UI 信息架构

## 页面结构

| 页面 | 路由 | 主要元素 |
|------|------|---------|
| **Dashboard** | `/` | 待复核数 / 到期预测数 / 本周新增 / 最值得关注的博主 |
| **Creators** | `/creators` | 列表 + 加博主按钮 |
| **Creator Detail** | `/creators/:id` | 博主基本信息 + Predictions 列表 + Reliability 面板 |
| **Videos** | `/videos` | 最近视频流；粘链接入口 |
| **Video Detail** | `/videos/:id` | 原始信息 / Transcript / Claims / Predictions 列表 / 进入 verification |
| **Predictions** | `/predictions` | 全站 Prediction 列表（filter: status / domain / horizon） |
| **Prediction Detail** | `/predictions/:id` | 原始话 + 抽取字段 + 时间轴 + Evidence 列表 + Verification 流程入口 |
| **Verification Queue** | `/queue` | 待人工复核的 verification（核心日常页） |
| **Analytics** | `/analytics` | 博主排行 / 置信度校准散点图 / 时间分布 |
| **Settings** | `/settings` | AI / Search / Knowledge Base / Obsidian Vault 路径 / 平台账号 / 数据导入导出 |

## 核心交互流

### 流 1：新视频入库（每天）
```
粘贴抖音/B站链接或分享文案
  → 自动解析（平台 + videoId + 标题）
  → 抓取/转写 → Transcript
  → AI 总结 + Claim 抽取 + Prediction 抽取
  → 入库 SQLite → 写 Obsidian
  → 跳转 Video Detail
```

### 流 2：人工复核（每周）
```
打开 Dashboard → 看到"待复核 3 条"
  → 进入 Verification Queue
  → 每条：读原话 → 看证据 → 确认/修改 AI 判定
  → 一键 final
```

### 流 3：博主画像（每月）
```
Creator Detail → Reliability 面板
  → 基础正确率 + 分桶 + 样本量
  → 置信度校准（V0.3+）
```

## 页面优先级

| 页面 | V0.1 | V0.2 | V0.3 |
|------|------|------|------|
| Dashboard（简化版） | ✅ | ✅ | ✅ |
| Videos + Video Detail | ✅ | ✅ | ✅ |
| Creators + Creator Detail | ✅ | ✅ | ✅ |
| Predictions + Prediction Detail | ✅ | ✅ | ✅ |
| Verification Queue | — | ✅ | ✅ |
| Analytics | — | — | ✅ |
| Settings（完整版） | 简化版 | ✅ | ✅ |

## UI 设计原则

1. **数据永远从 SQLite 读，不缓存 Markdown** —— 保证一致性
2. **"粘链接 → 完成"一条主路径** —— 每天 5 分钟完成
3. **待复核队列是日常主战场** —— 按钮大、证据清晰、30 秒可决断
4. **永远显示样本量** —— 任何百分比都带 (n=xx)
5. **本地优先** —— 无登录、无云同步，数据全在本机
