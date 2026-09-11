// V0.10 全局任务进度共享 Store（模块级单例）
//
// 问题：Dashboard.vue 把任务轮询放在组件内，切页/刷新后进度丢失。
// 方案：把任务列表 + 轮询提升到模块级单例，由 App 常驻层启动轮询，
//       任何组件 useIngestTasks() 拿到同一份响应式状态 → 切页/刷新仍能恢复。
import { computed, reactive, ref } from 'vue';
import { getTasks, type IngestTask } from '../api/client';

// 单例状态（模块顶层，不随组件销毁）
const POLL_INTERVAL_MS = 15000;

const tasks = ref<IngestTask[]>([]);
const lastError = ref<string>('');
let timer: number | null = null;

// 后端任务阶段 → 中文（与 Dashboard 原 STAGE_CN 一致）
const STAGE_CN: Record<string, string> = {
  parse: '解析链接',
  fetch_meta: '抓取元数据',
  transcript: '获取字幕',
  download: '下载视频',
  transcribe: 'Whisper 转写',
  simplify: '简体校对',
  summarize: 'AI 总结',
  claims: '观点抽取',
  predictions: '预测抽取',
  analyze: 'AI 分析',
  done: '完成',
};

export function stageToCn(stage: string): string {
  return STAGE_CN[stage] || stage || '';
}

export function isRunningStatus(status: string): boolean {
  return status === 'pending' || status === 'running';
}

export function useIngestTasks() {
  const isPolling = ref(false);

  /** 拉取一次后端任务列表（最新在前） */
  async function refresh() {
    try {
      const data = await getTasks(20);
      tasks.value = data.tasks ?? [];
      lastError.value = '';
    } catch (e) {
      console.error('[tasks] 刷新失败', e);
      lastError.value = String(e);
    }
  }

  /** 启动周期轮询（幂等：已在轮询则不重复） */
  function startPolling() {
    if (timer !== null) return;
    isPolling.value = true;
    void refresh();
    timer = window.setInterval(() => {
      void refresh();
    }, POLL_INTERVAL_MS);
  }

  /** 停止轮询（App 卸载时调用） */
  function stopPolling() {
    if (timer !== null) {
      window.clearInterval(timer);
      timer = null;
    }
    isPolling.value = false;
  }

  /** 页面刷新/切回后：确保在轮询并立即同步一次 */
  async function resume() {
    startPolling();
    await refresh();
  }

  /** 进行中的任务（最新一条 running/pending；后端列表按 created_at 倒序，取第一条） */
  const activeTask = computed<IngestTask | null>(() => {
    return tasks.value.find((t) => isRunningStatus(t.status)) ?? null;
  });

  /** 是否有任务在跑 */
  const hasRunning = computed(() => {
    return tasks.value.some((t) => isRunningStatus(t.status));
  });

  /** 全量任务（倒序），供「近期处理任务」列表使用 */
  const allTasks = computed(() => tasks.value);

  /** 最近任务（取前 8 条，供展示列表） */
  const recentTasks = computed(() => tasks.value.slice(0, 8));

  /** 移除某任务（保留在内存即可，后端下次轮询会恢复；此处用于 UI 立即收起） */
  function clearAll() {
    tasks.value = [];
  }

  return {
    isPolling,
    lastError,
    activeTask,
    hasRunning,
    allTasks,
    recentTasks,
    refresh,
    startPolling,
    stopPolling,
    resume,
    clearAll,
  };
}
