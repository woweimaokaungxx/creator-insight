<template>
  <div class="dashboard-page">
    <t-row :gutter="[20, 20]">
      <t-col :xs="12" :lg="7">
        <t-card bordered class="submit-card">
          <template #title>内容摄入</template>
          <template #actions>
            <t-tag theme="primary" variant="light">抖音 / B站</t-tag>
          </template>

          <t-form label-align="top">
            <t-form-item label="视频链接或分享文案">
              <t-textarea
                v-model="submitText"
                :autosize="{ minRows: 7, maxRows: 12 }"
                placeholder="粘贴抖音/B站视频链接，或完整分享文案。系统会自动识别平台、抓取字幕并提取可验证预测。"
              />
            </t-form-item>

            <t-form-item label="摄取范围">
              <t-radio-group v-model="ingestMode" variant="default-filled">
                <t-radio-button value="single">仅解析该视频</t-radio-button>
                <t-radio-button value="all">解析该博主全部视频</t-radio-button>
              </t-radio-group>
              <div class="mode-hint" style="width:100%;margin-top:6px;color:#98a2b3;font-size:12px;">
                {{ ingestMode === 'all' ? '将批量抓取该博主主页全部视频并逐个解析，耗时较长。' : '仅处理当前链接对应的单条视频。' }}
              </div>
            </t-form-item>

            <div class="submit-actions">
              <div class="platform-result">
                <t-tag :theme="detectedPlatform.theme" variant="light">
                  {{ detectedPlatform.label }}
                </t-tag>
                <span>{{ detectedPlatform.hint }}</span>
              </div>
              <t-button theme="primary" size="large" :loading="isSubmitting" @click="startSubmit">
                提交解析
              </t-button>
            </div>
          </t-form>

          <div v-if="activeTask" class="task-progress">
            <div class="task-title">
              <span>{{ activeTask.title }}</span>
              <t-tag theme="primary" variant="light">{{ activeTask.stage }}</t-tag>
            </div>
            <t-progress :percentage="activeTask.progress" theme="line" status="active" />
            <div v-if="batchItems.length" class="batch-summary">
              批量处理：第 {{ activeTask.currentIndex || 1 }} / {{ batchItems.length }} 条
              <span v-if="batchStats.done > 0">· 已完成 {{ batchStats.done }}</span>
              <span v-if="batchStats.failed > 0" style="color:#d54941;">· 失败 {{ batchStats.failed }}</span>
            </div>

            <!-- V0.9 批量明细列表（V0.13：completed / failed 状态 + 失败原因悬停） -->
            <div v-if="batchItems.length" class="batch-list">
              <div
                v-for="item in batchItems"
                :key="item.index"
                class="batch-item"
                :class="{ current: item.status === 'running', done: item.status === 'completed', error: item.status === 'failed' }"
              >
                <span class="batch-index">{{ item.index }}</span>
                <span class="batch-title" :title="item.error || item.title">{{ item.title }}</span>
                <t-tag
                  :theme="itemTheme(item.status, item.stage)"
                  variant="light"
                  size="small"
                  :title="item.error || ''"
                >
                  {{ item.stage_cn || item.stage || '等待中' }}
                </t-tag>
              </div>
            </div>

            <!-- V0.13 任务控制（docs/23 §8）：暂停 / 继续剩余 / 重试失败项 -->
            <div v-if="canPause || canResume || canRetryFailed" class="task-actions">
              <t-button
                v-if="canPause"
                size="small"
                variant="outline"
                :loading="controlling"
                @click="handlePause"
              >
                暂停
              </t-button>
              <t-button
                v-if="canResume"
                size="small"
                theme="primary"
                variant="outline"
                :loading="controlling"
                @click="handleResume"
              >
                继续剩余
              </t-button>
              <t-button
                v-if="canRetryFailed"
                size="small"
                theme="warning"
                variant="outline"
                :loading="controlling"
                @click="handleRetryFailed"
              >
                重试失败项（{{ retryableCount }}）
              </t-button>
              <span v-if="activeTaskHint" class="task-hint">{{ activeTaskHint }}</span>
            </div>
          </div>
        </t-card>
      </t-col>

      <t-col :xs="12" :lg="5">
        <div class="metric-grid">
          <t-card v-for="metric in metrics" :key="metric.label" bordered class="metric-card">
            <div class="metric-label">{{ metric.label }}</div>
            <div class="metric-value">{{ metric.value }}</div>
            <div class="metric-extra">{{ metric.extra }}</div>
          </t-card>
        </div>
      </t-col>
    </t-row>

    <t-row :gutter="[20, 20]" class="section-row">
      <t-col :xs="12" :xl="7">
        <ProcessingTasks :tasks="processingTasks" />
      </t-col>
      <t-col :xs="12" :xl="5">
        <CreatorCard v-if="featuredCreator" :creator="featuredCreator" />
        <t-card v-else bordered class="module-card">
          <template #title>创作者可信度画像</template>
          <t-empty title="暂无创作者画像" description="处理视频并完成验证后，这里展示样本最多的创作者" />
        </t-card>
      </t-col>
    </t-row>

    <t-row :gutter="[20, 20]" class="section-row">
      <t-col :xs="12" :xl="6">
        <ReliabilityBars :items="domainStats" />
      </t-col>
      <t-col :xs="12" :xl="6">
        <CalibrationChart :points="calibrationPoints" />
      </t-col>
    </t-row>
  </div>
</template>

<script setup lang="ts">
import {
  computed,
  defineComponent,
  h,
  nextTick,
  onBeforeUnmount,
  onMounted,
  type PropType,
  ref,
  resolveComponent,
  watch,
} from 'vue';
import * as echarts from 'echarts';
import { MessagePlugin } from 'tdesign-vue-next';
import {
  getDashboard,
  pauseTask,
  resumeTask,
  retryFailedItems,
  submitIngest,
  type DashboardData,
} from '../api/client';
import {
  isRunningStatus,
  stageToCn,
  taskStatusToCn,
  useIngestTasks,
} from '../composables/useIngestTasks';

type Theme = 'default' | 'primary' | 'success' | 'warning' | 'danger';

interface ProcessingTask {
  id: string;
  title: string;
  platform: string;
  stage: string;
  progress: number;
  status: Theme;
  currentIndex?: number;
}

// V0.9 批量任务子视频明细（V0.13：补失败原因与分类）
interface BatchItem {
  index: number;
  title: string;
  stage: string;
  stage_cn: string;
  status: string;
  item_id?: number;
  error?: string | null;
  error_class?: string | null;
  attempt_count?: number;
}

interface CreatorCardData {
  id: string;
  name: string;
  handle: string;
  avatarUrl: string;
  sampleSize: number;
  accuracy: number | null;
  brier: number | null;
  warning: string;
  hasStats: boolean;
}

const submitText = ref('');
const ingestMode = ref<'single' | 'all'>('single');
const isSubmitting = ref(false);

// V0.10 共享任务 store：切页/刷新后由 App 层持续轮询，这里只读展示
const { activeTask: storeTask, refresh, recentTasks } = useIngestTasks();
const activeTask = computed<ProcessingTask | null>(() => {
  const t = storeTask.value;
  if (!t) return null;
  return {
    id: t.id,
    title: t.result?.title || '解析任务',
    platform: t.mode === 'all' ? '批量' : (t.result?.platform || ''),
    stage: stageToCn(t.stage) || taskStatusToCn(t.status) || '处理中',
    progress: Math.round((t.progress ?? 0) * 100),
    status: t.status === 'failed' || t.status === 'waiting_for_action'
      ? 'danger'
      : t.status === 'paused' || t.status === 'partial'
        ? 'warning'
        : 'primary',
    currentIndex: t.current_index || 0,
  };
});

// ─── V0.13 任务控制（docs/23 §8）────────────────────────
const controlling = ref(false);

const canPause = computed(() => {
  const t = storeTask.value;
  return !!t && isRunningStatus(t.status);
});

const canResume = computed(() => {
  const t = storeTask.value;
  return !!t && (t.status === 'paused' || t.status === 'waiting_for_action');
});

const retryableCount = computed(() => {
  const t = storeTask.value;
  return t?.retryable_count ?? t?.failed_count ?? 0;
});

const canRetryFailed = computed(() => {
  const t = storeTask.value;
  return !!t && (t.status === 'partial' || t.status === 'failed') && retryableCount.value > 0;
});

const activeTaskHint = computed(() => {
  const t = storeTask.value;
  if (!t) return '';
  if (t.status === 'waiting_for_action') return '需人工处理（登录失效 / 验证码 / 限流 / 配额），处理后点「继续剩余」';
  if (t.status === 'paused') return '已安全暂停，点「继续剩余」处理未完成条目';
  if (t.status === 'partial') return '批次部分完成，可只重试失败项';
  return '';
});

async function handlePause() {
  const t = storeTask.value;
  if (!t) return;
  controlling.value = true;
  try {
    await pauseTask(t.id);
    MessagePlugin.success('已请求暂停，当前视频处理完后停止');
    await refresh();
  } catch (e) {
    MessagePlugin.error(e instanceof Error ? e.message : String(e));
  } finally {
    controlling.value = false;
  }
}

async function handleResume() {
  const t = storeTask.value;
  if (!t) return;
  controlling.value = true;
  try {
    await resumeTask(t.id);
    MessagePlugin.success('已继续处理剩余条目');
    await refresh();
  } catch (e) {
    MessagePlugin.error(e instanceof Error ? e.message : String(e));
  } finally {
    controlling.value = false;
  }
}

async function handleRetryFailed() {
  const t = storeTask.value;
  if (!t) return;
  controlling.value = true;
  try {
    const res = await retryFailedItems(t.id);
    MessagePlugin.success(`已重排 ${res.requeued} 条失败项`);
    await refresh();
  } catch (e) {
    MessagePlugin.error(e instanceof Error ? e.message : String(e));
  } finally {
    controlling.value = false;
  }
}

// 批量明细 items 从 store 的 active task 派生
const batchItems = computed<BatchItem[]>(() => {
  const items = storeTask.value?.items;
  return (items && Array.isArray(items) ? items : []) as BatchItem[];
});

const batchStats = computed(() => {
  const items = batchItems.value;
  return {
    done: items.filter((i) => i.status === 'completed').length,
    failed: items.filter((i) => i.status === 'failed').length,
    running: items.filter((i) => i.status === 'running').length,
  };
});

function itemTheme(status: string, stage: string): Theme {
  if (status === 'completed') return 'success';
  if (status === 'failed') return 'danger';
  if (status === 'queued' || status === 'not_started') return 'default';
  if (status === 'running') {
    if (stage === 'transcribe') return 'warning';
    if (stage === 'analyze' || stage === 'simplify' || stage === 'summarize' || stage === 'claims' || stage === 'predictions') return 'primary';
    return 'warning';
  }
  return 'default';
}

const metrics = ref([
  { label: '待处理', value: 0, extra: 'pending_review' },
  { label: '进行中', value: 0, extra: 'active' },
  { label: '已到期', value: 0, extra: 'due' },
  { label: '已锁定', value: 0, extra: 'final' },
]);

// V0.10 近期任务改真实数据：后端任务列表 → ProcessingTask 展示结构
const processingTasks = computed<ProcessingTask[]>(() => {
  return recentTasks.value.map((t) => {
    const isAll = t.mode === 'all';
    const title = t.result?.title
      || (isAll && t.result?.creator?.name ? `${t.result.creator.name} 全部视频` : '')
      || (t.items?.length ? `批量任务（${t.total ?? t.items.length} 条）` : '')
      || '处理任务';
    let stage = stageToCn(t.stage) || t.status || '';
    let progress = Math.round((t.progress ?? 0) * 100);
    let status: Theme = 'default';
    if (t.status === 'success') {
      stage = isAll && t.result
        ? `完成 ${t.result.processed ?? 0}，失败 ${t.result.failed ?? 0}`
        : '完成';
      progress = 100;
      status = 'success';
    } else if (t.status === 'partial') {
      stage = `部分完成 ${t.succeeded ?? 0}，失败 ${t.failed_count ?? 0}`;
      status = 'warning';
    } else if (t.status === 'waiting_for_action') {
      stage = '需人工处理';
      status = 'danger';
    } else if (t.status === 'paused' || t.status === 'pausing') {
      stage = t.status === 'paused' ? '已暂停' : '暂停中';
      status = 'warning';
    } else if (t.status === 'interrupted_recoverable') {
      stage = '待恢复';
      status = 'warning';
    } else if (t.status === 'failed') {
      stage = '失败';
      status = 'danger';
    } else if (t.status === 'queued') {
      stage = '排队中';
      status = 'default';
    } else {
      status = stage === 'Whisper 转写' ? 'warning' : 'primary';
    }
    return {
      id: t.id,
      title,
      platform: isAll ? '批量' : (t.result?.platform || ''),
      stage,
      progress,
      status,
      currentIndex: t.current_index || 0,
    };
  });
});

const dashboardData = ref<DashboardData | null>(null);

// V0.10 真实数据：featured 创作者取后端返回的样本最多者（有头像则显示头像）
function avatarSrcOf(creator: { avatar_path?: string | null; avatar_url?: string | null }): string {
  const local = creator.avatar_path;
  if (local) return `/${local.startsWith('/') ? local.slice(1) : local}`;
  return creator.avatar_url || '';
}

const featuredCreator = computed<CreatorCardData | null>(() => {
  const f = dashboardData.value?.featured_creator;
  if (!f) return null;
  const rel = f.reliability ?? {};
  const accuracy = rel.base_accuracy ?? null;
  const brier = rel.calibration_score ?? null;
  const sampleSize = rel.verified_count ?? 0;
  const hasStats = sampleSize > 0 && accuracy != null;
  return {
    id: f.id,
    name: f.name || '未知博主',
    handle: f.platform_id ? `${f.platform_id}` : f.platform || '',
    avatarUrl: avatarSrcOf(f),
    sampleSize,
    accuracy: accuracy != null ? Math.round(accuracy * 100) : null,
    brier,
    warning: (rel.sample_size_warning as string | null | undefined) || (hasStats ? '' : '尚无已验证预测，画像仅供参考'),
    hasStats,
  };
});

const domainStats = computed(() => {
  const rel = dashboardData.value?.featured_creator?.reliability ?? null;
  const byDomain = rel?.accuracy_by_domain ?? null;
  if (!byDomain || !Object.keys(byDomain).length) return [] as Array<{ label: string; value: number; count: number }>;
  return Object.entries(byDomain).map(([label, v]) => ({
    label,
    value: Math.round((v.accuracy ?? 0) * 100),
    count: v.n ?? 0,
  }));
});

const calibrationPoints = computed<Array<[number, number]>>(() => {
  // 逐条真实校准点：AI 置信度 → 实际命中(0/0.5/1)，来自已锁定验证记录
  return dashboardData.value?.featured_creator?.calibration_points ?? [];
});

const detectedPlatform = computed<{ label: string; hint: string; theme: Theme }>(() => {
  const value = submitText.value.toLowerCase();
  if (value.includes('bilibili') || value.includes('b23.tv') || value.includes('bv')) {
    return { label: 'B站', hint: '已识别为 Bilibili 内容', theme: 'primary' };
  }
  if (value.includes('douyin') || value.includes('v.douyin') || value.includes('抖音')) {
    return { label: '抖音', hint: '已识别为抖音分享文案', theme: 'success' };
  }
  return { label: '待识别', hint: '粘贴后自动判断平台', theme: 'default' };
});

// V0.10 提交任务：交 store 轮询，不再组件内 for 循环（切页不丢）
async function startSubmit() {
  if (!submitText.value.trim()) return;
  isSubmitting.value = true;
  submitText.value = submitText.value.trim();
  try {
    await submitIngest(submitText.value, true, ingestMode.value);
    submitText.value = '';
    // 立即同步一次；后续由 App 层 15s 轮询维持
    await refresh();
    await loadDashboard();
  } catch (e) {
    console.error('提交失败', e);
  } finally {
    isSubmitting.value = false;
  }
}

async function loadDashboard() {
  try {
    const data = await getDashboard();
    dashboardData.value = data;
    const counts = data.counts ?? {};
    metrics.value = [
      { label: '待处理', value: counts.pending_review ?? 0, extra: 'pending_review' },
      { label: '进行中', value: counts.active ?? 0, extra: 'active' },
      { label: '已到期', value: counts.due ?? 0, extra: 'due' },
      { label: '已锁定', value: counts.final ?? 0, extra: 'final' },
    ];
  } catch (e) {
    console.error('加载仪表盘失败', e);
  }
}

onMounted(async () => {
  await loadDashboard();
  await refresh();
});

const ProcessingTasks = defineComponent({
  name: 'ProcessingTasks',
  props: {
    tasks: {
      type: Array as PropType<ProcessingTask[]>,
      required: true,
    },
  },
  setup(props) {
    const TCard = resolveComponent('t-card');
    const TProgress = resolveComponent('t-progress');
    const TTag = resolveComponent('t-tag');

    return () =>
      h(
        TCard,
        { bordered: true, class: 'module-card' },
        {
          title: () => '近期处理任务',
          default: () =>
            h(
              'div',
              { class: 'task-list' },
              props.tasks.length
                ? props.tasks.map((task) =>
                    h('div', { class: 'task-item', key: task.id }, [
                      h('div', { class: 'task-item-head' }, [
                        h('div', [h('strong', task.title), h('span', `${task.id.slice(0, 8)} · ${task.platform || '-'}`)]),
                        h(TTag, { theme: task.status, variant: 'light' }, () => task.stage),
                      ]),
                      h(TProgress, { percentage: task.progress, theme: 'line', size: 'small' }),
                    ]),
                  )
                : h('div', { class: 'task-empty' }, '暂无处理任务，提交链接后会在这里显示进度'),
            ),
        },
      );
  },
});

const CreatorCard = defineComponent({
  name: 'CreatorCard',
  props: {
    creator: {
      type: Object as PropType<CreatorCardData>,
      required: true,
    },
  },
  setup(props) {
    const TAlert = resolveComponent('t-alert');
    const TAvatar = resolveComponent('t-avatar');
    const TCard = resolveComponent('t-card');
    const TTag = resolveComponent('t-tag');

    return () =>
      h(
        TCard,
        { bordered: true, class: 'module-card creator-card' },
        {
          title: () => '创作者可信度画像',
          actions: () => h(TTag, { theme: props.creator.sampleSize ? 'warning' : 'default', variant: 'light' },
            () => props.creator.sampleSize ? `已验证 ${props.creator.sampleSize}` : '待验证'),
          default: () => [
            h('div', { class: 'creator-head' }, [
              props.creator.avatarUrl
                ? h(TAvatar, { size: '64px', shape: 'round', image: props.creator.avatarUrl, hideOnLoadFailed: false })
                : h(TAvatar, { size: '64px' }, () => props.creator.name.slice(0, 1)),
              h('div', [h('h3', props.creator.name), h('p', props.creator.handle || props.creator.name)]),
            ]),
            h('div', { class: 'creator-score' }, [
              h('div', [h('span', '基础正确率'), h('strong', props.creator.accuracy != null ? `${props.creator.accuracy}%` : '-')]),
              h('div', [h('span', '样本量'), h('strong', `n=${props.creator.sampleSize}`)]),
              h('div', [h('span', 'Brier'), h('strong', props.creator.brier != null ? props.creator.brier.toFixed(2) : '-')]),
            ]),
            props.creator.warning
              ? h(TAlert, { theme: 'warning', message: props.creator.warning, close: false })
              : null,
          ],
        },
      );
  },
});

const ReliabilityBars = defineComponent({
  name: 'ReliabilityBars',
  props: {
    items: {
      type: Array as PropType<Array<{ label: string; value: number; count: number }>>,
      required: true,
    },
  },
  setup(props) {
    const TCard = resolveComponent('t-card');
    const TProgress = resolveComponent('t-progress');

    return () =>
      h(
        TCard,
        { bordered: true, class: 'module-card' },
        {
          title: () => '分领域正确率',
          default: () =>
            props.items.length
              ? h(
                  'div',
                  { class: 'bar-list' },
                  props.items.map((item) =>
                    h('div', { class: 'bar-row', key: item.label }, [
                      h('div', { class: 'bar-meta' }, [
                        h('span', item.label),
                        h('span', `${item.value}% · n=${item.count}`),
                      ]),
                      h(TProgress, { percentage: item.value, theme: 'line', color: '#1d4ed8' }),
                    ]),
                  ),
                )
              : h('div', { class: 'empty-chart' }, '暂无分领域数据（需完成验证后生成）'),
        },
      );
  },
});

const CalibrationChart = defineComponent({
  name: 'CalibrationChart',
  props: {
    points: {
      type: Array as PropType<Array<[number, number]>>,
      required: true,
    },
  },
  setup(props) {
    const chartRef = ref<HTMLDivElement>();
    const TCard = resolveComponent('t-card');
    let chart: echarts.ECharts | undefined;

    function renderChart() {
      if (!chartRef.value) return;
      chart = chart ?? echarts.init(chartRef.value);
      chart.setOption({
        grid: { top: 24, right: 18, bottom: 36, left: 44 },
        tooltip: { trigger: 'item', formatter: '预测置信度 {c0}<br />实际命中率 {c1}' },
        xAxis: { type: 'value', min: 0, max: 1, name: '预测置信度' },
        yAxis: { type: 'value', min: 0, max: 1, name: '实际命中率' },
        series: [
          {
            type: 'line',
            data: [
              [0, 0],
              [1, 1],
            ],
            symbol: 'none',
            lineStyle: { color: '#98a2b3', type: 'dashed' },
          },
          {
            type: 'scatter',
            symbolSize: 12,
            data: props.points,
            itemStyle: { color: '#1d4ed8' },
          },
        ],
      });
    }

    onMounted(() => {
      nextTick(renderChart);
      window.addEventListener('resize', renderChart);
    });

    watch(() => props.points, renderChart, { deep: true });

    onBeforeUnmount(() => {
      window.removeEventListener('resize', renderChart);
      chart?.dispose();
    });

    return () =>
      h(
        TCard,
        { bordered: true, class: 'module-card' },
        {
          title: () => 'Brier 校准散点图',
          default: () => h('div', { ref: chartRef, class: 'chart-box' }),
        },
      );
  },
});
</script>

<style scoped>
.dashboard-page {
  color: #101828;
}

.submit-card,
.module-card,
.metric-card {
  border-radius: 8px;
}

.submit-actions,
.task-title,
.task-item-head,
.creator-head,
.bar-meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}

.platform-result {
  display: flex;
  align-items: center;
  gap: 10px;
  color: #667085;
  font-size: 13px;
}

.task-progress {
  margin-top: 18px;
  padding: 16px;
  background: #f8fafc;
  border: 1px solid #edf1f7;
  border-radius: 8px;
}

.task-title {
  margin-bottom: 10px;
  font-weight: 600;
}

.batch-summary {
  margin-top: 10px;
  color: #667085;
  font-size: 13px;
}

.batch-list {
  margin-top: 10px;
  max-height: 320px;
  overflow-y: auto;
  border: 1px solid #edf1f7;
  border-radius: 8px;
  background: #fff;
}

.batch-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 7px 12px;
  border-bottom: 1px solid #f2f4f7;
}

.batch-item:last-child {
  border-bottom: none;
}

.batch-item.current {
  background: #eef4ff;
}

.batch-item.done .batch-title {
  color: #98a2b3;
  text-decoration: line-through;
}

.batch-item.error {
  background: #fef3f2;
}

/* V0.13 任务控制区（暂停 / 继续剩余 / 重试失败项） */
.task-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin-top: 12px;
}

.task-hint {
  color: #b54708;
  font-size: 12px;
  line-height: 1.5;
}

.batch-index {
  flex: 0 0 28px;
  color: #98a2b3;
  font-size: 12px;
  text-align: right;
}

.batch-title {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13px;
  color: #344054;
}

.metric-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
  height: 100%;
}

.metric-label,
.metric-extra,
.task-item span,
.creator-head p,
.creator-score span,
.bar-meta span:last-child {
  color: #667085;
  font-size: 13px;
}

.metric-value {
  margin-top: 12px;
  color: #101828;
  font-size: 34px;
  font-weight: 700;
}

.metric-extra {
  margin-top: 8px;
}

.section-row {
  margin-top: 20px;
}

.task-list {
  display: grid;
  gap: 14px;
}

.task-item {
  padding: 14px;
  background: #f8fafc;
  border: 1px solid #edf1f7;
  border-radius: 8px;
}

.task-item strong,
.task-item span {
  display: block;
}

.task-item span {
  margin-top: 4px;
}

.task-empty {
  padding: 24px 12px;
  color: #98a2b3;
  font-size: 13px;
  text-align: center;
  border: 1px dashed #e4e7ec;
  border-radius: 8px;
}

.creator-head {
  justify-content: flex-start;
}

.creator-head h3,
.creator-head p {
  margin: 0;
}

.creator-score {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
  margin: 20px 0;
}

.creator-score div {
  padding: 14px;
  background: #f8fafc;
  border-radius: 8px;
}

.creator-score span,
.creator-score strong {
  display: block;
}

.creator-score strong {
  margin-top: 6px;
  font-size: 22px;
}

.bar-list {
  display: grid;
  gap: 16px;
}

.bar-meta {
  margin-bottom: 8px;
}

.empty-chart {
  padding: 32px 12px;
  color: #98a2b3;
  font-size: 13px;
  text-align: center;
  border: 1px dashed #e4e7ec;
  border-radius: 8px;
}

.chart-box {
  width: 100%;
  height: 300px;
}
</style>
