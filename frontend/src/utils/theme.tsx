// 主题基础设施：浅色（冷灰纸 + 石绿） + 深色（深绿黑底 + 翡翠绿），localStorage 持久化
// 用法：main.tsx 用 ThemeProvider + buildTheme(mode) 驱动 ConfigProvider；
//      页面用 useThemeMode() 读/切模式；index.css 用 html[data-theme='dark'] 覆盖硬编码底色
// 设计口径（2026-11-17，design-taste-zh）：冷静的作战指挥台 —— 冷灰纸底 / 石墨文字 /
//   单一石绿强调 / 细线分隔 / 零阴影 / 等宽数字（对比度全 ≥ 4.5）
import { createContext, useContext, useLayoutEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { theme as antdTheme } from 'antd'
import type { ThemeConfig } from 'antd'

export type ThemeMode = 'light' | 'dark'

const STORAGE_KEY = 'autohealops_theme_mode'
const LEGACY_STORAGE_KEY = 'api_ops_theme_mode'

// 读取初始主题（含一次性迁移）
// 注意：2026-10-21 改键名时这里踩过坑 —— 写成 `A || B === 'dark' ? 'dark' : 'light'`，
// 由于 || 优先级高于 ?:，只要新键有任意非空值（包括 'light'）就判成 dark，
// 于是首屏先写 'light'、下次刷新变黑，登录框/整站主色都变黑。这里改回显式比较，
// 并把旧的 api_ops_theme_mode 迁移到新键（迁移一次后清掉旧键）。
function readInitialMode(): ThemeMode {
  try {
    const legacy = localStorage.getItem(LEGACY_STORAGE_KEY)
    let current = localStorage.getItem(STORAGE_KEY)
    if (legacy !== null) {
      // 旧键是本次改键名前的「真实偏好」，优先以它为准做一次迁移
      if (current !== legacy) {
        current = legacy
        localStorage.setItem(STORAGE_KEY, legacy)
      }
      localStorage.removeItem(LEGACY_STORAGE_KEY)
    }
    return current === 'dark' ? 'dark' : 'light'
  } catch {
    return 'light'
  }
}

const FONT_FAMILY =
  '"PingFang SC","Hiragino Sans GB","Microsoft YaHei",system-ui,-apple-system,"Segoe UI",sans-serif'

// 浅色主题：冷灰纸底 + 石绿强调（零阴影、细线分隔、6px 圆角族）
const lightTheme: ThemeConfig = {
  token: {
    colorPrimary: '#0d7a55',
    colorSuccess: '#0d7a55',
    colorInfo: '#2563eb',
    colorWarning: '#b54708',
    colorError: '#b42318',
    colorLink: '#0a6144',
    borderRadius: 6,
    colorBgLayout: '#f5f7f6',
    colorBgContainer: '#ffffff',
    colorText: '#101613',
    colorTextSecondary: '#47524c',
    colorTextTertiary: '#626f69',
    colorBorder: '#e3e8e5',
    colorBorderSecondary: '#cdd6d1',
    fontSize: 14,
    fontFamily: FONT_FAMILY,
  },
  components: {
    Layout: {
      siderBg: '#ffffff',
      bodyBg: '#f5f7f6',
      headerBg: '#ffffff',
      headerHeight: 56,
      headerPadding: '0 18px',
    },
    Menu: {
      itemBg: 'transparent',
      itemColor: '#47524c',
      itemHoverBg: '#f0f3f2',
      itemHoverColor: '#101613',
      itemSelectedBg: '#e7f2ec',
      itemSelectedColor: '#0a6144',
      subMenuItemBg: 'transparent',
      itemBorderRadius: 6,
      itemMarginInline: 8,
      itemHeight: 36,
      itemMarginBlock: 2,
      iconSize: 15,
    },
    Table: {
      headerBg: '#f0f3f2',
      headerColor: '#626f69',
      headerSplitColor: 'transparent',
      rowHoverBg: '#f5f7f6',
      borderColor: '#e3e8e5',
      cellPaddingBlock: 10,
      cellPaddingInline: 14,
      cellPaddingBlockSM: 8,
      cellPaddingInlineSM: 12,
    },
    Card: { borderRadiusLG: 8 },
    Tag: { borderRadiusSM: 4 },
    Button: { borderRadius: 6 },
  },
}

// 深色主题：深绿黑底 + 翡翠绿强调（监控/大屏风）
const darkTheme: ThemeConfig = {
  algorithm: antdTheme.darkAlgorithm,
  token: {
    colorPrimary: '#37c08a',
    colorSuccess: '#37c08a',
    colorInfo: '#60a5fa',
    colorWarning: '#e09a4e',
    colorError: '#f0736a',
    colorLink: '#6fd9ae',
    borderRadius: 6,
    colorBgLayout: '#0e1412',
    colorBgContainer: '#141b18',
    colorText: '#e9efec',
    colorTextSecondary: '#a7b4ad',
    colorTextTertiary: '#7f8d86',
    colorBorder: '#232d28',
    colorBorderSecondary: '#313d37',
    fontSize: 14,
    fontFamily: FONT_FAMILY,
  },
  components: {
    Layout: {
      siderBg: '#141b18',
      bodyBg: '#0e1412',
      headerBg: '#141b18',
      headerHeight: 56,
      headerPadding: '0 18px',
    },
    Menu: {
      itemBg: 'transparent',
      itemColor: '#a7b4ad',
      itemHoverBg: '#1c2420',
      itemHoverColor: '#e9efec',
      itemSelectedBg: '#123023',
      itemSelectedColor: '#6fd9ae',
      subMenuItemBg: 'transparent',
      itemBorderRadius: 6,
      itemMarginInline: 8,
      itemHeight: 36,
      itemMarginBlock: 2,
      iconSize: 15,
    },
    Table: {
      headerBg: '#1c2420',
      headerColor: '#a7b4ad',
      headerSplitColor: 'transparent',
      rowHoverBg: 'rgba(55,192,138,0.06)',
      borderColor: '#232d28',
      cellPaddingBlock: 10,
      cellPaddingInline: 14,
      cellPaddingBlockSM: 8,
      cellPaddingInlineSM: 12,
    },
    Card: { borderRadiusLG: 8 },
    Tag: { borderRadiusSM: 4 },
    Button: { borderRadius: 6 },
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
  const [mode, setModeState] = useState<ThemeMode>(readInitialMode)

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
