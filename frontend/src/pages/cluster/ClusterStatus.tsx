// R39 集群状态（单集群行式总览）：一行一个 Deployment，状态列用容器方块表示就绪情况
// 设计口径见 docs/BACKLOG-2026-11-13.md R39：不写「运行中」等文字，纯方块；绿块呼吸、红块常亮
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { Button, Card, Empty, Skeleton, Spin, Tag, Tooltip, message } from 'antd'
import { ArrowLeftOutlined, ReloadOutlined } from '@ant-design/icons'
import { getCluster, getClusterDetail, getClusterPods, getNodes } from '../../services/api'

interface DeploymentRow {
  namespace: string
  name: string
  replicas: number
  readyReplicas: number
  containerTotal: number
  containerReady: number
  /** 每个 Pod 的 [总容器, 就绪容器]，用于渲染方块 */
  pods: boolean[]
}

interface PodBrief {
  namespace?: string
  name?: string
  ready?: boolean
  containers?: string[]
  ready_containers?: number
  total_containers?: number
}

function fmtPct(n: number, d: number): string {
  if (!d) return '0%'
  return `${Math.round((n / d) * 100)}%`
}

export default function ClusterStatus() {
  const { id } = useParams()
  const clusterId = Number(id)
  const navigate = useNavigate()
  const [cluster, setCluster] = useState<Record<string, unknown> | null>(null)
  const [rows, setRows] = useState<DeploymentRow[]>([])
  const [nodeCount, setNodeCount] = useState(0)
  const [loading, setLoading] = useState(true)
  const [updatedAt, setUpdatedAt] = useState('')
  const timerRef = useRef<number | null>(null)

  const load = useCallback(async () => {
    if (!clusterId) return
    try {
      const [c, detail, podsRes, nodes] = await Promise.all([
        getCluster(clusterId).catch(() => null),
        getClusterDetail(clusterId).catch(() => []),
        getClusterPods(clusterId).catch(() => ({ pods: [] })),
        getNodes(clusterId).catch(() => []),
      ])
      if (c) setCluster(c as unknown as Record<string, unknown>)
      setNodeCount(Array.isArray(nodes) ? nodes.length : 0)

      const pods: PodBrief[] = ((podsRes as { pods?: PodBrief[] })?.pods) || []
      const nsList = (detail as { namespace: string; deployments?: { name: string; replicas: number; ready_replicas: number }[] }[]) || []

      // pod → deployment 归属：按「同 namespace 下最长的 deployment 名前缀」匹配
      // （pod 名形如 <deployment>-<rs hash>-<suffix>；名字本身带连字符时必须取最长匹配）
      const depNamesByNs = new Map<string, string[]>()
      for (const ns of nsList) {
        depNamesByNs.set(ns.namespace, (ns.deployments || []).map((d) => d.name).sort((a, b) => b.length - a.length))
      }
      const byDeploy = new Map<string, PodBrief[]>()
      for (const p of pods) {
        const name = p.name || ''
        const ns = p.namespace || ''
        const dep = (depNamesByNs.get(ns) || []).find((d) => name === d || name.startsWith(`${d}-`))
        if (!dep) continue
        const key = `${ns}/${dep}`
        if (!byDeploy.has(key)) byDeploy.set(key, [])
        byDeploy.get(key)!.push(p)
      }

      const out: DeploymentRow[] = []
      for (const ns of nsList) {
        for (const d of ns.deployments || []) {
          const key = `${ns.namespace}/${d.name}`
          const list = byDeploy.get(key) || []
          let total = 0
          let ready = 0
          const squares: boolean[] = []
          for (const p of list) {
            const t = Number(p.total_containers ?? (p.containers?.length || 1))
            const r = Number(p.ready_containers ?? (p.ready ? t : 0))
            total += t
            ready += r
            for (let i = 0; i < t; i++) squares.push(i < r)
          }
          out.push({
            namespace: ns.namespace,
            name: d.name,
            replicas: Number(d.replicas || 0),
            readyReplicas: Number(d.ready_replicas || 0),
            containerTotal: total || Number(d.replicas || 0),
            containerReady: total ? ready : Number(d.ready_replicas || 0),
            pods: squares,
          })
        }
      }
      out.sort((a, b) => (a.namespace === b.namespace ? a.name.localeCompare(b.name) : a.namespace.localeCompare(b.namespace)))
      setRows(out)
      setUpdatedAt(new Date().toLocaleTimeString())
    } catch (e) {
      message.error(String((e as Error)?.message || '加载集群状态失败'))
    } finally {
      setLoading(false)
    }
  }, [clusterId])

  useEffect(() => { load() }, [load])

  // 5s 轮询（页面隐藏时跳过）
  useEffect(() => {
    if (timerRef.current) window.clearInterval(timerRef.current)
    timerRef.current = window.setInterval(() => {
      if (document.hidden) return
      load()
    }, 5000)
    return () => { if (timerRef.current) window.clearInterval(timerRef.current) }
  }, [load])

  const stats = useMemo(() => {
    const nsSet = new Set(rows.map((r) => r.namespace))
    const totalC = rows.reduce((s, r) => s + r.containerTotal, 0)
    const readyC = rows.reduce((s, r) => s + r.containerReady, 0)
    const bad = rows.filter((r) => r.containerTotal > 0 && r.containerReady < r.containerTotal).length
    return { ns: nsSet.size, deployments: rows.length, totalC, readyC, bad }
  }, [rows])

  // namespace 合并单元格：只在每组第一行显示
  const firstOfNs = useMemo(() => {
    const set = new Set<string>()
    const counts = new Map<string, number>()
    rows.forEach((r) => counts.set(r.namespace, (counts.get(r.namespace) || 0) + 1))
    const seen = new Set<string>()
    rows.forEach((r, i) => {
      if (!seen.has(r.namespace)) { seen.add(r.namespace); set.add(String(i)) }
    })
    return { set, counts }
  }, [rows])

  return (
    <div style={{ padding: 16 }}>
      <style>{`
        @keyframes r39-breathe { 0%,100% { opacity: 1 } 50% { opacity: .45 } }
        .r39-sq { display:inline-block; width:10px; height:10px; border-radius:2px; margin:0 2px 2px 0; }
        .r39-sq.ok { background:#52c41a; animation: r39-breathe 1.8s ease-in-out infinite; }
        .r39-sq.bad { background:#ff4d4f; }
      `}</style>

      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 12, flexWrap: 'wrap' }}>
        <Button icon={<ArrowLeftOutlined />} onClick={() => navigate(`/clusters/${clusterId}`)}>返回集群</Button>
        <h2 style={{ margin: 0, fontSize: 20 }}>集群状态{cluster?.name ? `：${String(cluster.name)}` : ''}</h2>
        {cluster?.env_tag ? <Tag color="processing">{String(cluster.env_tag)}</Tag> : null}
        {cluster?.version ? <Tag>v{String(cluster.version).replace(/^v/, '')}</Tag> : null}
        {cluster?.source ? <Tag color="cyan">{String(cluster.source)}</Tag> : null}
        <span style={{ color: '#8c8c8c', fontSize: 12 }}>
          节点 {nodeCount} ｜ namespace {stats.ns} ｜ Deployment {stats.deployments} ｜ 异常 {stats.bad} ｜ 就绪率 {fmtPct(stats.readyC, stats.totalC)}
        </span>
        <span style={{ marginLeft: 'auto', color: '#bfbfbf', fontSize: 12 }}>{updatedAt && `更新于 ${updatedAt}`}</span>
        <Button size="small" icon={<ReloadOutlined />} onClick={load}>刷新</Button>
      </div>

      <Card size="small" styles={{ body: { padding: 0 } }}>
        {loading ? (
          <div style={{ padding: 24 }}><Spin /> <Skeleton active paragraph={{ rows: 6 }} /></div>
        ) : rows.length === 0 ? (
          <Empty description="该集群没有 Deployment" style={{ padding: 40 }} />
        ) : (
          <div style={{ maxHeight: 'calc(100vh - 190px)', overflow: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', tableLayout: 'fixed' }}>
              <thead style={{ position: 'sticky', top: 0, zIndex: 2, background: '#fafafa' }}>
                <tr>
                  <th style={th(210)}>NAMESPACE</th>
                  <th style={th(undefined)}>DEPLOYMENT</th>
                  <th style={th(140)}>容器数量</th>
                  <th style={th(undefined, '40%')}>状态</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r, i) => {
                  const isFirst = firstOfNs.set.has(String(i))
                  const span = firstOfNs.counts.get(r.namespace) || 1
                  const pct = r.containerTotal ? r.containerReady / r.containerTotal : 0
                  return (
                    <tr key={`${r.namespace}/${r.name}`} style={{ borderTop: '1px solid #f0f0f0' }}>
                      {isFirst && (
                        <td rowSpan={span} style={{
                          padding: '12px 10px', verticalAlign: 'top', fontWeight: 600,
                          borderRight: '1px solid #f0f0f0', background: '#fcfcfc', wordBreak: 'break-all',
                        }}>{r.namespace}</td>
                      )}
                      <td style={{ padding: '12px 10px' }}>
                        <Tooltip title={r.name}><span style={{ display: 'inline-block', maxWidth: '100%', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{r.name}</span></Tooltip>
                      </td>
                      <td style={{ padding: '12px 10px' }}>
                        {r.containerTotal === 0 ? (
                          <span style={{ color: '#bfbfbf', fontSize: 12 }}>0 / 0（已缩容）</span>
                        ) : (
                          <span style={{ color: pct >= 1 ? '#389e0d' : pct === 0 ? '#cf1322' : '#d48806', fontWeight: 600 }}>
                            {r.containerReady} / {r.containerTotal}
                          </span>
                        )}
                      </td>
                      <td style={{ padding: '12px 10px' }}>
                        <div style={{ display: 'flex', flexWrap: 'wrap' }}>
                          {r.pods.map((ok, idx) => (
                            <span key={idx} className={`r39-sq ${ok ? 'ok' : 'bad'}`}
                              style={ok ? { animationDelay: `${(idx % 12) * 0.12}s` } : undefined} />
                          ))}
                          {r.pods.length === 0 && (
                            <span style={{ color: '#bfbfbf', fontSize: 12 }}>
                              {r.replicas === 0 ? '已缩容为 0 副本' : '暂无 Pod'}
                            </span>
                          )}
                        </div>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  )
}

function th(width?: number, extra?: string): React.CSSProperties {
  return {
    width, textAlign: 'left', padding: '10px 10px', fontSize: 12, color: '#8c8c8c',
    fontWeight: 600, letterSpacing: 0.4, borderBottom: '1px solid #f0f0f0', ...(extra ? { width: extra } : {}),
  }
}
