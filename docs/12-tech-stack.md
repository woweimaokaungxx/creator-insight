# 12 · 推荐技术栈

## 技术选型（按设备：16GB RAM / Windows / H 盘）

| 层 | 选型 | 理由 |
|----|------|------|
| 后端 | **Python 3.13 + FastAPI** | 与现有 hermes_venv2 / boss_export.py 同生态；异步 + SSE 友好 |
| DB | **SQLite + 原生 JSON 字段**（不用 ORM） | CreatorDistill 也不用 ORM；直接 sqlite3 库 |
| AI | **DeepSeek-V3（云）+ 本地 Qwen2.5-7B-Instruct-4bit 兜底** | 兼顾成本/能力/隐私 |
| Search | **Tavily（主力）+ Bing（兜底）** | Tavily 适合 AI 消费，Bing 国内可用 |
| MarketData | **akshare（中国数据·免费）+ yfinance（美国）+ FRED（宏观）** | 都是 Python 生态，0 额外成本 |
| ASR | **本地 faster-whisper + 平台原生字幕** | 环境已有，0 API 费 |
| 前端 | **纯 HTML + htmx + Alpine.js + PicoCSS** | 一个人写得动，无构建步骤 |
| 打包 | **Uvicorn + 单一 .bat 启动** | 与 CreatorDistill 启动方式一致 |
| 调度 | **APScheduler**（进程内·V0.2 引入） | 不引入额外 worker |
| 视频下载 | **yt-dlp + 抖音专用接口**（沿用 douyin-download skill） | 已有基础 |

## 为什么不是其他方案

### Web App（FastAPI）vs 桌面应用（Tauri）

| 维度 | FastAPI Web App（推荐） | Tauri 桌面 |
|------|------------------------|-----------|
| 开发速度 | ✅ 快，2-3 周可出 UI | ❌ 慢，Rust 学习曲线 + 额外 2-3 月 |
| 部署 | ✅ 双击 .bat 启动浏览器 | ✅ 单文件 |
| 扩展 | ✅ 未来加 Docker/NAS/云服务容易 | ❌ 需重写服务层 |
| 与你现有资产 | ✅ 复用 hermes_venv2 / 现有 Python | ❌ 技术栈完全不同 |

**结论：本地 Web App 是 V0.1-V0.4 的最优解。Tauri 等 V0.5 产品定型后再考虑。**

### 前端：htmx + Alpine  vs React/Next.js

| 维度 | htmx + Alpine（推荐） | React/Next.js |
|------|----------------------|---------------|
| 构建步骤 | ✅ 无，直接 HTML | ❌ 需要 node 构建 |
| 学习成本 | ✅ 低 | ❌ 高 |
| 单文件分发 | ✅ | ❌ |
| 交互复杂度 | 够用（列表/表单/流程） | 多（但本项目不需要） |

**结论：单用户本地工具不需要前端框架。htmx + Alpine 足够。**

### 为什么不用向量数据库

- V0.1-V0.3 检索需求 = 按状态/博主/领域过滤，SQL 足够
- 语义搜索（跨博主找相似预测）是 V0.5 的事，届时再引入 sqlite-vec 或独立向量库
- **不提前引入复杂度**

## 成本估算（月）

| 项目 | 估算 |
|------|------|
| DeepSeek-V3 API | 月处理 200 视频 ≈ ¥5-15 |
| Claude/GPT（仅 Verification 推理） | 每月几十次 ≈ ¥10-20 |
| Tavily API | 免费额度够用（1000 次/月） |
| akshare/yfinance/FRED | 免费 |
| 本地 Qwen 模型 | 一次性下载 ~4GB |
| **总计** | **< ¥50/月** |

## 配置管理

沿用 CreatorDistill 模式：`config/*.example.json` → 用户复制为 `config/*.config.json`（.gitignore）

```
config/
  ai.config.example.json        # AIProvider 路由（cloud/local）
  search.config.example.json    # Tavily/Bing keys
  market-data.config.example.json # akshare/yfinance 开关
  obsidian.config.example.json  # Vault 路径
  platform.config.example.json  # 抖音/B站 cookie 配置
```
