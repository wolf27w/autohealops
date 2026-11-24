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
        // 打包分包：只拆「叶子」类大库（图表/终端/查询层），
        // antd 与 react 交给 Rollup 自动分块——手工把 antd 和 react-vendor 拆开会产生
        // 跨 chunk 循环依赖（vendor→antd→vendor），运行时 React 命名空间为 undefined，
        // 报 `Cannot read properties of undefined (reading 'version')`，整页白屏。
        manualChunks(id: string) {
          if (!id.includes('node_modules')) return
          if (id.includes('@ant-design/charts') || id.includes('@antv/') || id.includes('echarts')) return 'charts'
          if (id.includes('@xterm/')) return 'xterm'
          if (id.includes('@tanstack/react-query')) return 'query'
          return
        },
      },
    },
  },
})
