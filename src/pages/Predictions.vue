<template>
  <div class="predictions-page">
    <t-card bordered class="filter-card">
      <div class="filter-bar">
        <t-tabs :value="activeStatus" theme="normal" @change="handleTabChange">
          <t-tab-panel value="pending" label="待确认" />
          <t-tab-panel value="verifying" label="验证队列" />
          <t-tab-panel value="history" label="历史预测" />
          <t-tab-panel value="all" label="全部预测" />
        </t-tabs>

        <div class="filter-actions">
          <t-select v-model="filters.creator" placeholder="博主" clearable class="filter-select" @change="loadGroups">
            <t-option v-for="name in creatorOptions" :key="name" :value="name" :label="name" />
          </t-select>
          <t-input v-model="filters.keyword" placeholder="搜索视频标题/预测对象/预测内容" clearable class="filter-input" @enter="loadGroups" />
          <t-button variant="outline" @click="loadGroups">刷新</t-button>
        </div>
      </div>
    </t-card>

    <t-card bordered class="table-card">
      <template #title>{{ tableTitle }}</template>
      <template #actions>
        <t-space>
          <t-tag theme="warning" variant="light">待确认 {{ counts.pending }}</t-tag>
          <t-tag theme="primary" variant="light">验证中 {{ counts.verifying }}</t-tag>
          <t-tag theme="success" variant="light">已锁定 {{ counts.locked }}</t-tag>
        </t-space>
      </template>

      <!-- V0.9 视频分组视图 -->
      <div v-if="loading" class="group-loading">
        <t-loading :loading="true" size="small" text="加载中…" />
      </div>
      <t-empty v-else-if="!filteredGroups.length" title="暂无匹配的视频"
               description="当前筛选条件下没有包含预测的视频" />

      <div v-else class="group-list">
        <div v-for="group in filteredGroups" :key="group.contentId" class="video-card"
             :class="{ expanded: isExpanded(group.contentId) }">
          <!-- 视频行 -->
          <div class="video-row" @click="toggleExpand(group.contentId)">
            <span class="expand-icon">{{ isExpanded(group.contentId) ? '▾' : '▸' }}</span>
            <div class="video-main">
              <div class="video-title-row">
                <strong class="video-title" :title="group.title">{{ group.title || '（无标题）' }}</strong>
                <t-tag v-if="group.platform" theme="default" variant="light" size="small">{{ platformLabel(group.platform) }}</t-tag>
                <t-tag theme="default" variant="light" size="small">共 {{ group.rows.length }} 条预测</t-tag>
              </div>
              <div class="video-meta">
                <span v-if="group.creator">{{ group.creator }}</span>
                <span v-if="group.fetchedAtText">抓取于 {{ group.fetchedAtText }}</span>
                <a v-if="group.url" :href="group.url" target="_blank" @click.stop>原视频 ↗</a>
              </div>
            </div>
            <div class="video-status">
              <t-tag v-if="group.statusCount.pending" theme="warning" variant="light">待确认 {{ group.statusCount.pending }}</t-tag>
              <t-tag v-if="group.statusCount.verifying" theme="primary" variant="light">验证中 {{ group.statusCount.verifying }}</t-tag>
              <t-tag v-if="group.statusCount.locked" theme="success" variant="light">已锁定 {{ group.statusCount.locked }}</t-tag>
            </div>
          </div>

          <!-- 展开区：预测子表 + 观点 -->
          <div v-if="isExpanded(group.contentId)" class="video-detail">
            <div class="detail-section-title">预测明细（{{ group.rows.length }}）</div>
            <t-table
              v-if="group.rows.length"
              row-key="id"
              :data="group.rows"
              :columns="subColumns"
              size="small"
              hover
            >
              <template #prediction="{ row }">
                <div class="prediction-cell">
                  <div class="prediction-title">
                    <strong>{{ row.prediction }}</strong>
                    <t-tag v-if="!row.summarized" theme="warning" variant="light" size="small">未总结</t-tag>
                  </div>
                  <span v-if="row.rawText" class="raw-quote" :title="row.rawText">原话：{{ row.rawText }}</span>
                </div>
              </template>
              <template #status="{ row }">
                <t-tag :theme="statusMetaFor(row.status).theme" variant="light">
                  {{ statusMetaFor(row.status).label }}
                </t-tag>
              </template>
              <template #confidence="{ row }">
                <div class="confidence-cell">
                  <t-progress
                    :percentage="row.confidence"
                    theme="circle"
                    size="small"
                    :color="row.confidence >= 80 ? '#0f766e' : '#1d4ed8'"
                  />
                  <span>{{ row.aiVerdict }}</span>
                </div>
              </template>
              <template #operation="{ row }">
                <t-space>
                  <t-link v-if="row.status === 'pending'" theme="primary" hover="color" @click="openConfirmDrawer(row)">
                    确认
                  </t-link>
                  <t-link v-if="row.status === 'verifying'" theme="primary" hover="color" @click="openReviewDialog(row)">
                    复核
                  </t-link>
                  <t-popconfirm content="确定删除该预测？此操作会同时删除其验证记录、证据和语义索引。" @confirm="removePrediction(row.id)">
                    <t-link theme="danger" hover="color">删除</t-link>
                  </t-popconfirm>
                </t-space>
              </template>
            </t-table>

            <div class="detail-section-title">全部观点（{{ group.claims.length }}）</div>
            <div v-if="group.claimsLoading" class="claims-loading">
              <t-loading :loading="true" size="small" text="观点加载中…" />
            </div>
            <t-empty v-else-if="!group.claimsLoaded" title="加载观点失败" description="点击展开重试" />
            <t-empty v-else-if="!group.claims.length" title="暂无观点" />
            <div v-else class="claims-list">
              <div v-for="(c, idx) in group.claims" :key="idx" class="claim-item" :class="{ 'claim-top': (c.importance ?? 0) >= 0.8 }">
                <div class="claim-main">
                  <span v-if="c.key_phrase" class="claim-phrase">{{ c.key_phrase }}</span>
                  <span class="claim-text">{{ c.text }}</span>
                </div>
                <div class="claim-meta">
                  <t-tag v-if="c.category" theme="default" variant="light" size="small">{{ categoryLabel(c.category) }}</t-tag>
                  <t-tag v-if="c.topic" theme="default" variant="outline" size="small">{{ c.topic }}</t-tag>
                  <t-tag v-if="c.importance" theme="warning" variant="light" size="small">重要 {{ Math.round((c.importance ?? 0) * 100) }}%</t-tag>
                  <span v-if="c.support" class="claim-support" :title="c.support">论据：{{ c.support }}</span>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </t-card>

    <t-drawer
      v-model:visible="confirmDrawerVisible"
      header="人工确认预测"
      size="480px"
      :footer="false"
    >
      <div v-if="selectedRow" class="drawer-content">
        <t-alert theme="info" :message="selectedRow.prediction" :close="false">
          <template #default>
            <div v-if="selectedRow.rawText" class="drawer-raw">
              <span class="drawer-raw-label">博主原话：</span>{{ selectedRow.rawText }}
            </div>
          </template>
        </t-alert>
        <t-form label-align="top">
          <t-form-item label="是否为有效预测">
            <t-switch v-model="confirmForm.valid" :label="['有效', '无效']" />
          </t-form-item>
          <t-form-item label="补填到期时间">
            <t-date-picker v-model="confirmForm.dueAt" enable-time-picker clearable />
          </t-form-item>
          <t-form-item label="人工备注">
            <t-textarea v-model="confirmForm.note" :autosize="{ minRows: 4, maxRows: 7 }" />
          </t-form-item>
        </t-form>
        <div class="drawer-footer">
          <t-button variant="outline" @click="confirmDrawerVisible = false">取消</t-button>
          <t-button theme="primary" @click="submitConfirm">提交确认</t-button>
        </div>
      </div>
    </t-drawer>

    <t-dialog
      v-model:visible="reviewDialogVisible"
      header="人工复核 AI 判定"
      width="620px"
      confirm-btn="锁定结果"
      cancel-btn="稍后处理"
      @confirm="submitReview"
    >
      <div v-if="selectedRow" class="review-dialog">
        <t-descriptions bordered :column="1" size="small">
          <t-descriptions-item label="预测内容">{{ selectedRow.prediction }}</t-descriptions-item>
          <t-descriptions-item label="AI 证据摘要">{{ selectedRow.evidence }}</t-descriptions-item>
          <t-descriptions-item label="AI 置信度">{{ selectedRow.confidence }}%</t-descriptions-item>
        </t-descriptions>

        <t-radio-group v-model="reviewForm.result" variant="default-filled" class="verdict-group">
          <t-radio-button value="correct">正确</t-radio-button>
          <t-radio-button value="partial">部分正确</t-radio-button>
          <t-radio-button value="wrong">错误</t-radio-button>
          <t-radio-button value="unclear">无法判断</t-radio-button>
          <t-radio-button value="invalid">预测无效</t-radio-button>
        </t-radio-group>
      </div>
    </t-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue';
import {
  deletePrediction,
  getContentClaims,
  getPredictions,
  reviewPrediction,
  submitHumanReview,
  type ClaimRow,
  type Prediction,
} from '../api/client';

type PredictionStatus = 'pending' | 'verifying' | 'history' | 'locked';
type TabStatus = PredictionStatus | 'all';
type TagTheme = 'default' | 'primary' | 'success' | 'warning' | 'danger';

interface PredictionRow {
  id: string;
  creator: string;
  prediction: string;
  summarized: boolean;   // 是否有 AI 总结（老数据为 false → 仅显示原话）
  rawText: string;
  source: string;
  target: string;
  createdAt: string;
  dueAt: string;
  status: PredictionStatus;
  confidence: number;
  aiVerdict: string;
  result: string;
  evidence: string;
}

// V0.9 视频分组
interface VideoGroup {
  contentId: string;
  title: string;
  url: string;
  platform: string;
  creator: string;
  fetchedAt: number;
  fetchedAtText: string;
  rows: PredictionRow[];
  claims: ClaimRow[];
  claimsLoading: boolean;
  claimsLoaded: boolean;
}

const props = defineProps<{
  initialStatus?: string;
}>();

const emit = defineEmits<{
  'status-change': [status: string];
}>();

const activeStatus = ref<TabStatus>(normalizeStatus(props.initialStatus));
const selectedRow = ref<PredictionRow | null>(null);
const confirmDrawerVisible = ref(false);
const reviewDialogVisible = ref(false);
const loading = ref(false);

const filters = reactive({
  creator: '',
  keyword: '',
});

const confirmForm = reactive({
  valid: true,
  dueAt: '',
  note: '',
});

const reviewForm = reactive({
  result: 'correct',
});

const groups = ref<VideoGroup[]>([]);
const expandedIds = reactive(new Set<string>());

const STATUS_MAP: Record<string, PredictionStatus> = {
  pending_review: 'pending',
  active: 'verifying',
  due: 'verifying',
  human_review: 'verifying',
  final: 'locked',
  invalid: 'history',
};

const SUBJECT_TYPE_CN: Record<string, string> = {
  stock: '个股',
  index: '指数',
  fx: '外汇',
  commodity: '商品',
  macro_indicator: '宏观',
  policy_event: '政策',
  company_event: '公司',
  unknown: '',
};

const CATEGORY_CN: Record<string, string> = {
  opinion: '观点',
  analysis: '分析',
  context: '背景',
  recommendation: '建议',
};

function categoryLabel(category: string): string {
  return CATEGORY_CN[category] ?? category;
}

function platformLabel(platform: string): string {
  return platform === 'bilibili' ? 'B站' : platform === 'douyin' ? '抖音' : platform;
}

function toTabStatus(status: string): PredictionStatus {
  return STATUS_MAP[status] ?? 'pending';
}

function parseJsonField<T>(value: unknown, fallback: T): T {
  if (typeof value === 'string') {
    try {
      return JSON.parse(value) as T;
    } catch {
      return fallback;
    }
  }
  return (value as T) ?? fallback;
}

function formatTarget(subject: { type?: string; symbol?: string; name?: string }): string {
  const typeCn = SUBJECT_TYPE_CN[subject.type ?? ''] ?? '';
  const name = subject.name?.trim();
  const symbol = subject.symbol?.trim();
  const label = name || symbol || '';
  if (!label && !typeCn) return '-';
  return typeCn && label ? `${typeCn}·${label}` : (label || typeCn);
}

function toRow(p: Prediction): PredictionRow {
  const subject = parseJsonField<{ type?: string; symbol?: string; name?: string }>(p.subject, {});
  const timeWindow = parseJsonField<{ raw?: string }>(p.time_window, {});
  const aiVerdict = (p.ai_verdict as string) || '';
  const summary = (p.interpreted_intent as string) || '';
  const raw = p.raw_text || '';
  return {
    id: p.id,
    creator: (p.creator_name as string) || '未知',
    prediction: summary || raw,
    summarized: !!summary,
    rawText: summary ? raw : '',
    source: subject.type || '',
    target: formatTarget(subject),
    createdAt: p.created_at ? new Date(p.created_at * 1000).toLocaleString() : '-',
    dueAt: p.due_at ? new Date(p.due_at * 1000).toLocaleString() : '无法判断',
    status: toTabStatus(p.status),
    confidence: Math.round((p.confidence_score ?? 0) * 100),
    aiVerdict: aiVerdict || (p.status === 'pending_review' ? '待抽取' : p.status),
    result: p.status === 'final' ? '已锁定' : '-',
    evidence: timeWindow.raw || raw || '',
  };
}

async function loadGroups() {
  loading.value = true;
  try {
    const list: Prediction[] = await getPredictions();
    const rawMap = new Map<string, { row: PredictionRow; pred: Prediction }[]>();
    for (const p of list) {
      const cid = p.content_id || '';
      if (!cid) continue;
      if (!rawMap.has(cid)) rawMap.set(cid, []);
      rawMap.get(cid)!.push({ row: toRow(p), pred: p });
    }
    const newGroups: VideoGroup[] = [];
    for (const [cid, items] of rawMap) {
      const first = items[0].pred;
      const fetchedAt = first.content_fetched_at ?? first.created_at ?? 0;
      newGroups.push({
        contentId: cid,
        title: first.content_title || '',
        url: first.content_url || '',
        platform: first.content_platform || '',
        creator: first.creator_name || '未知',
        fetchedAt,
        fetchedAtText: fetchedAt ? new Date(fetchedAt * 1000).toLocaleString() : '-',
        rows: items.map((x) => x.row),
        claims: [],
        claimsLoading: false,
        claimsLoaded: false,
      });
    }
    // 视频行排序：按组内最新 created_at 降序（后端已按 p.created_at DESC，首条即最新）
    groups.value = newGroups;
  } catch (e) {
    console.error('加载预测失败', e);
    groups.value = [];
  } finally {
    loading.value = false;
  }
}

// 展开 / 收起 + 懒加载观点
function isExpanded(contentId: string): boolean {
  return expandedIds.has(contentId);
}

function toggleExpand(contentId: string) {
  if (expandedIds.has(contentId)) {
    expandedIds.delete(contentId);
    return;
  }
  expandedIds.add(contentId);
  const group = groups.value.find((g) => g.contentId === contentId);
  if (group && !group.claimsLoaded && !group.claimsLoading) {
    loadClaims(group);
  }
}

async function loadClaims(group: VideoGroup) {
  group.claimsLoading = true;
  try {
    group.claims = await getContentClaims(group.contentId);
    group.claimsLoaded = true;
  } catch (e) {
    console.error('加载观点失败', e);
    group.claimsLoaded = false;
  } finally {
    group.claimsLoading = false;
  }
}

const creatorOptions = computed(() => {
  const names = new Set<string>();
  groups.value.forEach((g) => g.creator && names.add(g.creator));
  return Array.from(names);
});

// tab 状态集合
const TAB_STATUS_SETS: Record<TabStatus, PredictionStatus[]> = {
  pending: ['pending'],
  verifying: ['verifying'],
  history: ['history', 'locked'],
  locked: ['locked'],
  all: ['pending', 'verifying', 'history', 'locked'],
};

const counts = computed(() => {
  let pending = 0;
  let verifying = 0;
  let locked = 0;
  groups.value.forEach((g) => {
    pending += g.rows.filter((r) => r.status === 'pending').length;
    verifying += g.rows.filter((r) => r.status === 'verifying').length;
    locked += g.rows.filter((r) => r.status === 'locked' || r.status === 'history').length;
  });
  return { pending, verifying, locked };
});

// 过滤：tab 状态集合 + 博主 + 关键词
const filteredGroups = computed(() => {
  const allowed = new Set(TAB_STATUS_SETS[activeStatus.value] ?? TAB_STATUS_SETS.all);
  const keyword = filters.keyword.trim().toLowerCase();
  return groups.value
    .filter((g) => {
      const hasStatus = g.rows.some((r) => allowed.has(r.status));
      if (!hasStatus) return false;
      if (filters.creator && g.creator !== filters.creator) return false;
      if (keyword) {
        const hitTitle = g.title.toLowerCase().includes(keyword);
        const hitRow = g.rows.some(
          (r) => r.prediction.toLowerCase().includes(keyword) || r.target.toLowerCase().includes(keyword),
        );
        if (!hitTitle && !hitRow) return false;
      }
      return true;
    })
    .map((g) => ({
      ...g,
      statusCount: {
        pending: g.rows.filter((r) => r.status === 'pending').length,
        verifying: g.rows.filter((r) => r.status === 'verifying').length,
        locked: g.rows.filter((r) => r.status === 'locked').length,
      },
    }));
});

const tableTitle = computed(() => {
  const titleMap: Record<TabStatus, string> = {
    pending: '待确认预测（按视频分组）',
    verifying: '验证队列（按视频分组）',
    history: '历史预测（按视频分组）',
    locked: '已锁定预测（按视频分组）',
    all: '全部预测（按视频分组）',
  };
  return titleMap[activeStatus.value];
});

const subColumns = [
  { colKey: 'prediction', title: '预测内容', minWidth: 300 },
  { colKey: 'target', title: '预测对象', width: 110 },
  { colKey: 'dueAt', title: '到期时间', width: 160 },
  { colKey: 'status', title: '状态', width: 90 },
  { colKey: 'confidence', title: 'AI置信度', width: 130 },
  { colKey: 'result', title: '判定结果', width: 90 },
  { colKey: 'operation', title: '操作', width: 130, fixed: 'right' },
];

const statusMeta: Record<PredictionStatus, { label: string; theme: TagTheme }> = {
  pending: { label: '待确认', theme: 'warning' },
  verifying: { label: '验证中', theme: 'primary' },
  history: { label: '已结束', theme: 'success' },
  locked: { label: '已锁定', theme: 'success' },
};

function statusMetaFor(status: unknown): { label: string; theme: TagTheme } {
  const key = (status as PredictionStatus) in statusMeta ? (status as PredictionStatus) : 'pending';
  return statusMeta[key];
}

watch(
  () => props.initialStatus,
  (value) => {
    activeStatus.value = normalizeStatus(value);
  },
);

function normalizeStatus(value?: string): TabStatus {
  if (value === 'pending' || value === 'verifying' || value === 'history' || value === 'locked') return value;
  return 'all';
}

function handleTabChange(value: string | number) {
  activeStatus.value = normalizeStatus(String(value));
  emit('status-change', activeStatus.value);
}

function openConfirmDrawer(row: PredictionRow) {
  selectedRow.value = row;
  confirmForm.valid = true;
  confirmForm.dueAt = row.dueAt && row.dueAt !== '-' ? row.dueAt : '';
  confirmForm.note = '';
  confirmDrawerVisible.value = true;
}

async function submitConfirm() {
  if (!selectedRow.value) return;
  try {
    const dueAt = confirmForm.dueAt ? new Date(confirmForm.dueAt).getTime() / 1000 : null;
    await reviewPrediction(selectedRow.value.id, confirmForm.valid ? 'active' : 'invalid', dueAt, confirmForm.note);
    confirmDrawerVisible.value = false;
    await loadGroups();
  } catch (e) {
    console.error('确认失败', e);
  }
}

function openReviewDialog(row: PredictionRow) {
  selectedRow.value = row;
  reviewForm.result = 'correct';
  reviewDialogVisible.value = true;
}

async function submitReview() {
  if (!selectedRow.value) return;
  const verdictMap: Record<string, string> = {
    correct: 'correct',
    partial: 'partial',
    wrong: 'incorrect',
    unclear: 'inconclusive',
    invalid: 'invalid',
  };
  try {
    await submitHumanReview(selectedRow.value.id, verdictMap[reviewForm.result] ?? 'correct', '前端复核');
    reviewDialogVisible.value = false;
    await loadGroups();
  } catch (e) {
    console.error('复核失败', e);
  }
}

async function removePrediction(id: string) {
  try {
    await deletePrediction(id);
    await loadGroups();
  } catch (e) {
    console.error('删除预测失败', e);
  }
}

onMounted(loadGroups);
</script>

<style scoped>
.predictions-page {
  color: #101828;
}

.filter-card,
.table-card {
  border-radius: 8px;
}

.table-card {
  margin-top: 20px;
}

.filter-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
}

.filter-actions {
  display: flex;
  align-items: center;
  gap: 12px;
}

.filter-select {
  width: 180px;
}

.filter-input {
  width: 260px;
}

.group-loading {
  padding: 40px;
  text-align: center;
}

.group-list {
  display: grid;
  gap: 12px;
}

.video-card {
  border: 1px solid #edf1f7;
  border-radius: 10px;
  background: #fff;
  transition: box-shadow 0.2s ease;
}

.video-card:hover {
  box-shadow: 0 2px 10px rgba(16, 24, 40, 0.06);
}

.video-card.expanded {
  border-color: #b3c7f5;
  box-shadow: 0 2px 12px rgba(29, 78, 216, 0.08);
}

.video-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 14px 16px;
  cursor: pointer;
  user-select: none;
}

.expand-icon {
  color: #98a2b3;
  font-size: 14px;
  flex: 0 0 auto;
}

.video-main {
  flex: 1;
  min-width: 0;
}

.video-title-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.video-title {
  color: #101828;
  font-size: 14px;
  line-height: 20px;
  max-width: 560px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.video-meta {
  display: flex;
  gap: 14px;
  margin-top: 4px;
  color: #98a2b3;
  font-size: 12px;
  flex-wrap: wrap;
}

.video-meta a {
  color: #1d4ed8;
  text-decoration: none;
}

.video-status {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  justify-content: flex-end;
  flex: 0 0 auto;
}

.video-detail {
  border-top: 1px solid #f2f4f7;
  padding: 16px;
  background: #fbfcfe;
  border-radius: 0 0 10px 10px;
}

.detail-section-title {
  color: #344054;
  font-weight: 600;
  font-size: 13px;
  margin-bottom: 10px;
}

.detail-section-title:not(:first-child) {
  margin-top: 20px;
}

.claims-loading {
  padding: 20px;
  text-align: center;
}

.claims-list {
  display: grid;
  gap: 8px;
}

.claim-item {
  padding: 10px 12px;
  background: #fff;
  border: 1px solid #f2f4f7;
  border-radius: 8px;
}

.claim-item.claim-top {
  border-left: 3px solid #e67700;
}

.claim-main {
  display: flex;
  gap: 8px;
  align-items: baseline;
}

.claim-phrase {
  color: #1d4ed8;
  font-weight: 600;
  flex: 0 0 auto;
}

.claim-text {
  color: #344054;
  font-size: 13px;
  line-height: 20px;
}

.claim-meta {
  display: flex;
  gap: 6px;
  align-items: center;
  margin-top: 6px;
  flex-wrap: wrap;
}

.claim-support {
  color: #98a2b3;
  font-size: 12px;
  max-width: 480px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.prediction-cell strong,
.prediction-cell span {
  display: block;
}

.prediction-title {
  display: flex;
  align-items: center;
  gap: 6px;
}

.prediction-title strong {
  color: #101828;
  line-height: 22px;
}

.prediction-cell span {
  margin-top: 4px;
  color: #667085;
  font-size: 12px;
}

.prediction-cell span.raw-quote {
  color: #98a2b3;
  font-size: 12px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 520px;
}

.confidence-cell {
  display: flex;
  align-items: center;
  gap: 10px;
}

.confidence-cell span {
  color: #667085;
  font-size: 12px;
}

.drawer-raw {
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px dashed #e4e7ec;
  color: #667085;
  font-size: 13px;
  line-height: 20px;
}

.drawer-raw-label {
  color: #98a2b3;
}

.drawer-content {
  display: grid;
  gap: 20px;
}

.drawer-footer {
  display: flex;
  justify-content: flex-end;
  gap: 12px;
  padding-top: 8px;
}

.review-dialog {
  display: grid;
  gap: 18px;
}

.verdict-group {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

@media (max-width: 1180px) {
  .filter-bar {
    align-items: stretch;
    flex-direction: column;
  }

  .filter-actions {
    justify-content: flex-start;
  }
}
</style>
