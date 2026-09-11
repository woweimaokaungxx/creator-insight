<template>
  <t-layout class="app-shell">
    <t-aside class="sidebar" width="248px">
      <div class="brand" @click="emit('navigate', '/dashboard')">
        <div class="brand-mark">CI</div>
        <div>
          <div class="brand-title">Creator Insight</div>
          <div class="brand-subtitle">预测可信度验证</div>
        </div>
      </div>

      <t-menu
        :value="activeMenu"
        theme="light"
        expand-mutex
        class="side-menu"
        @change="handleMenuChange"
      >
        <t-menu-group title="工作台">
          <t-menu-item value="/dashboard">
            <template #icon><dashboard-icon /></template>
            提交与仪表盘
          </t-menu-item>
        </t-menu-group>

        <t-menu-group title="预测管理">
          <t-menu-item value="/predictions?status=pending">
            <template #icon><time-icon /></template>
            待确认列表
          </t-menu-item>
          <t-menu-item value="/predictions?status=verifying">
            <template #icon><search-icon /></template>
            验证队列
          </t-menu-item>
          <t-menu-item value="/predictions">
            <template #icon><list-icon /></template>
            全部预测
          </t-menu-item>
        </t-menu-group>

        <t-menu-group title="创作者">
          <t-menu-item value="/creators">
            <template #icon><usergroup-icon /></template>
            创作者画像
          </t-menu-item>
          <t-menu-item value="/library">
            <template #icon><list-icon /></template>
            视频库
          </t-menu-item>
          <t-menu-item value="/semantic">
            <template #icon><search-icon /></template>
            语义检索
          </t-menu-item>
        </t-menu-group>

        <t-menu-group title="系统">
          <t-menu-item value="/monitoring">
            <template #icon><server-icon /></template>
            自动监控
          </t-menu-item>
          <t-menu-item value="/notifications">
            <template #icon><setting-icon /></template>
            通知设置
          </t-menu-item>
        </t-menu-group>
      </t-menu>
    </t-aside>

    <t-layout class="app-body">
      <t-header class="topbar">
        <div class="topbar-left">
          <div class="page-heading">
            <h1>{{ pageTitle }}</h1>
            <p>{{ pageDescription }}</p>
          </div>

          <!-- V0.10 全局任务进度：任一页面可见，点击跳仪表盘 -->
          <div v-if="activeTask" class="global-task" @click="goDashboard" title="查看任务详情">
            <div class="global-task-head">
              <span class="global-task-badge" :class="activeTask.status">{{ activeTask.status === 'error' ? '失败' : '处理中' }}</span>
              <span class="global-task-text">{{ globalTaskLabel }}</span>
              <span class="global-task-pct">{{ globalTaskPct }}%</span>
            </div>
            <t-progress
              :percentage="globalTaskPct"
              theme="line"
              size="small"
              :status="activeTask.status === 'error' ? 'error' : 'active'"
            />
          </div>
        </div>

        <div class="topbar-actions">
          <t-input class="global-search" placeholder="搜索博主、预测对象、预测内容" clearable>
            <template #prefix-icon><search-icon /></template>
          </t-input>

          <t-popup placement="bottom-right" trigger="click" :overlay-style="{ width: '360px' }">
            <t-button variant="text" shape="square" class="icon-button">
              <t-badge :count="unreadCount" size="small">
                <notification-icon />
              </t-badge>
            </t-button>

            <template #content>
              <div class="notification-panel">
                <div class="panel-head">
                  <span>通知中心</span>
                  <t-link theme="primary" hover="color" @click="markAllRead">全部已读</t-link>
                </div>

                <t-list split>
                  <t-list-item v-for="item in notifications" :key="item.id" class="notification-item">
                    <t-list-item-meta :title="item.title" :description="item.content" />
                    <template #action>
                      <t-tag :theme="item.read ? 'default' : 'primary'" variant="light">
                        {{ item.read ? '已读' : '未读' }}
                      </t-tag>
                    </template>
                  </t-list-item>
                </t-list>

                <div class="panel-footer">
                  <t-button block variant="outline" @click="emit('navigate', '/notifications')">
                    Webhook / 邮件配置
                  </t-button>
                </div>
              </div>
            </template>
          </t-popup>

          <t-dropdown :options="userOptions" trigger="click">
            <t-avatar image="" size="40px">投</t-avatar>
          </t-dropdown>
        </div>
      </t-header>

      <t-content class="content">
        <slot />
      </t-content>
    </t-layout>
  </t-layout>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import {
  DashboardIcon,
  ListIcon,
  NotificationIcon,
  SearchIcon,
  ServerIcon,
  SettingIcon,
  TimeIcon,
  UsergroupIcon,
} from 'tdesign-icons-vue-next';
import { getNotifications, markNotificationsRead } from '../api/client';
import { useIngestTasks, stageToCn } from '../composables/useIngestTasks';

const props = defineProps<{
  activeRoute: string;
  pageTitle: string;
  pageDescription: string;
}>();

const emit = defineEmits<{
  navigate: [route: string];
}>();

// V0.10 全局任务进度（与 App / Dashboard 共享同一单例）
const { activeTask } = useIngestTasks();

const globalTaskLabel = computed(() => {
  const t = activeTask.value;
  if (!t) return '';
  const isAll = t.mode === 'all';
  if (isAll && t.items?.length) {
    const idx = Math.min(t.current_index ?? 1, t.items.length);
    return `批量 ${idx}/${t.items.length} · ${stageToCn(t.stage) || t.message || ''}`;
  }
  return t.result?.title || (t.items?.length ? `批量 ${t.total ?? t.items.length} 条` : '') || stageToCn(t.stage) || '处理中';
});

const globalTaskPct = computed(() => {
  const t = activeTask.value;
  return Math.round((t?.progress ?? 0) * 100);
});

function goDashboard() {
  if (props.activeRoute.startsWith('/dashboard')) return;
  emit('navigate', '/dashboard');
}

const notifications = ref<Array<{ id: string; title: string; content: string; read: boolean }>>([]);

async function loadNotifications() {
  try {
    const data = await getNotifications(20);
    notifications.value = data.items.map((n) => ({
      id: n.id,
      title: n.title,
      content: n.body || n.title,
      read: !!n.read_at,
    }));
  } catch (e) {
    console.error('加载通知失败', e);
    notifications.value = [];
  }
}

const userOptions = [
  { content: '个人偏好', value: 'profile' },
  { content: '数据导出', value: 'export' },
  { content: '退出', value: 'logout' },
];

const activeMenu = computed(() => {
  if (props.activeRoute.startsWith('/predictions?status=pending')) return '/predictions?status=pending';
  if (props.activeRoute.startsWith('/predictions?status=verifying')) return '/predictions?status=verifying';
  if (props.activeRoute.startsWith('/predictions')) return '/predictions';
  return props.activeRoute.split('?')[0];
});

const unreadCount = computed(() => notifications.value.filter((item) => !item.read).length);

function handleMenuChange(value: string | number) {
  emit('navigate', String(value));
}

async function markAllRead() {
  try {
    await markNotificationsRead(null);
    await loadNotifications();
  } catch (e) {
    console.error('标记已读失败', e);
  }
}

onMounted(() => {
  loadNotifications();
  // 每 30s 刷新通知角标
  window.setInterval(loadNotifications, 30000);
});
</script>

<style scoped>
/* V0.10 固定导航布局：整页锁死在视口内，仅内容区内部滚动 */
:global(html),
:global(body) {
  margin: 0;
  overflow: hidden;
  height: 100%;
}

.app-shell {
  display: flex;
  height: 100vh;
  min-height: 0;
  overflow: hidden;
  background: #f5f7fb;
}

/* 侧栏：贴左固定，自身超高时内部滚动 */
.sidebar {
  flex: 0 0 auto;
  height: 100vh;
  min-height: 0;
  overflow-y: auto;
  background: #fff;
  border-right: 1px solid #e7eaf0;
}

/* 右侧列：纵向弹性，锁死视口高度，顶栏固定、内容滚动 */
.app-body {
  flex: 1 1 auto;
  display: flex;
  flex-direction: column;
  height: 100vh;
  min-height: 0;
  min-width: 0;
  overflow: hidden;
}

.brand {
  display: flex;
  align-items: center;
  gap: 12px;
  height: 72px;
  padding: 0 22px;
  cursor: pointer;
  border-bottom: 1px solid #eef1f6;
}

.brand-mark {
  display: grid;
  place-items: center;
  width: 38px;
  height: 38px;
  border-radius: 8px;
  color: #fff;
  font-weight: 700;
  background: linear-gradient(135deg, #1d4ed8, #0f766e);
}

.brand-title {
  color: #101828;
  font-size: 16px;
  font-weight: 700;
}

.brand-subtitle {
  margin-top: 2px;
  color: #667085;
  font-size: 12px;
}

.side-menu {
  border-right: 0;
}

.topbar {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  justify-content: space-between;
  min-height: 72px;
  height: auto;
  padding: 10px 28px;
  background: #fff;
  border-bottom: 1px solid #e7eaf0;
  gap: 16px;
}

.topbar-left {
  display: flex;
  align-items: center;
  gap: 24px;
  flex-wrap: wrap;
}

.page-heading h1 {
  margin: 0;
  color: #101828;
  font-size: 20px;
  line-height: 28px;
}

.page-heading p {
  margin: 4px 0 0;
  color: #667085;
  font-size: 13px;
}

/* V0.10 全局任务进度 */
.global-task {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 240px;
  padding: 8px 14px;
  border: 1px solid #dbe4ff;
  border-radius: 8px;
  background: #f4f7ff;
  cursor: pointer;
  transition: box-shadow 0.2s ease;
}

.global-task:hover {
  box-shadow: 0 2px 8px rgba(29, 78, 216, 0.12);
}

.global-task-head {
  display: flex;
  align-items: center;
  gap: 8px;
}

.global-task-badge {
  flex: 0 0 auto;
  padding: 1px 8px;
  border-radius: 999px;
  font-size: 11px;
  line-height: 18px;
  background: #1d4ed8;
  color: #fff;
}

.global-task-badge.error {
  background: #d54941;
}

.global-task-text {
  flex: 1;
  color: #344054;
  font-size: 12px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.global-task-pct {
  color: #1d4ed8;
  font-size: 12px;
  font-weight: 600;
}

.topbar-actions {
  display: flex;
  align-items: center;
  gap: 14px;
  flex: 0 0 auto;
}

.global-search {
  width: 300px;
}

.icon-button {
  color: #344054;
}

.content {
  flex: 1 1 auto;
  min-width: 0;
  min-height: 0;
  overflow-y: auto;
  padding: 24px 28px 32px;
}

.notification-panel {
  padding: 14px;
  background: #fff;
  border-radius: 8px;
  box-shadow: 0 12px 36px rgb(16 24 40 / 14%);
}

.panel-head,
.panel-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.panel-head {
  margin-bottom: 8px;
  color: #101828;
  font-weight: 600;
}

.panel-footer {
  margin-top: 12px;
}

.notification-item :deep(.t-list-item__meta-description) {
  color: #667085;
  font-size: 12px;
}

@media (max-width: 1080px) {
  .global-search {
    width: 220px;
  }
}
</style>
