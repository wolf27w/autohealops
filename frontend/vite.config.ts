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
  build: {
    chunkSizeWarningLimit: 1200,
    rollupOptions: {
      output: {
        // M5 打包优化：按库拆 vendor 分包，配合路由 lazy 缩短首屏加载
        manualChunks(id: string) {
          if (!id.includes('node_modules')) return
          if (id.includes('@ant-design/charts') || id.includes('@antv/') || id.includes('echarts')) return 'charts'
          if (id.includes('/antd/') || id.includes('@ant-design/icons') || id.includes('/rc-')) return 'antd'
          if (id.includes('@xterm/')) return 'xterm'
          if (id.includes('@tanstack/react-query')) return 'query'
          if (id.includes('react-dom') || id.includes('/react/') || id.includes('react-router') || id.includes('/scheduler/')) return 'react-vendor'
          return 'vendor'
        },
      },
    },
  },
})
