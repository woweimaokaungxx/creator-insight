// 后端 API 封装（对接 FastAPI，/api 由 Vite dev 代理或同源托管）
const BASE = '/api';

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      detail = body.detail ?? JSON.stringify(body);
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

// ─── 类型 ─────────────────────────────────────────────
export interface Prediction {
  id: string;
  raw_text: string;
  status: string;
  confidence_score: number | null;
  direction: string;
  time_expression_raw: string;
  creator_name?: string;
  created_at?: number;
  due_at: number | null;
  interpreted_intent?: string | null;
  ai_verdict?: string | null;
  ai_confidence?: number | null;
  ai_score?: number | null;
  content_id?: string;
  content_title?: string | null;
  content_fetched_at?: number | null;
  content_url?: string | null;
  content_platform?: string | null;
  subject?: unknown;         // JSON 字符串或对象
  time_window?: unknown;     // JSON 字符串或对象
  magnitude?: unknown;
  conditions?: unknown;
  [key: string]: unknown;
}

export interface IngestTask {
  id: string;
  status: 'pending' | 'running' | 'success' | 'error';
  stage: string;
  stage_progress: number;
  progress: number;
  message: string;
  error: string | null;
  created_at: number;
  finished_at: number | null;
  result: IngestResult | null;
  // V0.9 批量任务明细
  mode?: 'single' | 'all';
  total?: number;
  current_index?: number;
  items?: Array<{
    index: number;
    title: string;
    stage: string;
    stage_cn: string;
    status: string;
  }>;
}

export interface IngestResult {
  ok: boolean;
  task_id: string;
  content_id: string;
  creator: { name: string; id: string };
  title: string;
  platform: string;
  summary: unknown;
  claims: unknown[];
  predictions: Prediction[];
  transcript_source: string;
  transcript_simplified: string;
  saved_files: string[];
  [key: string]: unknown;
}

export interface DashboardCreator {
  id: string;
  name: string;
  platform_id: string;
  platform: string;
  avatar_url?: string | null;
  avatar_path?: string | null;
  reliability: Partial<CreatorReliability> | null;
  prediction_count?: number;
  calibration_points?: Array<[number, number]>;
  [key: string]: unknown;
}

export interface DashboardData {
  counts: Record<string, number>;
  verification_queue: number;
  updated_at: number;
  featured_creator?: DashboardCreator | null;
  [key: string]: unknown;
}

export interface CreatorRow {
  id: string;
  platform: string;
  platform_id: string;
  name: string;
  content_count: number;
  avatar_url?: string | null;   // 平台远程头像
  avatar_path?: string | null;  // 本地缓存路径
  [key: string]: unknown;
}

export interface CreatorReliability {
  verified_count: number;
  correct_count: number;
  incorrect_count: number;
  partial_count: number;
  base_accuracy: number | null;
  calibration_score: number | null;
  accuracy_by_domain: Record<string, { accuracy: number; n: number }>;
  accuracy_by_confidence_band: Record<string, { accuracy: number; n: number }>;
  sample_size_warning: string | null;
  [key: string]: unknown;
}

export interface CreatorProfile {
  id: string;
  name: string;
  platform: string;
  platform_id: string;
  reliability: CreatorReliability | null;
  predictions: Array<{
    id: string;
    raw_text: string;
    status: string;
    ai_verdict: string | null;
    final_verdict: string | null;
    confidence_score: number | null;
    calibration_point: [number, number] | null;
    [key: string]: unknown;
  }>;
  prediction_count: number;
  [key: string]: unknown;
}

export interface Subscription {
  id: string;
  platform: string;
  source_key: string;
  display_name: string;
  check_interval_hours: number;
  auto_process: number;
  enabled: number;
  next_check_at: number | null;
  last_result: { new_count?: number; error?: string } | null;
  baseline_count: number;
  content_count: number;
  last_task?: {
    status?: string;
    total_videos?: number;
    finished_at?: number | null;
    [key: string]: unknown;
  };
  [key: string]: unknown;
}

export interface NotificationRow {
  id: string;
  channel: string;
  category: string;
  title: string;
  body: string;
  status: string;
  created_at: number;
  read_at: number | null;
  [key: string]: unknown;
}

// ─── ingest / 任务 ────────────────────────────────────
export function submitIngest(input: string, useWhisper = true, mode: 'single' | 'all' = 'single') {
  return request<{ task_id: string; status: string }>('/ingest', {
    method: 'POST',
    body: JSON.stringify({ input, use_whisper: useWhisper, mode }),
  });
}

export function getTask(taskId: string) {
  return request<IngestTask>(`/tasks/${taskId}`);
}

export function getTasks(limit = 10) {
  return request<{ tasks: IngestTask[] }>(`/tasks?limit=${limit}`);
}

// ─── 预测 ─────────────────────────────────────────────
export function getPredictions(status = '', creatorId = '') {
  const params = new URLSearchParams();
  if (status) params.set('status', status);
  if (creatorId) params.set('creator_id', creatorId);
  const qs = params.toString();
  return request<Prediction[]>(`/predictions${qs ? `?${qs}` : ''}`);
}

export function getPrediction(id: string) {
  return request<Prediction>(`/predictions/${id}`);
}

export function deletePrediction(id: string) {
  return request<{ ok: boolean }>(`/predictions/${id}`, { method: 'DELETE' });
}

export function reviewPrediction(id: string, decision: string, dueAt: number | null, notes = '') {
  return request<{ ok: boolean }>(`/predictions/${id}/review`, {
    method: 'POST',
    body: JSON.stringify({ decision, due_at: dueAt, notes }),
  });
}

export function getVerificationQueue() {
  return request<unknown[]>(`/verification/queue`);
}

export function getVerification(id: string) {
  return request<unknown>(`/verification/${id}`);
}

export function runVerification(id: string) {
  return request<unknown>(`/verification/${id}/run`, { method: 'POST' });
}

export function submitHumanReview(
  id: string,
  humanVerdict: string,
  humanNotes = '',
  humanScore: number | null = null,
) {
  return request<unknown>(`/verification/${id}/human`, {
    method: 'POST',
    body: JSON.stringify({ human_verdict: humanVerdict, human_notes: humanNotes, human_score: humanScore }),
  });
}

export function undoAutoApply(id: string) {
  return request<unknown>(`/verification/${id}/undo-auto-apply`, { method: 'POST' });
}

// ─── 仪表盘 / 创作者 / 内容 ───────────────────────────
export function getDashboard() {
  return request<DashboardData>('/dashboard');
}

export function getCreators() {
  return request<CreatorRow[]>('/creators');
}

export function getCreatorProfile(id: string) {
  return request<CreatorProfile>(`/creators/${id}`);
}

export interface ContentItem {
  id: string;
  creator_id: string;
  creator_name?: string;
  platform: string;
  platform_vid: string;
  title: string;
  url: string;
  published_at: number | null;
  duration_sec: number | null;
  fetched_at: number;
  raw_meta: Record<string, unknown>;
  digg_count: number | null;
  comment_count: number | null;
  share_count: number | null;
  collect_count: number | null;
  [key: string]: unknown;
}

export function getContents(limit = 50) {
  return request<ContentItem[]>(`/contents?limit=${limit}`);
}

// V0.9 视频观点（claim）——供预测页分组展开展示
export interface ClaimRow {
  text: string;
  category?: string | null;
  topic?: string | null;
  stance?: string | null;
  importance?: number | null;
  confidence?: number | null;
  support?: string | null;
  key_phrase?: string | null;
  start_offset?: number | null;
  end_offset?: number | null;
  created_at?: number;
  [key: string]: unknown;
}

export function getContentClaims(contentId: string) {
  return request<ClaimRow[]>(`/contents/${contentId}/claims`);
}

// ─── 订阅监控 ─────────────────────────────────────────
export function getSubscriptions() {
  return request<Subscription[]>('/subscriptions');
}

export function addSubscription(input: string, platform = 'douyin', interval = 24, autoProcess = false) {
  return request<Subscription>('/subscriptions', {
    method: 'POST',
    body: JSON.stringify({ input, platform, check_interval_hours: interval, auto_process: autoProcess }),
  });
}

export function updateSubscription(id: string, patch: Record<string, unknown>) {
  return request<Subscription>(`/subscriptions/${id}`, {
    method: 'PATCH',
    body: JSON.stringify(patch),
  });
}

export function deleteSubscription(id: string) {
  return request<{ ok: boolean }>(`/subscriptions/${id}`, { method: 'DELETE' });
}

export function checkSubscription(id: string) {
  return request<Subscription>(`/subscriptions/${id}/check`, { method: 'POST' });
}

export function getSubscriptionVideos(id: string) {
  return request<unknown[]>(`/subscriptions/${id}/videos`);
}

export interface CrawlTask {
  id: string;
  subscription_id: string;
  display_name?: string;
  trigger: string;
  status: string;
  total_videos: number | null;
  new_video_ids: string[];
  error: string | null;
  created_at: number;
  finished_at: number | null;
  [key: string]: unknown;
}

export function getCrawlTasks(limit = 20) {
  return request<CrawlTask[]>(`/crawl_tasks?limit=${limit}`);
}

// ─── 通知 ─────────────────────────────────────────────
export function getNotifications(limit = 50, unreadOnly = false) {
  return request<{ items: NotificationRow[]; unread: number }>(
    `/notifications?limit=${limit}&unread_only=${unreadOnly}`,
  );
}

export function markNotificationsRead(id: string | null = null) {
  return request<{ ok: boolean }>('/notifications/read', {
    method: 'POST',
    body: JSON.stringify({ id }),
  });
}

export function clearNotifications() {
  return request<{ ok: boolean }>('/notifications', { method: 'DELETE' });
}

export function testNotification(title = '测试通知', body = '如果你能看到这条消息，说明通知通道工作正常。') {
  return request<{ ok: boolean }>('/notifications/test', {
    method: 'POST',
    body: JSON.stringify({ title, body }),
  });
}

// ─── 系统 ─────────────────────────────────────────────
export function getStatus() {
  return request<Record<string, unknown>>('/status');
}

export function runScheduler() {
  return request<{ ok: boolean }>('/scheduler/run', { method: 'POST' });
}

// ─── 语义检索（参考 douyin-creator-distill）──────────────
export interface SemanticResult {
  score: number;
  target_type: 'content' | 'prediction';
  target_id: string;
  title: string;
  url?: string;
  creator?: string;
  raw_text?: string;
  video_title?: string;      // prediction 命中时所属视频标题
  due_at?: number | null;    // prediction 到期时间
  content_id?: string;
  indexed_text?: string;
  digg_count?: number | null;
  comment_count?: number | null;
  [key: string]: unknown;
}

export function semanticSearch(query: string, limit = 20) {
  return request<{ query: string; model_id: string; results: SemanticResult[]; count: number }>(
    `/search?query=${encodeURIComponent(query)}&limit=${limit}`,
  );
}

export function semanticIndexContent() {
  return request<{ ok: boolean; indexed: number }>('/search/index', { method: 'POST', body: JSON.stringify({ all: true }) });
}

export function semanticIndexPredictions() {
  return request<{ ok: boolean; indexed: number }>('/search/index/predictions', { method: 'POST' });
}

export interface SemanticModel {
  id: string;
  label: string;
  installed: boolean;
  selected: boolean;
  dimension: number;
  approximateSize?: string;
  summary?: string;
  [key: string]: unknown;
}

export interface SemanticSettings {
  activeModel: string;
  modelRoot: string;
  runtime: Record<string, unknown>;
  models: SemanticModel[];
  indexedCount?: number;
  [key: string]: unknown;
}

export function getSemanticSettings() {
  return request<SemanticSettings>('/semantic/settings');
}

export function semanticSelectModel(modelId: string) {
  return request<SemanticSettings>(`/semantic/select?model_id=${encodeURIComponent(modelId)}`, { method: 'POST' });
}

export function semanticDownloadModel(modelId: string) {
  return request<SemanticSettings>(`/semantic/download?model_id=${encodeURIComponent(modelId)}`, { method: 'POST' });
}
