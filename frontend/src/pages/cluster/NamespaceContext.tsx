// 集群内「命名空间」上下文（2026-11-13 老板要求）
// 口径：进入集群默认显示 default；在任一页面切换命名空间，服务 / 路由 / 容器组 / 工作负载 / 存储 / 配置 跟着一起切；
// 选择结果按集群记住（localStorage），刷新/来回切页不丢。
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { getClusterNamespaces } from '../../services/api'

/** 「全部命名空间」的哨兵值（不要用空串，避免和后端"未指定"混淆） */
export const NS_ALL = '__all__'
const DEFAULT_NS = 'default'

const storageKey = (clusterId: number) => `cluster_ns_${clusterId}`

interface ClusterNamespaceCtx {
  clusterId: number
  ns: string
  setNs: (v: string) => void
  namespaces: string[]
  loading: boolean
  /** 重新拉取命名空间列表（新建 namespace 后可调用） */
  reload: () => void
}

const Ctx = createContext<ClusterNamespaceCtx | null>(null)

export function ClusterNamespaceProvider({ clusterId, children }: { clusterId: number; children: ReactNode }) {
  const [namespaces, setNamespaces] = useState<string[]>([])
  const [loading, setLoading] = useState(true)
  const [ns, setNsState] = useState<string>(() => localStorage.getItem(storageKey(clusterId)) || DEFAULT_NS)

  const reload = useCallback(() => {
    setLoading(true)
    getClusterNamespaces(clusterId)
      .then((d) => setNamespaces(((d || []) as string[]).filter(Boolean)))
      .catch(() => setNamespaces([]))
      .finally(() => setLoading(false))
  }, [clusterId])

  useEffect(() => { reload() }, [reload])

  // 记忆值失效（namespace 被删 / 换集群）→ 回退 default，没有 default 就用第一个
  useEffect(() => {
    if (loading || !namespaces.length || ns === NS_ALL) return
    if (!namespaces.includes(ns)) {
      const next = namespaces.includes(DEFAULT_NS) ? DEFAULT_NS : namespaces[0]
      setNsState(next)
      localStorage.setItem(storageKey(clusterId), next)
    }
  }, [loading, namespaces, ns, clusterId])

  const setNs = useCallback((v: string) => {
    setNsState(v)
    localStorage.setItem(storageKey(clusterId), v)
  }, [clusterId])

  const value = useMemo<ClusterNamespaceCtx>(
    () => ({ clusterId, ns, setNs, namespaces, loading, reload }),
    [clusterId, ns, setNs, namespaces, loading, reload],
  )

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

/** 读取当前集群的命名空间选择；不在 Provider 内会抛错（便于早发现接线遗漏） */
export function useClusterNamespace(): ClusterNamespaceCtx {
  const v = useContext(Ctx)
  if (!v) throw new Error('useClusterNamespace 必须在 ClusterNamespaceProvider 内使用')
  return v
}

/** 页面里传给接口的 namespace 参数：选「全部」时传空串（后端口径=全部） */
export function nsParam(ns: string): string {
  return ns === NS_ALL ? '' : ns
}
