# 09 · 项目运行方式

## 环境要求

- Python **3.13**（`start.bat` 引用 `...\python\versions\3.13.12`）。
- Node.js + npm（前端）。
- 可选系统工具：**FFmpeg**（本地转写）、**Chrome/Edge**（抖音 playwright cookie）、**Ollama**（本地 AI 兜底）。

## 一、后端（FastAPI）

### 方式 A：`start.bat`（Windows 一键）

```
start.bat
```

脚本逻辑（[start.bat](../../start.bat)）：
1. 若 `.venv` 不存在 → 用 Python 3.13 创建虚拟环境 + `pip install -r requirements.txt`。
2. 若 `config/config.json` 不存在 → 从 `config.example.json` 复制生成。
3. 启动 `uvicorn app.main:app --host 127.0.0.1 --port 8781`。

> 请打开 `config/config.json` 填入 `ai.cloud.api_key`（DeepSeek 官方 key）。

### 方式 B：手动

```bash
# Windows
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8781
# 或激活环境后
python -m uvicorn app.main:app --host 127.0.0.1 --port 8781
```

- 启动时自动执行：`init_db()`（建表 + 迁移）、`backfill_missing_baselines()`、`_backfill_content_stats()`，随后启动两个后台调度线程（验证到期扫描 + 关注监控 tick）。
- 启动后浏览器访问 **`http://127.0.0.1:8781`**（返回 `static/vue/index.html`，Vue 模块化前端）。

## 二、前端（Vue3 + Vite）

```bash
cd h:\workbuddytest\creator-insight
npm install        # 首次安装依赖
npm run dev        # 开发服务，端口 5173，/api 代理到后端 8781
```

生产构建：

```bash
npm run build      # vue-tsc -b && vite build → 产物到 static/vue
```

## 三、配置准备

1. 复制模板：`copy config\config.example.json config\config.json`（start.bat 会自动做）。
2. 必填：`ai.cloud.api_key`。
3. 可选：`search.tavily_api_key`（解锁真实搜索）；`obsidian.vault_path + write_enabled`（写 Obsidian）；`notify.channels/webhook_url/smtp`（通知通道）；`monitor.profile_path`（登录态 Chrome 批量抓目录）。

## 四、测试与工具脚本（[scripts/](../../scripts/)）

| 脚本 | 内容 | 状态（PROJECT_STATUS） |
|------|------|------------------------|
| `smoke_test.py` | 16 项冒烟测试 | ✅ 16/16 |
| `v02_test.py` | 15 项 V0.2 状态机测试 | ✅ 15/15 |
| `v03_test.py` | 43 项 V0.3 测试（硬预测自动过/24h 撤销/博主画像） | ✅ 43/43 |
| `v04_test.py` | 22 项 V0.4 测试（通知通道/触发点集成） | ✅ 22/22 |
| `v05_test.py` | 27 项 V0.5 测试（异步 ingest 任务/简体文案保存） | ✅ 27/27 |
| `ai_evidence_test.py` | 8 项 AI 生成证据链测试 | ✅ 8/8 |
| `doctor.py` | 一键诊断：配置/AI key/FFmpeg/whisper/playwright/yt-dlp/数据目录/后端可启动 | 12 项诊断 |
| `ingest_demo.py` | 演示数据入库 | — |

运行测试示例：

```bash
.venv\Scripts\python.exe scripts\smoke_test.py
```

## 五、数据落地位置

```
data/
├── creator_insight.db         # SQLite 主库
├── media/                     # 下载的音视频
├── avatars/                   # 头像缓存（/avatars 暴露）
├── cookies/                   # 抖音 cookie 缓存
├── transcripts/               # {标题}_简体校对版.md / {标题}_AI总结.md
│   └── whisper/               # whisper 转写产物 (md/json/srt)
└── models/embedding/          # 本地 embedding 模型
```

Obsidian 输出：`{vault_path}/{root_folder}/Creators/{博主}/verification_*.md`、`prediction_*.md`。

## 六、常见排坑（PROJECT_STATUS 记录）

1. **旧进程占端口 → 新代码 404**：`netstat -ano | findstr 8781` 找 PID → `Stop-Process -Id <PID>` 再重启。
2. **database is locked**：AI 调用期间不持写事务（`ingest_pipeline` 已拆三段）；确认无长事务并发。
3. **AI 超时/断连（NVIDIA NIM 类 key）**：AIGateway 走直连/代理双通道重试；优先用 DeepSeek 官方。
4. **抖音风控「Fresh cookies needed」**：adapter 会自动用 playwright 生成 cookie；或手动填 `monitor.douyin_cookie` / 配置 `monitor.profile_path`。
5. **长视频 ingest 超时**：接口已改异步（提交即返回 task_id + 前端轮询），无需等待同步响应。
6. **Whisper 英文空转写**：`transcription.whisper.language` 留空自动检测（勿强设 zh）。
7. **语义检索报"模型未安装/未建索引"**：先 `POST /api/semantic/download` 下载模型、`POST /api/search/index`（与 index/predictions）建索引再搜索。