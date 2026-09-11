# creator-insight 前端（Vue3 + TDesign）

基于 **Vue 3 + TypeScript + TDesign Vue Next + ECharts** 的 Web UI，对接 `creator-insight` FastAPI 后端。

## 目录结构

```
creator-insight/
├── package.json          # 前端依赖清单
├── vite.config.ts        # 构建配置 + /api 代理到后端 8781
├── tsconfig*.json        # TypeScript 配置
├── index.html            # 入口
└── src/
    ├── main.ts           # 应用入口（注册 TDesign）
    ├── App.vue           # 路由 + 页面编排
    ├── layouts/Layout.vue
    ├── pages/Dashboard.vue      # 提交/进度/指标
    ├── pages/Predictions.vue    # 预测列表/确认/复核
    └── api/client.ts     # 后端 API 封装（全部接口）
```

## 依赖清单

| 包 | 用途 |
|----|------|
| `vue` ^3.5 | 核心框架 |
| `tdesign-vue-next` ^1.10 | UI 组件库（TDesign） |
| `tdesign-icons-vue-next` ^0.3 | TDesign 图标 |
| `echarts` ^5.5 | 图表（Brier 校准散点图等） |
| `vite` ^6 / `@vitejs/plugin-vue` | 构建工具 |
| `typescript` / `vue-tsc` | 类型检查 |

## 启动

```bash
# 安装依赖（首次）
npm install

# 开发模式（端口 5173，自动代理 /api 到后端 8781）
npm run dev

# 生产构建（输出到 static/vue/，由后端托管）
npm run build

# 仅类型检查
npm run typecheck
```

## 接口对接

所有接口集中在 `src/api/client.ts`，走 `/api` 前缀（Vite dev 代理转发到 `http://127.0.0.1:8781`）。

| 前端页面 | 使用的后端接口 |
|----------|----------------|
| Dashboard 提交 | `POST /api/ingest` + `GET /api/tasks/{id}`（异步进度轮询） |
| Dashboard 指标 | `GET /api/dashboard` |
| Predictions 列表 | `GET /api/predictions` |
| Predictions 确认 | `POST /api/predictions/{id}/review` |
| Predictions 复核 | `POST /api/verification/{id}/human` |
| 创作者画像 | `GET /api/creators` |
| 通知中心 | `GET /api/notifications` + `POST /api/notifications/read` |

### 后端字段约定

`GET /api/predictions` 返回的 `subject` / `magnitude` / `time_window` / `conditions` 为 **JSON 字符串**（数据库序列化），前端用 `JSON.parse` 解析后展示（见 `Predictions.vue` 的 `parseJsonField`）。

## 生产部署

`npm run build` 产物输出到 `static/vue/`，后端 FastAPI 已挂载 `static` 目录，可直接访问 `http://127.0.0.1:8781/vue/index.html`（或配置路由指向该入口）。
