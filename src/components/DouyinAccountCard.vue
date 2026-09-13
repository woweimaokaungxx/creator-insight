<template>
  <t-card bordered class="account-card">
    <template #title>抖音账号与登录态</template>
    <template #actions>
      <t-tag :theme="meta.tag" variant="light">{{ meta.label }}</t-tag>
    </template>

    <t-alert
      theme="info"
      :close="false"
      class="account-tip"
      message="登录态仅保存在本机专用浏览器目录，页面不会显示 Cookie 明文；抓取与下载会自动复用最新凭证。"
    />

    <t-form label-align="top" class="account-form">
      <t-form-item label="登录态浏览器目录">
        <t-input v-model="form.profilePath" placeholder="留空使用项目默认目录" clearable />
      </t-form-item>

      <t-form-item label="手动写入 Cookie">
        <t-textarea
          v-model="form.cookie"
          placeholder="k1=v1; k2=v2"
          :autosize="{ minRows: 3, maxRows: 6 }"
        />
      </t-form-item>

      <t-form-item>
        <t-space break-line>
          <t-button theme="primary" :loading="loginLoading" @click="openLogin">打开登录窗口</t-button>
          <t-button variant="outline" :loading="cookieSaving" @click="saveCookie">保存 Cookie</t-button>
          <t-button variant="outline" :loading="configSaving" @click="saveProfile">保存目录</t-button>
          <t-button variant="outline" :loading="exporting" @click="exportCookie">导出 Cookie</t-button>
        </t-space>
      </t-form-item>
    </t-form>

    <t-alert v-if="hint" :theme="meta.alert" :close="false" class="account-progress" :message="hint" />

    <div class="account-meta">
      <span>Cookie 来源：{{ cookieSourceLabel }}（{{ overview?.cookie?.count ?? 0 }} 项）</span>
      <span>浏览器目录：{{ profileLabel }}</span>
      <span v-if="verifiedAt">最近验证：{{ verifiedAt }}</span>
    </div>
  </t-card>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue';
import { MessagePlugin } from 'tdesign-vue-next';
import {
  exportAccountCookie,
  getAccountLoginStatus,
  getAccountOverview,
  saveAccountConfig,
  saveAccountCookie,
  startAccountLogin,
  type AccountOverview,
  type LoginPhase,
} from '../api/client';

const POLL_INTERVAL = 3000;

const LOGIN_META: Record<LoginPhase, { label: string; tag: string; alert: string }> = {
  idle: { label: '未登录', tag: 'default', alert: 'info' },
  launching_login: { label: '启动中', tag: 'primary', alert: 'info' },
  waiting_for_login: { label: '等待扫码登录', tag: 'warning', alert: 'warning' },
  login_ready: { label: '已登录', tag: 'success', alert: 'success' },
  browser_closed: { label: '登录失败', tag: 'danger', alert: 'error' },
  helper_timeout: { label: '已超时', tag: 'danger', alert: 'error' },
  helper_error: { label: '登录失败', tag: 'danger', alert: 'error' },
};

const COOKIE_SOURCE: Record<string, string> = {
  manual: '手动写入',
  login: '登录态文件',
  auto: '匿名自动生成',
  none: '未配置',
};

const overview = ref<AccountOverview | null>(null);
const phase = ref<LoginPhase>('idle');
const errorMsg = ref('');
const form = reactive({ profilePath: '', cookie: '' });

const loginLoading = ref(false);
const cookieSaving = ref(false);
const configSaving = ref(false);
const exporting = ref(false);
let timer: number | undefined;

const meta = computed(() => LOGIN_META[phase.value] ?? LOGIN_META.idle);

const cookieSourceLabel = computed(() => COOKIE_SOURCE[overview.value?.cookie?.source ?? 'none'] ?? '未知');

const profileLabel = computed(() => {
  const p = overview.value?.profile;
  if (!p?.configured) return '未配置';
  return p.dir_name || '已配置';
});

const verifiedAt = computed(() => overview.value?.login?.verified_at || '');

const hint = computed(() => {
  if (phase.value === 'launching_login') return '正在启动浏览器窗口…';
  if (phase.value === 'waiting_for_login') return '请在弹出的浏览器窗口中扫码登录抖音，登录成功后窗口会自动关闭。';
  if (phase.value === 'login_ready') return '登录成功，登录态已保存，后续抓取会自动复用。';
  return errorMsg.value;
});

async function loadOverview() {
  try {
    const data = await getAccountOverview();
    overview.value = data;
    phase.value = data.login?.phase ?? 'idle';
    errorMsg.value = data.login?.error ?? '';
  } catch (e) {
    console.error('加载账号信息失败', e);
  }
}

function stopPoll() {
  if (timer) {
    window.clearInterval(timer);
    timer = undefined;
  }
}

function startPoll() {
  stopPoll();
  timer = window.setInterval(async () => {
    try {
      const status = await getAccountLoginStatus();
      phase.value = status.phase;
      errorMsg.value = status.error ?? '';
      if (status.phase === 'login_ready') {
        stopPoll();
        await loadOverview();
        MessagePlugin.success('登录成功，登录态已保存');
      } else if (['browser_closed', 'helper_timeout', 'helper_error'].includes(status.phase)) {
        stopPoll();
      }
    } catch (e) {
      console.error('轮询登录状态失败', e);
    }
  }, POLL_INTERVAL);
}

async function openLogin() {
  loginLoading.value = true;
  try {
    const res = await startAccountLogin(form.profilePath.trim());
    phase.value = res.phase ?? 'launching_login';
    errorMsg.value = '';
    MessagePlugin.info(res.already_open ? '登录窗口已打开，请完成扫码' : '登录窗口正在打开，请完成扫码');
    startPoll();
  } catch (e) {
    console.error('启动登录窗口失败', e);
    errorMsg.value = e instanceof Error ? e.message : String(e);
  } finally {
    loginLoading.value = false;
  }
}

async function saveCookie() {
  const raw = form.cookie.trim();
  if (!raw) {
    errorMsg.value = '请先粘贴 Cookie 内容（k1=v1; k2=v2）';
    return;
  }
  cookieSaving.value = true;
  try {
    const res = await saveAccountCookie(raw);
    form.cookie = '';
    errorMsg.value = '';
    await loadOverview();
    MessagePlugin.success(`已保存并生成 Cookie 文件（${res.count} 项）`);
  } catch (e) {
    console.error('保存 Cookie 失败', e);
    errorMsg.value = e instanceof Error ? e.message : String(e);
  } finally {
    cookieSaving.value = false;
  }
}

async function saveProfile() {
  configSaving.value = true;
  try {
    await saveAccountConfig(form.profilePath.trim());
    await loadOverview();
    MessagePlugin.success('账号配置已保存');
  } catch (e) {
    console.error('保存账号配置失败', e);
    errorMsg.value = e instanceof Error ? e.message : String(e);
  } finally {
    configSaving.value = false;
  }
}

async function exportCookie() {
  exporting.value = true;
  try {
    const res = await exportAccountCookie();
    errorMsg.value = '';
    await loadOverview();
    MessagePlugin.success(`已导出 ${res.count} 项 Cookie`);
  } catch (e) {
    console.error('导出 Cookie 失败', e);
    errorMsg.value = e instanceof Error ? e.message : String(e);
  } finally {
    exporting.value = false;
  }
}

onMounted(loadOverview);
onBeforeUnmount(stopPoll);
</script>

<style scoped>
.account-tip {
  margin-bottom: 16px;
}

.account-form {
  max-width: 640px;
}

.account-progress {
  margin-top: 4px;
}

.account-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 18px;
  margin-top: 14px;
  color: #667085;
  font-size: 13px;
}
</style>
