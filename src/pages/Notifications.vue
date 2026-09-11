<template>
  <div class="notifications-page">
    <t-card bordered>
      <template #title>通知记录 <t-tag v-if="unread" theme="danger" variant="light" class="unread-badge">{{ unread }} 未读</t-tag></template>
      <template #actions>
        <t-space>
          <t-button size="small" variant="outline" @click="markAll">全部已读</t-button>
          <t-popconfirm content="确定清空全部通知记录？" @confirm="clearAll">
            <t-button size="small" theme="danger" variant="outline">清空</t-button>
          </t-popconfirm>
        </t-space>
      </template>

      <t-list split>
        <t-list-item v-for="n in items" :key="n.id" :class="{ 'is-unread': !n.read_at }" @click="markOne(n.id)">
          <t-list-item-meta :title="`${catLabel(n.category)} · ${n.title}`" :description="n.body">
            <template #avatar>
              <div class="note-avatar" :class="`cat-${n.category || 'other'}`">
                <t-icon :name="catIcon(n.category)" size="30px" stroke-width="2" />
              </div>
            </template>
          </t-list-item-meta>
          <template #action>
            <span class="meta">{{ n.channel }} · {{ fmtTime(n.created_at) }}</span>
          </template>
        </t-list-item>
      </t-list>
      <t-empty v-if="!items.length" title="暂无通知" description="处理视频、自动锁定、新视频都会推送通知" />
    </t-card>

    <t-card bordered>
      <template #title>通道测试与设置</template>
      <t-alert theme="info" message="通道配置在 config.json 的 notify 段。点击测试可验证当前启用的通道是否可用。" :close="false" class="module-alert" />
      <t-form label-align="top" class="settings-form">
        <t-form-item label="测试通知标题">
          <t-input v-model="testForm.title" />
        </t-form-item>
        <t-form-item label="测试通知内容">
          <t-textarea v-model="testForm.body" :autosize="{ minRows: 2 }" />
        </t-form-item>
        <t-form-item>
          <t-button theme="primary" :loading="testing" @click="sendTest">发送测试通知</t-button>
          <span v-if="testResult" class="test-result">{{ testResult }}</span>
        </t-form-item>
      </t-form>
    </t-card>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue';
import {
  clearNotifications,
  getNotifications,
  markNotificationsRead,
  testNotification,
  type NotificationRow,
} from '../api/client';

const items = ref<NotificationRow[]>([]);
const unread = ref(0);
const testing = ref(false);
const testResult = ref('');

const testForm = reactive({
  title: '测试通知',
  body: '如果你能看到这条消息，说明通知通道工作正常。',
});

const CAT_META: Record<string, { label: string; icon: string }> = {
  new_videos: { label: '新视频', icon: 'video-camera' },
  auto_process: { label: '自动处理', icon: 'tools' },
  auto_applied: { label: '自动锁定', icon: 'lock-on' },
  manual: { label: '测试', icon: 'mail' },
};

function catLabel(c: string): string {
  return CAT_META[c]?.label ?? c;
}
function catIcon(c: string): string {
  return CAT_META[c]?.icon ?? 'bell';
}
function fmtTime(ts: number): string {
  const d = new Date((ts || 0) * 1000);
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

async function load() {
  try {
    const data = await getNotifications(50);
    items.value = data.items;
    unread.value = data.unread;
  } catch (e) {
    console.error('加载通知失败', e);
  }
}

async function markAll() {
  try {
    await markNotificationsRead(null);
    await load();
  } catch (e) {
    console.error('标记已读失败', e);
  }
}

async function markOne(id: string) {
  try {
    await markNotificationsRead(id);
    await load();
  } catch (e) {
    console.error('标记已读失败', e);
  }
}

async function clearAll() {
  try {
    await clearNotifications();
    await load();
  } catch (e) {
    console.error('清空失败', e);
  }
}

async function sendTest() {
  testing.value = true;
  testResult.value = '';
  try {
    const res = await testNotification(testForm.title, testForm.body);
    testResult.value = '已发送（成功项见后端通知记录）';
  } catch (e) {
    testResult.value = `发送失败：${e instanceof Error ? e.message : e}`;
  } finally {
    testing.value = false;
    setTimeout(load, 1500);
  }
}

onMounted(load);
</script>

<style scoped>
.notifications-page {
  display: grid;
  gap: 20px;
}
.is-unread {
  background: #f0f6ff;
  cursor: pointer;
}
.meta {
  color: #98a2b3;
  font-size: 12px;
}
/* 分类图标徽章：统一 44px 圆角方形，柔色底 + 居中 t-icon */
.note-avatar {
  display: grid;
  place-items: center;
  flex: 0 0 auto;
  width: 44px;
  height: 44px;
  border-radius: 10px;
  line-height: 1;
  user-select: none;
}
.note-avatar.cat-new_videos {
  background: #e7f0ff;
  color: #1d4ed8;
}
.note-avatar.cat-auto_process {
  background: #fff1e0;
  color: #b45309;
}
.note-avatar.cat-auto_applied {
  background: #e3f7ee;
  color: #0f766e;
}
.note-avatar.cat-manual {
  background: #eef1f6;
  color: #475467;
}
.note-avatar.cat-other {
  background: #f2f4f7;
  color: #667085;
}
.settings-form {
  max-width: 560px;
  margin-top: 16px;
}
.module-alert {
  margin-bottom: 8px;
}
.test-result {
  margin-left: 12px;
  color: #12b886;
  font-size: 13px;
}
.unread-badge {
  margin-left: 8px;
}
</style>
