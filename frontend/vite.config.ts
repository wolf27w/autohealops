import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Vite 配置：dev 环境代理 /api/v1 到 api-server
export default defineConfig({
  plugins: [react()],
  define: {
    // 构建/启动时间戳（用于确认浏览器实际加载的版本）
    __BUILD_TS__: JSON.stringify(new Date().toISOString()),
  },
  server: {
    port: 5173,
    proxy: {
      '/api/v1': {
        target: 'http://127.0.0.1:8080',
        changeOrigin: true,
        ws: true,
      },
    },
  },
})
