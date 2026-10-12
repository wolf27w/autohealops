// 主题基础设施：浅色（现有浅绿主题） + 深色（监控大屏风），localStorage 持久化
// 用法：main.tsx 用 ThemeProvider + buildTheme(mode) 驱动 ConfigProvider；
//      页面用 useThemeMode() 读/切模式；index.css 用 html[data-theme='dark'] 覆盖硬编码底色
import { createContext, useContext, useLayoutEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { theme as antdTheme } from 'antd'
import type { ThemeConfig } from 'antd'

export type ThemeMode = 'light' | 'dark'

const STORAGE_KEY = 'api_ops_theme_mode'

const FONT_FAMILY =
  '"PingFang SC","Hiragino Sans GB","Microsoft YaHei",system-ui,-apple-system,"Segoe UI",sans-serif'

// 浅色主题：沿用现有「换肤 A」浅绿主题（token 与 main.tsx 原配置一致）
const lightTheme: ThemeConfig = {
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
    fontFamily: FONT_FAMILY,
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
}

// 深色主题：深蓝黑底 + 翡翠绿强调（监控/大屏风）
const darkTheme: ThemeConfig = {
  algorithm: antdTheme.darkAlgorithm,
  token: {
    colorPrimary: '#34d399',
    colorSuccess: '#34d399',
    colorInfo: '#38bdf8',
    colorWarning: '#fbbf24',
    colorError: '#f87171',
    colorLink: '#34d399',
    borderRadius: 10,
    colorBgLayout: '#0b1220',
    colorBgContainer: '#111a2c',
    colorText: '#e2e8f0',
    colorTextSecondary: '#94a3b8',
    colorBorder: '#1e293b',
    colorBorderSecondary: '#273449',
    fontSize: 13,
    fontFamily: FONT_FAMILY,
  },
  components: {
    Layout: {
      siderBg: '#0a1424',
      bodyBg: '#0b1220',
      headerBg: '#0d1726',
      headerHeight: 56,
      headerPadding: '0 18px',
    },
    Menu: {
      darkItemBg: 'transparent',
      darkSubMenuItemBg: 'transparent',
      darkItemColor: '#8aa3b8',
      darkItemHoverBg: 'rgba(255,255,255,0.06)',
      darkItemHoverColor: '#ffffff',
      darkItemSelectedBg: '#10b981',
      darkItemSelectedColor: '#ffffff',
      itemBorderRadius: 8,
      itemMarginInline: 8,
      itemHeight: 38,
      itemMarginBlock: 2,
    },
    Table: {
      headerBg: '#0f1b2d',
      headerColor: '#94a3b8',
      headerSplitColor: 'transparent',
      rowHoverBg: 'rgba(16,185,129,0.08)',
      borderColor: '#1e293b',
      cellPaddingBlock: 10,
      cellPaddingInline: 14,
      cellPaddingBlockSM: 8,
      cellPaddingInlineSM: 12,
    },
    Card: { borderRadiusLG: 12 },
    Tag: { borderRadiusSM: 20 },
    Button: { borderRadius: 9 },
  },
}

export function buildTheme(mode: ThemeMode): ThemeConfig {
  return mode === 'dark' ? darkTheme : lightTheme
}

interface ThemeContextValue {
  mode: ThemeMode
  toggle: () => void
  setMode: (m: ThemeMode) => void
}

const ThemeContext = createContext<ThemeContextValue>({
  mode: 'light',
  toggle: () => {},
  setMode: () => {},
})

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [mode, setModeState] = useState<ThemeMode>(() => {
    try {
      return localStorage.getItem(STORAGE_KEY) === 'dark' ? 'dark' : 'light'
    } catch {
      return 'light'
    }
  })

  // 同步 <html>/<body> 的 data-theme，供 index.css 深色规则 + localStorage 持久化
  // 用 useLayoutEffect：在浏览器 paint 前就位，深色刷新时不闪白
  useLayoutEffect(() => {
    document.documentElement.setAttribute('data-theme', mode)
    document.body.setAttribute('data-theme', mode)
    try {
      localStorage.setItem(STORAGE_KEY, mode)
    } catch {
      /* ignore */
    }
  }, [mode])

  const value = useMemo<ThemeContextValue>(
    () => ({
      mode,
      toggle: () => setModeState((m) => (m === 'dark' ? 'light' : 'dark')),
      setMode: setModeState,
    }),
    [mode],
  )

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>
}

export function useThemeMode() {
  return useContext(ThemeContext)
}
