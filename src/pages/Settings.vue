<template>
  <div class="settings-page">
    <!-- 运行状态 + 全局操作 -->
    <t-card bordered class="status-card">
      <template #title>运行状态</template>
      <template #actions>
        <t-space>
          <t-button variant="outline" :loading="testing" @click="testAi">测试 AI 连接</t-button>
          <t-button theme="primary" :loading="saving" @click="saveAll">保存全部</t-button>
        </t-space>
      </template>

      <div class="status-row">
        <t-tag :theme="runtime?.ai_cloud ? 'success' : 'danger'" variant="light">
          云端 AI：{{ runtime?.ai_cloud ? '已配置' : '未配置' }}
        </t-tag>
        <t-tag :theme="runtime?.ai_local ? 'success' : 'default'" variant="light">
          本地兜底：{{ runtime?.ai_local ? '已启用' : '未启用' }}
        </t-tag>
        <t-tag :theme="runtime?.obsidian_active ? 'success' : 'default'" variant="light">
          Obsidian：{{ runtime?.obsidian_active ? '已启用' : '未启用' }}
        </t-tag>
        <t-tag variant="light">今日 AI 调用：{{ runtime?.ai_usage_today ?? 0 }} 次</t-tag>
      </div>

      <t-alert
        v-if="testResult"
        :theme="testOk ? 'success' : 'error'"
        :close="false"
        class="test-alert"
        :message="testResult"
      />
      <t-alert
        theme="info"
        :close="false"
        message="密钥类字段留空表示不修改（显示为掩码）；配置保存在 config/config.json，保存后即时生效（主机/端口/数据目录需重启）。"
      />
    </t-card>

    <!-- 分组配置 -->
    <t-card v-for="g in groups" :key="g.title" bordered class="settings-card">
      <template #title>{{ g.title }}</template>
      <p v-if="g.desc" class="group-desc">{{ g.desc }}</p>

      <!-- 分组快捷开关（如「启用 GPU 加速」：一键切换多个字段） -->
      <div v-if="g.quickSwitch" class="quick-switch">
        <t-switch
          :value="quickSwitchOn(g)"
          @change="onQuickSwitch(g, $event)"
        />
        <span class="quick-label">{{ g.quickSwitch.label }}</span>
        <span class="quick-tip">{{ g.quickSwitch.tip }}</span>
      </div>

      <t-form label-align="top" class="settings-form">
        <t-form-item
          v-for="f in g.fields"
          :key="keyOf(g.section, f.path)"
          :label="f.label"
          :help="f.tip"
        >
          <t-input
            v-if="f.type === 'text'"
            v-model="form[keyOf(g.section, f.path)]"
            :placeholder="f.placeholder"
          />
          <t-input
            v-else-if="f.type === 'password'"
            v-model="form[keyOf(g.section, f.path)]"
            type="password"
            :placeholder="f.placeholder"
          />
          <t-input-number
            v-else-if="f.type === 'number'"
            v-model="form[keyOf(g.section, f.path)]"
            style="width: 100%"
          />
          <t-switch v-else-if="f.type === 'switch'" v-model="form[keyOf(g.section, f.path)]" />
          <t-select
            v-else-if="f.type === 'select'"
            v-model="form[keyOf(g.section, f.path)]"
            :options="f.options"
          />
          <t-select
            v-else-if="f.type === 'multi'"
            v-model="form[keyOf(g.section, f.path)]"
            :options="f.options"
            multiple
            clearable
          />
        </t-form-item>
      </t-form>
    </t-card>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue';
import { MessagePlugin } from 'tdesign-vue-next';
import {
  getSettings,
  testAiConnection,
  updateSettings,
  type SettingsResponse,
} from '../api/client';
import { SETTINGS_GROUPS, type FieldDef, type GroupDef } from './settingsSchema';

const groups = SETTINGS_GROUPS;
const data = ref<SettingsResponse | null>(null);
const runtime = computed(() => data.value?.runtime ?? null);
const form = reactive<Record<string, any>>({});

const saving = ref(false);
const testing = ref(false);
const testResult = ref('');
const testOk = ref(false);

function keyOf(section: string, path: string[]) {
  return `${section}.${path.join('.')}`;
}

function isKeepingSecret(f: FieldDef, value: unknown): boolean {
  if (!f.secretKey) return false;
  const mask = data.value?.mask ?? '****';
  const s = String(value ?? '');
  return s === '' || s.includes(mask);
}

/** 快捷开关当前是否为「开」 */
function quickSwitchOn(g: GroupDef): boolean {
  const qs = g.quickSwitch;
  if (!qs) return false;
  return form[keyOf(g.section, qs.when.path)] === qs.when.equals;
}

/** 模板事件入口（避免内联箭头函数的隐式 any） */
function onQuickSwitch(g: GroupDef, value: unknown) {
  toggleQuickSwitch(g, Boolean(value));
}

/** 切换快捷开关：按 schema 批量写入对应字段（仍需点「保存全部」落库） */
function toggleQuickSwitch(g: GroupDef, on: boolean) {
  const qs = g.quickSwitch;
  if (!qs) return;
  for (const item of (on ? qs.on : qs.off)) {
    form[keyOf(g.section, item.path)] = item.value;
  }
  MessagePlugin.info(
    on ? `已切换为「${qs.label}」的开配置，记得点「保存全部」`
       : `已切换为「${qs.label}」的关配置，记得点「保存全部」`,
  );
}

async function load() {
  try {
    const res = await getSettings();
    data.value = res;
    const next: Record<string, any> = {};
    for (const g of groups) {
      for (const f of g.fields) {
        let node: any = res.config?.[g.section];
        for (const p of f.path) node = node?.[p];
        if (node === undefined || node === null) {
          if (f.type === 'switch') node = false;
          else if (f.type === 'multi') node = [];
          else if (f.type === 'number') node = 0;
          else node = '';
        }
        next[keyOf(g.section, f.path)] = node;
      }
    }
    Object.keys(form).forEach((k) => delete form[k]);
    Object.assign(form, next);
  } catch (e) {
    console.error('加载系统设置失败', e);
  }
}

function collectPatch(): Record<string, any> {
  const patch: Record<string, any> = {};
  for (const g of groups) {
    for (const f of g.fields) {
      const val = form[keyOf(g.section, f.path)];
      if (isKeepingSecret(f, val)) continue;   // 留空/掩码 → 不提交
      patch[g.section] = patch[g.section] ?? {};
      let node = patch[g.section];
      for (let i = 0; i < f.path.length - 1; i++) {
        node[f.path[i]] = node[f.path[i]] ?? {};
        node = node[f.path[i]];
      }
      node[f.path[f.path.length - 1]] = val;
    }
  }
  return patch;
}

async function saveAll() {
  saving.value = true;
  try {
    const res = await updateSettings(collectPatch());
    data.value = res.settings;
    MessagePlugin.success(
      res.applied.length ? `已保存：${res.applied.join('、')}` : '没有需要保存的改动',
    );
    await load();
  } catch (e) {
    console.error('保存系统设置失败', e);
    MessagePlugin.error(e instanceof Error ? e.message : String(e));
  } finally {
    saving.value = false;
  }
}

async function testAi() {
  testing.value = true;
  testResult.value = '';
  try {
    const res = await testAiConnection();
    testOk.value = true;
    testResult.value = `连接成功（provider=${res.provider}）返回：${JSON.stringify(res.sample).slice(0, 120)}`;
    MessagePlugin.success('AI 连接正常');
  } catch (e) {
    testOk.value = false;
    testResult.value = e instanceof Error ? e.message : String(e);
    MessagePlugin.error('AI 连接失败');
  } finally {
    testing.value = false;
  }
}

onMounted(load);
</script>

<style scoped>
.settings-page {
  display: grid;
  gap: 20px;
}

.status-row {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-bottom: 14px;
}

.test-alert {
  margin-bottom: 12px;
}

.group-desc {
  margin: 0 0 14px;
  color: #667085;
  font-size: 13px;
  line-height: 1.6;
}

.quick-switch {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
  padding: 12px 14px;
  margin-bottom: 14px;
  background: #f8fafc;
  border: 1px solid #edf1f7;
  border-radius: 8px;
}

.quick-label {
  color: #101828;
  font-size: 13px;
  font-weight: 600;
}

.quick-tip {
  color: #667085;
  font-size: 12px;
  line-height: 1.6;
}

.settings-form {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 4px 24px;
  max-width: 1080px;
}
</style>
