<template>
  <div class="library-page">
    <t-card bordered>
      <template #title>视频库</template>
      <template #actions>
        <t-radio-group v-model="searchMode" variant="default-filled" style="margin-right:8px">
          <t-radio-button value="keyword">关键词</t-radio-button>
          <t-radio-button value="semantic">语义</t-radio-button>
        </t-radio-group>
        <t-input v-model="keyword" :placeholder="searchMode === 'semantic' ? '输入自然语言搜索（如：讲火箭的视频）' : '搜索标题…'" clearable style="width:260px" @enter="doSearch" />
        <t-select v-if="searchMode === 'keyword'" v-model="platformFilter" style="width:120px;margin-left:8px" :options="platformOptions" />
        <t-button theme="primary" variant="outline" :loading="semanticLoading" @click="doSearch">搜索</t-button>
      </template>

      <div class="video-grid">
        <template v-if="searchMode === 'keyword'">
          <div v-for="item in filtered" :key="item.id" class="video-card" @click="openUrl(item.url)">
            <div class="vc-title">{{ item.title || '（无标题）' }}</div>
            <div class="vc-meta">
              <span>{{ item.platform === 'douyin' ? '抖音' : 'B站' }} · {{ item.creator_name || '未知博主' }}</span>
              <span v-if="item.published_at">{{ fmtDate(item.published_at) }}</span>
            </div>
            <div class="vc-stats">
              <span title="点赞"><span class="s-ico">👍</span>{{ fmtCount(item.digg_count) }}</span>
              <span title="评论"><span class="s-ico">💬</span>{{ fmtCount(item.comment_count) }}</span>
              <span title="转发"><span class="s-ico">↗</span>{{ fmtCount(item.share_count) }}</span>
              <span title="收藏"><span class="s-ico">★</span>{{ fmtCount(item.collect_count) }}</span>
            </div>
            <div class="vc-actions">
              <t-button size="small" variant="text" @click.stop="openReader(item)">
                阅读
              </t-button>
            </div>
          </div>
        </template>
        <template v-else>
          <div v-for="(hit, i) in semanticResults" :key="i" class="video-card" @click="openUrl(hit.url)">
            <div class="vc-title">{{ hit.title || '（无标题）' }}</div>
            <div v-if="hit.target_type === 'prediction' && hit.video_title" class="vc-video-from">来自视频：{{ hit.video_title }}</div>
            <div class="vc-meta">
              <span>{{ hit.creator || '' }} · 相似度 {{ hit.score }}</span>
              <span>{{ hit.target_type === 'content' ? '视频' : '预测' }}</span>
            </div>
            <div v-if="hit.target_type === 'prediction'" class="vc-raw" :title="hit.raw_text">原话：{{ hit.raw_text }}</div>
            <div v-if="hit.indexed_text && hit.target_type === 'content'" class="vc-excerpt">{{ hit.indexed_text }}</div>
            <div class="vc-stats">
              <span v-if="hit.digg_count != null" title="点赞">👍 {{ fmtCount(hit.digg_count) }}</span>
              <span v-if="hit.comment_count != null" title="评论">💬 {{ fmtCount(hit.comment_count) }}</span>
            </div>
          </div>
        </template>
      </div>
      <t-empty v-if="(searchMode === 'keyword' ? !filtered.length : !semanticResults.length)" title="暂无结果" description="试试其他关键词，或切换到语义搜索" />
    </t-card>

    <!-- V0.15 内容阅读：AI 总结（版本化）/ 简体校对版逐字稿 -->
    <t-dialog v-model:visible="readerVisible" :header="readerHeader" width="820px">
      <template #footer>
        <div class="reader-footer">
          <t-button theme="danger" variant="outline" @click="handleDelete">删除此视频</t-button>
        </div>
      </template>
      <t-tabs v-model="readerTab" @change="onTabChange">
        <t-tab-panel value="summary" label="AI 总结">
          <div v-if="summaryLoading" class="sm-loading">加载中…</div>
          <template v-else-if="currentSummary">
            <div class="sm-meta">
              <t-tag size="small" theme="primary" variant="light">v{{ currentSummary.version }}</t-tag>
              <t-tag v-if="currentSummary.model" size="small" variant="light">{{ currentSummary.model }}</t-tag>
              <span class="sm-time">{{ fmtDateTime(currentSummary.created_at) }}</span>
              <span v-if="currentSummary.word_count" class="sm-time">{{ currentSummary.word_count }} 字</span>
              <t-button size="small" variant="text" @click="copyText(currentSummary.summary)">
                复制正文
              </t-button>
              <t-button v-if="summaryVersions.length > 1" size="small" variant="text"
                        @click="showVersions = !showVersions">
                {{ showVersions ? '收起历史版本' : `历史版本（${summaryVersions.length}）` }}
              </t-button>
            </div>
            <div class="sm-body">{{ currentSummary.summary }}</div>
            <div v-if="currentSummary.key_points?.length" class="sm-points">
              <div class="sm-label">要点</div>
              <ul>
                <li v-for="(p, i) in currentSummary.key_points" :key="i">{{ p }}</li>
              </ul>
            </div>
            <div v-if="showVersions" class="sm-versions">
              <div class="sm-versions-label">历史版本（点击切换查看，旧版本不会被覆盖）</div>
              <div v-for="v in summaryVersions" :key="v.id" class="sm-ver-item"
                   :class="{ active: v.id === currentSummary.id }" @click="selectVersion(v)">
                <span>v{{ v.version }}</span>
                <span class="sm-ver-model">{{ v.model || '未知模型' }}</span>
                <span class="sm-time">{{ fmtDateTime(v.created_at) }}</span>
                <t-tag v-if="v.is_current" size="small" theme="success" variant="light">当前</t-tag>
              </div>
            </div>
          </template>
          <t-empty v-else title="暂无 AI 总结"
                   description="该视频尚未生成总结（老数据或处理失败）。重新处理该视频即可补上。" />
        </t-tab-panel>

        <t-tab-panel value="transcript" label="简体校对版">
          <div v-if="transcriptLoading" class="sm-loading">加载中…</div>
          <template v-else-if="transcriptText">
            <div class="sm-meta">
              <t-tag size="small" theme="primary" variant="light">{{ transcriptKindLabel }}</t-tag>
              <span class="sm-time">{{ transcriptCharCount }} 字</span>
              <span v-if="transcriptSourceLabel" class="sm-time">来源：{{ transcriptSourceLabel }}</span>
              <t-button size="small" variant="text" @click="copyText(transcriptText)">复制全文</t-button>
            </div>
            <div class="tx-body">{{ transcriptText }}</div>
          </template>
          <t-empty v-else title="暂无逐字稿"
                   description="该视频可能只抓取了目录、尚未处理。处理后可在此阅读。" />
        </t-tab-panel>
      </t-tabs>
    </t-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { DialogPlugin, MessagePlugin } from 'tdesign-vue-next';
import {
  deleteContent,
  getContentSummary,
  getContentSummaryVersions,
  getContentTranscript,
  getContents,
  previewDeleteContent,
  semanticSearch,
  type ContentItem,
  type ContentSummary,
  type SemanticResult,
} from '../api/client';

const items = ref<ContentItem[]>([]);
const keyword = ref('');
const platformFilter = ref('all');
const searchMode = ref<'keyword' | 'semantic'>('keyword');
const semanticResults = ref<SemanticResult[]>([]);
const semanticLoading = ref(false);

const platformOptions = [
  { label: '全部平台', value: 'all' },
  { label: '抖音', value: 'douyin' },
  { label: 'B站', value: 'bilibili' },
];

const filtered = computed(() => {
  const kw = keyword.value.trim().toLowerCase();
  return items.value.filter((it) => {
    if (platformFilter.value !== 'all' && it.platform !== platformFilter.value) return false;
    if (kw && !(it.title || '').toLowerCase().includes(kw)) return false;
    return true;
  });
});

function fmtCount(n: number | null | undefined): string {
  if (n == null) return '-';
  if (n >= 10000) return `${(n / 10000).toFixed(1)}万`;
  return String(n);
}

function fmtDate(ts: number): string {
  const d = new Date(ts * 1000);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

function fmtDateTime(ts: number): string {
  const d = new Date(ts * 1000);
  const hh = String(d.getHours()).padStart(2, '0');
  const mm = String(d.getMinutes()).padStart(2, '0');
  return `${fmtDate(ts)} ${hh}:${mm}`;
}

// ─── V0.15 内容阅读：AI 总结（版本化）/ 简体校对版逐字稿 ──
// 两个数据源都按需拉取：列表接口不返回长文本，打开弹窗只拉总结（轻），
// 切到「简体校对版」标签时才拉逐字稿（可能几万字）。
const readerVisible = ref(false);
const readerHeader = ref('阅读');
const readerTab = ref<'summary' | 'transcript'>('summary');
let readerItemId = '';

const summaryLoading = ref(false);
const currentSummary = ref<ContentSummary | null>(null);
const summaryVersions = ref<ContentSummary[]>([]);
const showVersions = ref(false);

const transcriptLoading = ref(false);
const transcriptText = ref('');
const transcriptKind = ref<'simplified' | 'raw'>('simplified');
const transcriptCharCount = ref(0);
const transcriptSource = ref('');
let transcriptLoadedFor = '';

const transcriptKindLabel = computed(() =>
  transcriptKind.value === 'simplified' ? 'AI 简体校对版' : '原始逐字稿（未校对）',
);
const transcriptSourceLabel = computed(() => {
  if (transcriptSource.value === 'whisper_local') return 'Whisper 本地转写';
  if (transcriptSource.value === 'platform_subtitle') return '平台字幕';
  return transcriptSource.value || '';
});

async function openReader(item: ContentItem) {
  readerHeader.value = item.title || '（无标题）';
  readerVisible.value = true;
  readerTab.value = 'summary';
  showVersions.value = false;
  currentSummary.value = null;
  summaryVersions.value = [];
  transcriptText.value = '';
  transcriptLoadedFor = '';
  const id = item.id;
  readerItemId = id;

  summaryLoading.value = true;
  try {
    const [cur, vers] = await Promise.all([
      getContentSummary(id),
      getContentSummaryVersions(id),
    ]);
    currentSummary.value = cur.summary;
    summaryVersions.value = vers.versions || [];
  } catch (e) {
    console.error('加载 AI 总结失败', e);
  } finally {
    summaryLoading.value = false;
  }
}

function selectVersion(v: ContentSummary) {
  currentSummary.value = v;
}

async function onTabChange(value: unknown) {
  if (value === 'transcript') await loadTranscript();
}

/** 逐字稿体量大，切到该标签时才按需拉取（同一视频只拉一次） */
async function loadTranscript() {
  if (!readerItemId || transcriptLoadedFor === readerItemId) return;
  transcriptLoading.value = true;
  try {
    const res = await getContentTranscript(readerItemId);
    transcriptText.value = res.text || '';
    transcriptKind.value = res.text_kind || 'simplified';
    transcriptCharCount.value = res.char_count || 0;
    transcriptSource.value = res.source || '';
    transcriptLoadedFor = readerItemId;
  } catch (e) {
    console.error('加载逐字稿失败', e);
  } finally {
    transcriptLoading.value = false;
  }
}

async function copyText(text: string) {
  if (!text) return;
  try {
    await navigator.clipboard.writeText(text);
    MessagePlugin.success('已复制到剪贴板');
  } catch {
    MessagePlugin.warning('复制失败，请手动选择文本');
  }
}

/** 删除视频：先拉影响面 → 二次确认 → 执行（不可恢复） */
async function handleDelete() {
  const id = readerItemId;
  if (!id) return;

  let pv;
  try {
    pv = await previewDeleteContent(id);
  } catch (e) {
    MessagePlugin.error(`获取删除影响面失败：${(e as Error).message}`);
    return;
  }

  const parts: string[] = [];
  if (pv.predictions) parts.push(`预测 ${pv.predictions} 条`);
  if (pv.claims) parts.push(`观点 ${pv.claims} 条`);
  if (pv.evidences) parts.push(`证据 ${pv.evidences} 条`);
  if (pv.summaries) parts.push(`AI 总结 ${pv.summaries} 条`);
  if (pv.transcripts) parts.push('逐字稿');
  const title = pv.title || '（无标题）';
  const body = parts.length
    ? `《${title}》将连同以下数据一并删除（不可恢复）：${parts.join('、')}。`
    : `《${title}》没有分析数据，删除后不可恢复。`;

  const dialog = DialogPlugin.confirm({
    header: '确认删除该视频？',
    body,
    confirmBtn: { content: '确认删除', theme: 'danger' },
    cancelBtn: '取消',
    onConfirm: async () => {
      try {
        const res = await deleteContent(id);
        MessagePlugin.success(
          `已删除（连带预测 ${res.deleted.predictions} / 观点 ${res.deleted.claims}）`,
        );
        readerVisible.value = false;
        await reload();
      } catch (e) {
        MessagePlugin.error(`删除失败：${(e as Error).message}`);
      }
      dialog.destroy();
    },
  });
}

function openUrl(url?: string) {
  if (url) window.open(url, '_blank');
}

async function doSearch() {
  if (searchMode.value === 'semantic') {
    if (!keyword.value.trim()) return;
    semanticLoading.value = true;
    try {
      const data = await semanticSearch(keyword.value.trim(), 20);
      semanticResults.value = data.results || [];
    } catch (e) {
      console.error('语义检索失败', e);
      semanticResults.value = [];
    } finally {
      semanticLoading.value = false;
    }
  }
  /* keyword 模式由 computed filtered 自动响应 */
}

async function reload() {
  try {
    items.value = await getContents(50);
  } catch (e) {
    console.error('加载视频库失败', e);
    items.value = [];
  }
}

onMounted(reload);
</script>

<style scoped>
/* V0.15 AI 总结弹窗 */
.vc-actions {
  margin-top: 8px;
  display: flex;
  justify-content: flex-end;
}
.sm-loading {
  padding: 24px 0;
  text-align: center;
  color: #8a94a6;
}
.sm-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 12px;
}
.sm-time {
  color: #8a94a6;
  font-size: 12px;
}
.sm-body {
  white-space: pre-wrap;
  line-height: 1.75;
  color: #2c3345;
  font-size: 14px;
  max-height: 52vh;
  overflow-y: auto;
  padding-right: 6px;
}
.reader-footer {
  display: flex;
  justify-content: flex-end;
}
/* 逐字稿正文：长文阅读区（独立滚动，不撑破弹窗） */
.tx-body {
  white-space: pre-wrap;
  line-height: 1.85;
  color: #2c3345;
  font-size: 14px;
  max-height: 58vh;
  overflow-y: auto;
  padding-right: 6px;
}
.sm-points {
  margin-top: 16px;
}
.sm-label {
  font-weight: 600;
  margin-bottom: 6px;
  color: #2c3345;
}
.sm-points ul {
  margin: 0;
  padding-left: 20px;
  color: #55617a;
  line-height: 1.9;
  font-size: 13px;
}
.sm-versions {
  margin-top: 16px;
  border-top: 1px solid #edf1f7;
  padding-top: 12px;
}
.sm-versions-label {
  font-size: 12px;
  color: #8a94a6;
  margin-bottom: 8px;
}
.sm-ver-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 10px;
  border-radius: 6px;
  cursor: pointer;
  font-size: 13px;
}
.sm-ver-item:hover {
  background: #f5f8ff;
}
.sm-ver-item.active {
  background: #eef4ff;
}
.sm-ver-model {
  color: #55617a;
}
.video-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  gap: 16px;
  margin-top: 16px;
}
.video-card {
  border: 1px solid #edf1f7;
  border-radius: 10px;
  padding: 14px;
  cursor: pointer;
  transition: box-shadow 0.2s;
  background: #fff;
}
.video-card:hover {
  box-shadow: 0 4px 14px rgba(0, 0, 0, 0.08);
}
.vc-title {
  font-weight: 600;
  color: #101828;
  font-size: 14px;
  line-height: 1.4;
  height: 40px;
  overflow: hidden;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}
.vc-meta {
  display: flex;
  justify-content: space-between;
  color: #98a2b3;
  font-size: 12px;
  margin-top: 8px;
}
.vc-video-from {
  color: #667085;
  font-size: 12px;
  margin-top: 4px;
  line-height: 1.5;
  display: -webkit-box;
  -webkit-line-clamp: 1;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.vc-raw {
  color: #98a2b3;
  font-size: 12px;
  margin-top: 6px;
  line-height: 1.5;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.vc-excerpt {
  color: #667085;
  font-size: 12px;
  margin-top: 8px;
  line-height: 1.5;
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.vc-stats {
  display: flex;
  gap: 14px;
  margin-top: 12px;
  color: #475467;
  font-size: 13px;
}
.vc-stats span {
  display: inline-flex;
  align-items: center;
  gap: 3px;
}
.s-ico {
  font-style: normal;
}
.vc-progress {
  margin-top: 10px;
}
.has-transcript {
  color: #12b886;
  font-size: 12px;
}
.no-transcript {
  color: #98a2b3;
  font-size: 12px;
}
</style>
