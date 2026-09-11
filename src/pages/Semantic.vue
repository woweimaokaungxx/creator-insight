<template>
  <div class="semantic-page">
    <t-card bordered>
      <template #title>语义检索设置</template>
      <t-alert theme="info" message="用本地 embedding 模型（如 Qwen3-Embedding-0.6B）给视频内容生成向量，实现自然语言智能检索。参考 douyin-creator-distill。" :close="false" class="module-alert" />

      <div class="section-title">模型选择</div>
      <t-list split>
        <t-list-item v-for="m in settings.models" :key="m.id">
          <t-list-item-meta :title="`${m.label}（${m.id}）`"
                            :description="`${m.summary || ''} · 维度 ${m.dimension} · ${m.approximateSize || ''}`">
            <template #avatar>
              <t-tag :theme="m.selected ? 'primary' : 'default'" variant="light">
                {{ m.installed ? (m.selected ? '当前' : '已安装') : '未安装' }}
              </t-tag>
            </template>
          </t-list-item-meta>
          <template #action>
            <t-space>
              <t-button v-if="!m.installed" size="small" theme="primary" variant="outline" @click="downloadModel(m.id)">下载</t-button>
              <t-button v-if="m.installed && !m.selected" size="small" variant="outline" @click="selectModel(m.id)">启用</t-button>
            </t-space>
          </template>
        </t-list-item>
      </t-list>
      <div class="model-root">模型目录：{{ settings.modelRoot }}</div>
    </t-card>

    <t-card bordered>
      <template #title>语义索引</template>
      <div class="index-stats">
        <div class="mini-stat"><span>已索引视频</span><strong>{{ status.indexedWorks ?? '—' }}</strong></div>
        <div class="mini-stat"><span>已索引片段</span><strong>{{ status.indexedChunks ?? '—' }}</strong></div>
        <div class="mini-stat"><span>视频总数</span><strong>{{ status.totalContents ?? '—' }}</strong></div>
      </div>
      <t-space class="module-alert">
        <t-button theme="primary" :loading="indexing" @click="buildIndex">建立视频索引</t-button>
        <t-button variant="outline" :loading="indexingPred" @click="buildPredIndex">建立预测索引</t-button>
        <t-button variant="outline" @click="load">刷新状态</t-button>
      </t-space>
      <div v-if="indexMsg" class="index-msg">{{ indexMsg }}</div>
    </t-card>

    <t-card bordered>
      <template #title>使用说明</template>
      <ol class="usage-list">
        <li>在「视频库」页切换到<b>语义</b>搜索，输入自然语言（如"讲火箭的视频"）即可检索。</li>
        <li>语义索引需先选择已安装模型并点击「建立视频索引」。</li>
        <li>模型在设置中心下载；可选轻量（bge-small-zh）或高精度（Qwen3-Embedding-0.6B）。</li>
        <li>向量在本机生成，不上传；只有精排才调用云端模型。</li>
      </ol>
    </t-card>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue';
import {
  getSemanticSettings,
  semanticIndexContent,
  semanticIndexPredictions,
  semanticSelectModel,
  semanticDownloadModel,
  type SemanticSettings,
} from '../api/client';

const settings = ref<SemanticSettings>({
  activeModel: '', modelRoot: '', runtime: {}, models: [],
});
const status = ref<Record<string, unknown>>({});
const indexing = ref(false);
const indexingPred = ref(false);
const indexMsg = ref('');

async function load() {
  try {
    settings.value = await getSemanticSettings();
    const modelId = settings.value.activeModel;
    const model = settings.value.models.find((m) => m.id === modelId);
    status.value = {
      indexedWorks: settings.value.indexedCount ?? '—',
      indexedChunks: '—',
      totalContents: '—',
      modelInstalled: model?.installed,
    };
  } catch (e) {
    console.error('加载语义设置失败', e);
  }
}

async function selectModel(id: string) {
  try {
    settings.value = await semanticSelectModel(id);
    await load();
  } catch (e) {
    console.error('切换模型失败', e);
  }
}

async function downloadModel(id: string) {
  try {
    await semanticDownloadModel(id);
    indexMsg.value = '已开始后台下载模型（ModelScope 国内源），完成后请刷新。';
  } catch (e) {
    indexMsg.value = `下载失败：${e instanceof Error ? e.message : e}`;
  }
}

async function buildIndex() {
  indexing.value = true;
  indexMsg.value = '正在建立视频语义索引（首次可能需要几分钟）…';
  try {
    const r = await semanticIndexContent();
    indexMsg.value = `索引完成：${r.indexed} 个分块已建立。`;
    await load();
  } catch (e) {
    indexMsg.value = `索引失败：${e instanceof Error ? e.message : e}`;
  } finally {
    indexing.value = false;
  }
}

async function buildPredIndex() {
  indexingPred.value = true;
  indexMsg.value = '正在建立预测语义索引…';
  try {
    const r = await semanticIndexPredictions();
    indexMsg.value = `预测索引完成：${r.indexed} 条。`;
  } catch (e) {
    indexMsg.value = `索引失败：${e instanceof Error ? e.message : e}`;
  } finally {
    indexingPred.value = false;
  }
}

onMounted(load);
</script>

<style scoped>
.semantic-page {
  display: grid;
  gap: 20px;
}
.module-alert {
  margin-bottom: 8px;
}
.section-title {
  color: #344054;
  font-weight: 600;
  font-size: 14px;
  margin: 16px 0 8px;
}
.model-root {
  color: #98a2b3;
  font-size: 12px;
  margin-top: 12px;
}
.index-stats {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 16px;
  margin-bottom: 8px;
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
.index-msg {
  margin-top: 12px;
  color: #12b886;
  font-size: 13px;
}
.usage-list {
  color: #475467;
  line-height: 2;
  padding-left: 20px;
}
</style>
