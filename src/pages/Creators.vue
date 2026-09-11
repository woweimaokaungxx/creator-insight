<template>
  <div class="creators-page">
    <t-card bordered class="list-card">
      <template #title>创作者列表</template>
      <t-list split>
        <t-list-item v-for="creator in creators" :key="creator.id">
          <t-list-item-meta :title="creator.name || creator.platform_id || '未知'"
                            :description="`${creator.platform || ''} · ${creator.content_count ?? 0} 条内容`">
            <template #avatar>
              <div class="avatar-wrap">
                <t-avatar v-if="avatarSrc(creator)" :image="avatarSrc(creator)" :hide-on-load-failed="false" />
                <t-avatar v-else>{{ (creator.name || '?').slice(0, 1) }}</t-avatar>
              </div>
            </template>
          </t-list-item-meta>
          <template #action>
            <t-space>
              <t-tag :theme="(creator.content_count ?? 0) < 30 ? 'warning' : 'success'" variant="light">
                n={{ creator.content_count ?? 0 }}
              </t-tag>
              <t-button size="small" theme="primary" variant="outline" @click="openProfile(creator.id)">
                查看画像
              </t-button>
            </t-space>
          </template>
        </t-list-item>
        <t-empty v-if="!creators.length" title="暂无创作者" description="处理视频后博主会自动出现在这里" />
      </t-list>
    </t-card>

    <t-card v-if="profile" bordered class="profile-card">
      <template #title>画像详情 · {{ profile.name || profile.platform_id }}</template>
      <t-row v-if="reliability" :gutter="[16, 16]">
        <t-col :span="6">
          <div class="mini-stat"><span>已验证样本</span><strong>{{ reliability.verified_count ?? 0 }}</strong></div>
        </t-col>
        <t-col :span="6">
          <div class="mini-stat"><span>基础正确率</span><strong>{{ fmtPct(reliability.base_accuracy) }}</strong></div>
        </t-col>
        <t-col :span="6">
          <div class="mini-stat"><span>Brier 校准分</span><strong>{{ reliability.calibration_score != null ? reliability.calibration_score.toFixed(3) : '-' }}</strong></div>
        </t-col>
        <t-col :span="6">
          <div class="mini-stat"><span>正确 / 错误</span><strong>{{ reliability.correct_count ?? 0 }} / {{ reliability.incorrect_count ?? 0 }}</strong></div>
        </t-col>
      </t-row>
      <t-alert v-if="reliability?.sample_size_warning" class="module-alert" theme="warning"
               :message="reliability.sample_size_warning" :close="false" />

      <t-row :gutter="[16, 16]" class="charts-row">
        <t-col :span="12">
          <div class="chart-title">置信度校准（Brier 散点）</div>
          <div ref="calibrationEl" class="calibration-chart"></div>
        </t-col>
        <t-col :span="12">
          <div class="chart-title">分领域正确率</div>
          <div ref="domainEl" class="calibration-chart"></div>
        </t-col>
      </t-row>

      <div class="chart-title" style="margin-top:16px">已验证预测</div>
      <t-table row-key="id" :data="profile.predictions" :columns="predColumns" hover size="small">
        <template #raw_text="{ row }">
          <div style="max-width:420px;">
            <div style="display:flex;align-items:center;gap:6px;">
              <span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">{{ row.interpreted_intent || row.raw_text }}</span>
              <t-tag v-if="!row.interpreted_intent" theme="warning" variant="light" size="small">未总结</t-tag>
            </div>
            <span v-if="row.interpreted_intent" style="display:block;color:#98a2b3;font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">原话：{{ row.raw_text }}</span>
          </div>
        </template>
        <template #verdict="{ row }">
          <t-tag v-if="row.final_verdict" :theme="verdictTheme(row.final_verdict)" variant="light">
            {{ verdictLabel(row.final_verdict) }}
          </t-tag>
          <t-tag v-else theme="default" variant="light">-</t-tag>
        </template>
      </t-table>
      <t-empty v-if="profile.predictions && !profile.predictions.length" title="暂无已验证预测" />
    </t-card>
  </div>
</template>

<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref } from 'vue';
import * as echarts from 'echarts';
import { getCreatorProfile, getCreators, type CreatorProfile, type CreatorRow } from '../api/client';

const creators = ref<CreatorRow[]>([]);
const profile = ref<CreatorProfile | null>(null);
const calibrationEl = ref<HTMLElement | null>(null);
const domainEl = ref<HTMLElement | null>(null);
let calibrationChart: echarts.ECharts | null = null;
let domainChart: echarts.ECharts | null = null;

const reliability = ref(profile.value?.reliability ?? null);

const predColumns = [
  { colKey: 'raw_text', title: '预测内容', minWidth: 360 },
  { colKey: 'verdict', title: '判定', width: 100 },
  { colKey: 'confidence', title: '置信度', width: 90 },
  { colKey: 'status', title: '状态', width: 90 },
];

// 头像：优先本地缓存（/avatars/xxx.jpg），否则回退平台远程地址
function avatarSrc(creator: CreatorRow): string {
  const local = (creator as { avatar_path?: string | null }).avatar_path;
  if (local) return `/${local.startsWith('/') ? local.slice(1) : local}`;
  const remote = (creator as { avatar_url?: string | null }).avatar_url;
  return remote || '';
}

function fmtPct(v: unknown): string {
  if (v == null) return '-';
  const n = Number(v);
  return `${Math.round(n * 100)}%`;
}

function verdictLabel(v: string): string {
  const m: Record<string, string> = {
    correct: '正确', partial: '部分正确', incorrect: '错误',
    inconclusive: '无法判断', invalid: '无效',
  };
  return m[v] ?? v;
}

function verdictTheme(v: string): string {
  if (v === 'correct') return 'success';
  if (v === 'partial') return 'warning';
  if (v === 'incorrect') return 'danger';
  return 'default';
}

async function loadCreators() {
  try {
    creators.value = await getCreators();
  } catch (e) {
    console.error('加载创作者失败', e);
    creators.value = [];
  }
}

async function openProfile(id: string) {
  try {
    profile.value = await getCreatorProfile(id);
    reliability.value = profile.value?.reliability ?? null;
    await nextTick();
    renderCharts();
  } catch (e) {
    console.error('加载画像失败', e);
  }
}

function renderCharts() {
  if (calibrationEl.value) {
    calibrationChart?.dispose();
    calibrationChart = echarts.init(calibrationEl.value);
    const points = (profile.value?.predictions ?? [])
      .filter((p) => p.calibration_point)
      .map((p) => p.calibration_point as [number, number]);
    calibrationChart.setOption({
      tooltip: { trigger: 'item' },
      grid: { left: 50, right: 20, top: 30, bottom: 40 },
      xAxis: { type: 'value', name: 'AI 置信度', min: 0, max: 1 },
      yAxis: { type: 'value', name: '实际正确率', min: 0, max: 1 },
      series: [
        { type: 'line', data: [[0, 0], [1, 1]], name: '完美校准', showSymbol: false, lineStyle: { type: 'dashed', color: '#adb5bd' } },
        { type: 'scatter', data: points, name: '预测点', symbolSize: 12, itemStyle: { color: '#4263eb' } },
      ],
      legend: { bottom: 0 },
    });
  }
  if (domainEl.value) {
    domainChart?.dispose();
    domainChart = echarts.init(domainEl.value);
    const domains = reliability.value?.accuracy_by_domain ?? {};
    const names = Object.keys(domains);
    domainChart.setOption({
      tooltip: { trigger: 'axis' },
      grid: { left: 50, right: 20, top: 30, bottom: 50 },
      xAxis: { type: 'category', data: names, axisLabel: { interval: 0, rotate: 30 } },
      yAxis: { type: 'value', min: 0, max: 1, name: '正确率' },
      series: [{
        type: 'bar',
        data: names.map((n) => ({ value: domains[n]?.accuracy ?? 0, name: n })),
        itemStyle: { color: '#0ca678' },
      }],
    });
  }
}

function onResize() {
  calibrationChart?.resize();
  domainChart?.resize();
}

onMounted(() => {
  loadCreators();
  window.addEventListener('resize', onResize);
});

onBeforeUnmount(() => {
  window.removeEventListener('resize', onResize);
  calibrationChart?.dispose();
  domainChart?.dispose();
});
</script>

<style scoped>
.creators-page {
  display: grid;
  grid-template-columns: minmax(300px, 420px) minmax(0, 1fr);
  gap: 20px;
  align-items: start;
}
.avatar-wrap :deep(.t-avatar) {
  width: 56px;
  height: 56px;
  font-size: 22px;
}
.mini-stat {
  padding: 14px;
  background: #f8fafc;
  border: 1px solid #edf1f7;
  border-radius: 8px;
}
.mini-stat span, .mini-stat strong { display: block; }
.mini-stat span { color: #667085; font-size: 13px; }
.mini-stat strong { margin-top: 6px; color: #101828; font-size: 22px; }
.module-alert { margin-top: 16px; }
.charts-row { margin-top: 16px; }
.chart-title { color: #344054; font-weight: 600; font-size: 14px; margin-bottom: 8px; }
.calibration-chart { width: 100%; height: 280px; }
</style>
