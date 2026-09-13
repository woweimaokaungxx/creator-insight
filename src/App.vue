<template>
  <AppLayout
    :active-route="activeRoute"
    :page-title="pageMeta.title"
    :page-description="pageMeta.description"
    @navigate="handleNavigate"
  >
    <Dashboard v-if="activePage === 'dashboard'" />
    <Predictions
      v-else-if="activePage === 'predictions'"
      :initial-status="predictionStatus"
      @status-change="handlePredictionStatusChange"
    />
    <Creators v-else-if="activePage === 'creators'" />
    <Monitoring v-else-if="activePage === 'monitoring'" />
    <Notifications v-else-if="activePage === 'notifications'" />
    <Library v-else-if="activePage === 'library'" />
    <Semantic v-else-if="activePage === 'semantic'" />
    <Settings v-else-if="activePage === 'settings'" />
    <t-card v-else bordered class="placeholder-card">
      <template #title>{{ pageMeta.title }}</template>
      <t-empty
        title="模块预留"
        description="该页面已在导航中占位，后续可按相同组件边界接入自动监控、通知设置或创作者画像详情。"
      />
    </t-card>
  </AppLayout>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue';
import AppLayout from './layouts/Layout.vue';
import { useIngestTasks } from './composables/useIngestTasks';
import Dashboard from './pages/Dashboard.vue';
import Predictions from './pages/Predictions.vue';
import Creators from './pages/Creators.vue';
import Monitoring from './pages/Monitoring.vue';
import Notifications from './pages/Notifications.vue';
import Library from './pages/Library.vue';
import Semantic from './pages/Semantic.vue';
import Settings from './pages/Settings.vue';

type PageKey = 'dashboard' | 'predictions' | 'creators' | 'monitoring' | 'notifications' | 'library' | 'semantic' | 'settings';

const activeRoute = ref(normalizeRoute(window.location.pathname + window.location.search));

const pageMetaMap: Record<PageKey, { title: string; description: string }> = {
  dashboard: {
    title: '提交与仪表盘',
    description: '粘贴视频链接或分享文案，追踪处理进度，并快速查看今日验证工作量。',
  },
  predictions: {
    title: '预测生命周期管理',
    description: '统一处理待确认、验证队列和历史预测，保留 AI 初判与人工锁定结果。',
  },
  creators: {
    title: '创作者可信度画像',
    description: '查看博主正确率、样本量、Brier 校准和分领域表现。',
  },
  monitoring: {
    title: '自动监控管理',
    description: '管理关注博主、抓取间隔、暂停恢复和最近抓取记录。',
  },
  notifications: {
    title: '通知设置',
    description: '配置站内通知、邮件和 Webhook 推送策略。',
  },
  library: {
    title: '视频库',
    description: '查看已处理视频的互动数据 + 语义检索。',
  },
  semantic: {
    title: '语义检索',
    description: '本地 embedding 模型 + 智能检索配置（参考 douyin-creator-distill）。',
  },
  settings: {
    title: '系统设置',
    description: '可视化配置 AI、搜索、通知、Obsidian、抓取监控与验证参数，保存即生效。',
  },
};

const activePage = computed<PageKey>(() => {
  if (activeRoute.value.startsWith('/predictions')) return 'predictions';
  if (activeRoute.value.startsWith('/creators')) return 'creators';
  if (activeRoute.value.startsWith('/monitoring')) return 'monitoring';
  if (activeRoute.value.startsWith('/notifications')) return 'notifications';
  if (activeRoute.value.startsWith('/library')) return 'library';
  if (activeRoute.value.startsWith('/semantic')) return 'semantic';
  if (activeRoute.value.startsWith('/settings')) return 'settings';
  return 'dashboard';
});

const predictionStatus = computed(() => {
  const query = activeRoute.value.split('?')[1] ?? '';
  return new URLSearchParams(query).get('status') ?? 'all';
});

const pageMeta = computed(() => pageMetaMap[activePage.value]);

function normalizeRoute(route: string) {
  if (!route || route === '/') return '/dashboard';
  return route;
}

function handleNavigate(route: string) {
  activeRoute.value = normalizeRoute(route);
  window.history.pushState({}, '', activeRoute.value);
}

function handlePredictionStatusChange(status: string) {
  const route = status === 'all' ? '/predictions' : `/predictions?status=${status}`;
  handleNavigate(route);
}

function handlePopState() {
  activeRoute.value = normalizeRoute(window.location.pathname + window.location.search);
}

// V0.10 任务进度全局轮询：App 常驻，切页/刷新后仍恢复显示
const { resume } = useIngestTasks();

onMounted(() => {
  window.addEventListener('popstate', handlePopState);
  void resume();
});

onBeforeUnmount(() => {
  window.removeEventListener('popstate', handlePopState);
});
</script>

<style scoped>
.placeholder-card {
  min-height: 420px;
}
</style>
