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
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { getContents, semanticSearch, type ContentItem, type SemanticResult } from '../api/client';

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
