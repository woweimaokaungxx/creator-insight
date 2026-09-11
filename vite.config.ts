import { defineConfig } from 'vite';
import vue from '@vitejs/plugin-vue';
import { fileURLToPath, URL } from 'node:url';

// 后端 FastAPI 地址：开发时通过 /api 代理转发，避免跨域
const API_TARGET = 'http://127.0.0.1:8781';

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: API_TARGET,
        changeOrigin: true,
      },
    },
  },
  build: {
    // 产物输出到后端 static，由 FastAPI 直接托管（与现有 index.html 并存）
    outDir: 'static/vue',
    emptyOutDir: true,
  },
});
