import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { ConfigProvider } from 'antd'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import zhCN from 'antd/locale/zh_CN'
import App from './App'
import { ThemeProvider, useThemeMode, buildTheme } from './utils/theme'
import './index.css'

// react-query 客户端（A3 工程化）：默认不自动 refetch、请求重试 1 次、缓存 30s
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      staleTime: 30_000,
      refetchOnWindowFocus: false,
    },
  },
})

// 内层：读取主题模式，驱动 ConfigProvider（theme 随 mode 动态切换）
function ThemedApp() {
  const { mode } = useThemeMode()
  return (
    <ConfigProvider locale={zhCN} theme={buildTheme(mode)}>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </ConfigProvider>
  )
}

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <ThemeProvider>
        <ThemedApp />
      </ThemeProvider>
    </QueryClientProvider>
  </React.StrictMode>,
)
