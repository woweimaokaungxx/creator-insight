// 系统设置表单 schema（纯数据，供 Settings.vue 渲染）
export type FieldType = 'text' | 'password' | 'number' | 'switch' | 'select' | 'multi';

export interface FieldDef {
  path: string[];              // 相对 section 的配置路径
  label: string;
  type: FieldType;
  options?: Array<{ label: string; value: string | number }>;
  placeholder?: string;
  tip?: string;
  secretKey?: string;          // 对应 GET /api/settings 的 secrets_configured key
}

export interface GroupDef {
  title: string;
  section: string;
  desc?: string;
  fields: FieldDef[];
}

const MODE_OPTIONS = [
  { label: '云端', value: 'cloud' },
  { label: '本地', value: 'local' },
];

const TASK_LABELS: Array<[string, string]> = [
  ['summarize', '总结'],
  ['extract_claim', '观点抽取'],
  ['extract_prediction', '预测抽取'],
  ['parse_time', '时间解析'],
  ['verify', '预测验证'],
  ['simplify', '简体校对'],
];

export const SETTINGS_GROUPS: GroupDef[] = [
  {
    title: 'AI 云端',
    section: 'ai',
    desc: '主力模型（OpenAI 兼容协议 + Bearer 鉴权）。密钥留空表示不修改；保存后可点「测试 AI 连接」验证。',
    fields: [
      { path: ['cloud', 'base_url'], label: 'Base URL', type: 'text', placeholder: 'https://api.xiaomimimo.com/v1' },
      { path: ['cloud', 'model'], label: '模型', type: 'text', placeholder: 'mimo-v2.5' },
      {
        path: ['cloud', 'api_key'], label: 'API Key', type: 'password',
        secretKey: 'ai.cloud.api_key', placeholder: '留空表示不修改',
      },
      { path: ['cloud', 'max_tokens'], label: '单次最大输出 tokens', type: 'number' },
      {
        path: ['daily_call_limit'], label: '每日调用上限', type: 'number',
        tip: '0 = 不限制；达到上限后 AI 调用会被拒绝（成本控制）',
      },
      {
        path: ['daily_call_warn'], label: '用量告警阈值', type: 'number',
        tip: '0 = 不告警；当日调用达到该次数时推送一条通知',
      },
    ],
  },
  {
    title: 'AI 本地兜底',
    section: 'ai',
    desc: '云端不可用时回退本地 Ollama（需本地已运行对应的模型）。',
    fields: [
      { path: ['local_fallback', 'enabled'], label: '启用本地兜底', type: 'switch' },
      { path: ['local_fallback', 'base_url'], label: 'Ollama 地址', type: 'text' },
      { path: ['local_fallback', 'model'], label: '本地模型', type: 'text' },
    ],
  },
  {
    title: '任务模型路由',
    section: 'ai',
    desc: '每个 AI 任务走云端还是本地。',
    fields: TASK_LABELS.map(([key, label]) => ({
      path: ['task_model_map', key],
      label,
      type: 'select' as FieldType,
      options: MODE_OPTIONS,
    })),
  },
  {
    title: '搜索（证据收集）',
    section: 'search',
    desc: '验证阶段联网收集证据用。未配置 Tavily 时仍可手工添加证据。',
    fields: [
      {
        path: ['provider'], label: '搜索提供方', type: 'select',
        options: [
          { label: 'Tavily', value: 'tavily' },
          { label: 'Bing', value: 'bing' },
        ],
      },
      {
        path: ['tavily_api_key'], label: 'Tavily API Key', type: 'password',
        secretKey: 'search.tavily_api_key', placeholder: '留空表示不修改',
      },
      {
        path: ['fallback'], label: '兜底搜索源', type: 'select',
        options: [
          { label: 'Bing', value: 'bing' },
          { label: '无', value: '' },
        ],
      },
      { path: ['max_results'], label: '单次结果数', type: 'number' },
    ],
  },
  {
    title: '通知',
    section: 'notify',
    desc: '站内（local）/ Webhook / 邮件三通道，可多选。',
    fields: [
      { path: ['enabled'], label: '启用通知', type: 'switch' },
      {
        path: ['channels'], label: '启用通道', type: 'multi',
        options: [
          { label: '站内', value: 'local' },
          { label: 'Webhook', value: 'webhook' },
          { label: '邮件', value: 'email' },
        ],
      },
      {
        path: ['webhook_url'], label: 'Webhook 地址', type: 'password',
        secretKey: 'notify.webhook_url', placeholder: '留空表示不修改',
      },
      { path: ['smtp', 'host'], label: 'SMTP 主机', type: 'text' },
      { path: ['smtp', 'port'], label: 'SMTP 端口', type: 'number' },
      { path: ['smtp', 'user'], label: 'SMTP 用户名', type: 'text' },
      {
        path: ['smtp', 'password'], label: 'SMTP 密码', type: 'password',
        secretKey: 'notify.smtp.password', placeholder: '留空表示不修改',
      },
      { path: ['smtp', 'from_name'], label: '发件人名称', type: 'text' },
      { path: ['smtp', 'to'], label: '收件人', type: 'text' },
      { path: ['smtp', 'use_ssl'], label: '使用 SSL', type: 'switch' },
    ],
  },
  {
    title: 'Obsidian',
    section: 'obsidian',
    desc: '只写验证报告与关键预测摘要；`AUTO` 区由软件维护，`HUMAN` 区不会被覆盖。',
    fields: [
      { path: ['vault_path'], label: 'Vault 路径', type: 'text', placeholder: '如 H:/Obsidian/MyVault' },
      { path: ['root_folder'], label: '根目录名', type: 'text' },
      { path: ['write_enabled'], label: '启用写出', type: 'switch' },
      { path: ['sync_predictions'], label: '同步预测摘要', type: 'switch' },
      { path: ['sync_verifications'], label: '同步验证报告', type: 'switch' },
    ],
  },
  {
    title: '抓取监控',
    section: 'monitor',
    desc: '关注博主的定期增量抓取参数。抖音登录态在「自动监控」页顶部设置。',
    fields: [
      { path: ['enabled'], label: '启用监控调度', type: 'switch' },
      { path: ['max_pages'], label: '单次最多页数', type: 'number' },
      { path: ['per_page'], label: '每页条数', type: 'number' },
    ],
  },
  {
    title: '验证与统计',
    section: 'verification',
    desc: '到期扫描、硬预测自动过与样本量门槛。',
    fields: [
      { path: ['auto_apply'], label: '硬预测自动过', type: 'switch' },
      { path: ['auto_apply_min_confidence'], label: '自动过置信度门槛', type: 'number' },
      { path: ['auto_apply_undo_hours'], label: '撤销窗口（小时）', type: 'number' },
      { path: ['human_review_required'], label: '强制人工复核', type: 'switch' },
      { path: ['sample_size_threshold'], label: '样本量门槛', type: 'number' },
      { path: ['scheduler_enabled'], label: '启用到期扫描调度', type: 'switch' },
      { path: ['scheduler_interval_sec'], label: '扫描间隔（秒）', type: 'number' },
    ],
  },
  {
    title: '应用',
    section: 'app',
    desc: '主机 / 端口 / 数据目录的改动需重启服务才生效。',
    fields: [
      { path: ['host'], label: '监听地址', type: 'text', tip: '重启后生效' },
      { path: ['port'], label: '端口', type: 'number', tip: '重启后生效' },
      { path: ['data_dir'], label: '数据目录', type: 'text', tip: '重启后生效' },
      {
        path: ['log_level'], label: '日志级别', type: 'select',
        options: [
          { label: 'INFO', value: 'INFO' },
          { label: 'DEBUG', value: 'DEBUG' },
          { label: 'WARNING', value: 'WARNING' },
          { label: 'ERROR', value: 'ERROR' },
        ],
      },
    ],
  },
];
