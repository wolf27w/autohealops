// 全局刷新广播：顶栏「刷新」按钮递增 version，各页订阅 version 变化后重载数据
import { createContext, useCallback, useContext, useMemo, useState } from 'react'
import type { ReactNode } from 'react'

interface GlobalRefreshContextValue {
  /** 刷新代次：每次点击顶栏刷新 +1 */
  version: number
  /** 触发一次全局刷新（当前路由页面应据此重载数据） */
  refresh: () => void
}

const GlobalRefreshContext = createContext<GlobalRefreshContextValue>({
  version: 0,
  refresh: () => {},
})

export function GlobalRefreshProvider({ children }: { children: ReactNode }) {
  const [version, setVersion] = useState(0)
  const refresh = useCallback(() => setVersion((v) => v + 1), [])
  const value = useMemo(() => ({ version, refresh }), [version, refresh])
  return <GlobalRefreshContext.Provider value={value}>{children}</GlobalRefreshContext.Provider>
}

/** 页面订阅：const { version } = useGlobalRefresh()，在 useEffect 依赖里监听 version 变化触发重载 */
export function useGlobalRefresh() {
  return useContext(GlobalRefreshContext)
}

export default GlobalRefreshContext
