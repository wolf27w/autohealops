import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { ConfigProvider } from 'antd'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import zhCN from 'antd/locale/zh_CN'
import App from './App'
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

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <ConfigProvider
        locale={zhCN}
        theme={{
          token: {
            colorPrimary: '#12b981',
            colorSuccess: '#12b981',
            colorInfo: '#0ea5e9',
            colorWarning: '#f59e0b',
            colorError: '#ef4444',
            colorLink: '#048c63',
            borderRadius: 10,
            colorBgLayout: '#eef4ef',
            colorText: '#0f2018',
            colorTextSecondary: '#5a7368',
            colorBorder: '#d9e6de',
            colorBorderSecondary: '#eaf2ec',
            fontSize: 13,
            fontFamily:
              '"PingFang SC","Hiragino Sans GB","Microsoft YaHei",system-ui,-apple-system,"Segoe UI",sans-serif',
          },
          components: {
            Layout: {
              siderBg: '#0f2a20',
              bodyBg: '#eef4ef',
              headerBg: '#ffffff',
              headerHeight: 56,
              headerPadding: '0 18px',
            },
            Menu: {
              darkItemBg: 'transparent',
              darkSubMenuItemBg: 'transparent',
              darkItemColor: '#b6cdc0',
              darkItemHoverBg: 'rgba(255,255,255,0.06)',
              darkItemHoverColor: '#ffffff',
              darkItemSelectedBg: '#12b981',
              darkItemSelectedColor: '#ffffff',
              itemBorderRadius: 8,
              itemMarginInline: 8,
              itemHeight: 38,
              itemMarginBlock: 2,
            },
            Table: {
              headerBg: '#f4faf6',
              headerColor: '#5a7368',
              headerSplitColor: 'transparent',
              rowHoverBg: '#ecfdf5',
              borderColor: '#eaf2ec',
              cellPaddingBlock: 10,
              cellPaddingInline: 14,
              cellPaddingBlockSM: 8,
              cellPaddingInlineSM: 12,
            },
            Card: { borderRadiusLG: 12 },
            Tag: { borderRadiusSM: 20 },
            Button: { borderRadius: 9 },
          },
        }}
      >
        <BrowserRouter>
          <App />
        </BrowserRouter>
      </ConfigProvider>
    </QueryClientProvider>
  </React.StrictMode>,
)
