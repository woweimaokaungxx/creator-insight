<template>
  <div class="monitoring-page">
    <t-card bordered>
      <template #title>关注博主订阅</template>
      <template #actions>
        <t-button theme="primary" @click="addDialog = true">新增订阅</t-button>
      </template>

      <t-table row-key="id" :data="subscriptions" :columns="columns" hover>
        <template #display_name="{ row }">{{ row.display_name || row.source_key || '-' }}</template>
        <template #enabled="{ row }">
          <t-tag :theme="row.enabled ? 'success' : 'default'" variant="light">
            {{ row.enabled ? '启用' : '暂停' }}
          </t-tag>
        </template>
        <template #operation="{ row }">
          <t-space>
            <t-button size="small" variant="outline" @click="runCheck(row.id)">立即抓取</t-button>
            <t-button size="small" variant="outline" @click="toggle(row)">{{ row.enabled ? '暂停' : '恢复' }}</t-button>
            <t-popconfirm content="确定删除该订阅？" @confirm="remove(row.id)">
              <t-button size="small" theme="danger" variant="outline">删除</t-button>
            </t-popconfirm>
          </t-space>
        </template>
      </t-table>
      <t-empty v-if="!subscriptions.length" title="暂无订阅" description="点击右上角新增关注博主" />
    </t-card>

    <t-card bordered>
      <template #title>最近抓取记录</template>
      <t-table row-key="id" :data="crawlTasks" :columns="taskColumns" hover size="small">
        <template #status="{ row }">
          <t-tag :theme="taskStatusTheme(row.status)" variant="light">{{ taskStatusLabel(row.status) }}</t-tag>
        </template>
      </t-table>
      <t-empty v-if="!crawlTasks.length" title="暂无抓取记录" />
    </t-card>

    <t-dialog v-model:visible="addDialog" header="新增关注博主" :footer="false" width="520px">
      <t-form label-align="top">
        <t-form-item label="博主链接 / 分享文案" required-mark>
          <t-textarea v-model="addForm.input" placeholder="粘贴博主主页链接或任意作品分享文案" :autosize="{ minRows: 3 }" />
        </t-form-item>
        <t-form-item label="平台">
          <t-radio-group v-model="addForm.platform">
            <t-radio-button value="douyin">抖音</t-radio-button>
            <t-radio-button value="bilibili">B站</t-radio-button>
          </t-radio-group>
        </t-form-item>
        <t-form-item label="检查间隔">
          <t-select v-model="addForm.interval" :options="intervalOptions" />
        </t-form-item>
        <t-form-item label="发现新视频后自动处理（下载+转写+AI）">
          <t-switch v-model="addForm.autoProcess" />
        </t-form-item>
        <t-form-item>
          <t-button theme="primary" :loading="adding" @click="add">保存</t-button>
        </t-form-item>
      </t-form>
    </t-dialog>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue';
import {
  addSubscription,
  checkSubscription,
  deleteSubscription,
  getCrawlTasks,
  getSubscriptions,
  updateSubscription,
  type CrawlTask,
  type Subscription,
} from '../api/client';

const subscriptions = ref<Subscription[]>([]);
const crawlTasks = ref<CrawlTask[]>([]);
const addDialog = ref(false);
const adding = ref(false);

const addForm = reactive({ input: '', platform: 'douyin', interval: 24, autoProcess: false });

const intervalOptions = [
  { label: '每 6 小时', value: 6 },
  { label: '每天', value: 24 },
  { label: '每 2 天', value: 48 },
  { label: '每 3 天', value: 72 },
];

const columns = [
  { colKey: 'display_name', title: '博主', minWidth: 200 },
  { colKey: 'platform', title: '平台', width: 100 },
  { colKey: 'interval', title: '间隔', width: 100 },
  { colKey: 'enabled', title: '状态', width: 90 },
  { colKey: 'content_count', title: '内容数', width: 90 },
  { colKey: 'operation', title: '操作', width: 260 },
];

const taskColumns = [
  { colKey: 'display_name', title: '博主', minWidth: 180 },
  { colKey: 'trigger', title: '触发', width: 100 },
  { colKey: 'status', title: '状态', width: 100 },
  { colKey: 'total_videos', title: '视频数', width: 90 },
  { colKey: 'error', title: '错误信息', minWidth: 200 },
];

function fmtInterval(h: number): string {
  if (h < 24) return `每 ${h} 小时`;
  if (h % 24 === 0) return `每 ${h / 24} 天`;
  return `${h} 小时`;
}

function taskStatusTheme(s: string): string {
  if (s === 'success') return 'success';
  if (s === 'error') return 'danger';
  if (s === 'running') return 'primary';
  return 'default';
}

function taskStatusLabel(s: string): string {
  const m: Record<string, string> = {
    success: '成功', error: '失败', running: '进行中', pending: '等待中',
  };
  return m[s] ?? s;
}

async function load() {
  try {
    const subs = await getSubscriptions();
    subscriptions.value = subs.map((s) => ({
      ...s,
      interval: fmtInterval(s.check_interval_hours ?? 24),
    })) as Subscription[];
  } catch (e) {
    console.error('加载订阅失败', e);
    subscriptions.value = [];
  }
  try {
    crawlTasks.value = await getCrawlTasks();
  } catch (e) {
    console.error('加载抓取记录失败', e);
    crawlTasks.value = [];
  }
}

async function add() {
  if (!addForm.input.trim()) return;
  adding.value = true;
  try {
    await addSubscription(addForm.input, addForm.platform, addForm.interval, addForm.autoProcess);
    addDialog.value = false;
    addForm.input = '';
    await load();
  } catch (e) {
    console.error('新增订阅失败', e);
  } finally {
    adding.value = false;
  }
}

async function runCheck(id: string) {
  try {
    await checkSubscription(id);
    await load();
  } catch (e) {
    console.error('立即抓取失败', e);
  }
}

async function toggle(row: Subscription) {
  try {
    await updateSubscription(row.id, { enabled: row.enabled ? 0 : 1 });
    await load();
  } catch (e) {
    console.error('切换状态失败', e);
  }
}

async function remove(id: string) {
  try {
    await deleteSubscription(id);
    await load();
  } catch (e) {
    console.error('删除订阅失败', e);
  }
}

onMounted(load);
</script>

<style scoped>
.monitoring-page {
  display: grid;
  gap: 20px;
}
</style>
