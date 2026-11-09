// 工作负载级监控抽屉（R15）：Deployment/StatefulSet/DaemonSet × 容器，跨 Pod 重建拼接。
// 数据源 = 集群 Prometheus（平台不落库）：CPU/内存按容器各一条线；网络按 Deployment 汇总
// （容器共享 netns，拆不到容器，UI 里明确标注）。
import { useEffect, useMemo, useRef, useState } from 'react'
import { Drawer, Segmented, Empty, Space, Tag, Spin, Switch } from 'antd'
import { Line } from '@ant-design/charts'
import { getWorkloadMetrics } from '../services/api'

type Series = { name: string; values: [number, string][] }

interface WorkloadMetricsPayload {
  cpu: Series[]
  memory: Series[]
  net_rx: Series[]
  net_tx: Series[]
  pod_container_count: Record<string, number>
}

interface Props {
  target: { clusterId: number; kind: string; namespace: string; name: string } | null
  onClose: () => void
}

const RANGES = [
  { value: '1h', label: '1小时' }, { value: '6h', label: '6小时' }, { value: '24h', label: '24小时' },
  { value: '72h', label: '3天' }, { value: '168h', label: '7天' },
]
const STEP_LABEL: Record<string, string> = {
  '1h': '1 分钟', '6h': '5 分钟', '24h': '5 分钟', '72h': '15 分钟', '168h': '1 小时',
}

const fmtT = (ts: number, range: string) => {
  const d = new Date(ts * 1000)
  const hh = String(d.getHours()).padStart(2, '0')
  const mm = String(d.getMinutes()).padStart(2, '0')
  const ss = String(d.getSeconds()).padStart(2, '0')
  if (range === '24h' || range === '72h' || range === '168h') {
    return `${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')} ${hh}:${mm}`
  }
  return `${hh}:${mm}:${ss}`
}

function useSeries(series: Series[], range: string, unitDiv = 1) {
  return useMemo(() => {
    const out: { t: string; v: number; type: string }[] = []
    for (const s of series || []) {
      for (const [ts, v] of s.values || []) {
        out.push({ t: fmtT(ts, range), v: Number(v) / unitDiv, type: s.name })
      }
    }
    return out
  }, [series, range, unitDiv])
}

export default function WorkloadMetricsDrawer({ target, onClose }: Props) {
  const [range, setRange] = useState('1h')
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState('')
  const [data, setData] = useState<{
    cpu: Series[]; memory: Series[]; net_rx: Series[]; net_tx: Series[]
    pod_container_count: Record<string, number>
  }>({ cpu: [], memory: [], net_rx: [], net_tx: [], pod_container_count: {} })
  const [autoRefresh, setAutoRefresh] = useState(true)
  const [lastUpdated, setLastUpdated] = useState(0)
  const inFlight = useRef(false)

  const applyData = (raw: unknown) => {
    const d = raw as unknown as WorkloadMetricsPayload
    setData({
      cpu: d?.cpu || [], memory: d?.memory || [], net_rx: d?.net_rx || [], net_tx: d?.net_tx || [],
      pod_container_count: d?.pod_container_count || {},
    })
    setLastUpdated(Date.now())
  }

  // 首次打开 / 切换范围：带 loading
  useEffect(() => {
    if (!target) return
    let cancelled = false
    setLoading(true); setErr('')
    inFlight.current = true
    getWorkloadMetrics(target.clusterId, target.kind, target.namespace, target.name, range)
      .then((raw) => { if (!cancelled) applyData(raw) })
      .catch((e) => { if (!cancelled) setErr(e?.response?.data?.message || '查询失败（检查集群 Prometheus 是否可用）') })
      .finally(() => { inFlight.current = false; if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
    // 依赖拆成字段：父组件传新对象（同值）时不重复拉数据，避免闪屏
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [target?.clusterId, target?.kind, target?.namespace, target?.name, range])

  // 自动刷新：静默更新（不 loading、不重建图表 → 不闪）
  useEffect(() => {
    if (!target || !autoRefresh) return
    let cancelled = false
    const t = setInterval(() => {
      if (inFlight.current) return
      inFlight.current = true
      getWorkloadMetrics(target.clusterId, target.kind, target.namespace, target.name, range)
        .then((raw) => { if (!cancelled) applyData(raw) })
        .catch(() => { /* 静默失败，保留旧数据 */ })
        .finally(() => { inFlight.current = false })
    }, 15000)
    return () => { cancelled = true; clearInterval(t) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [target?.clusterId, target?.kind, target?.namespace, target?.name, range, autoRefresh])

  const containerCount = Math.max(1, data.cpu.length)
  const podCount = Object.keys(data.pod_container_count).length
  const multiContainer = Object.values(data.pod_container_count).some((n) => n > 1)

  const cpuData = useSeries(data.cpu, range)
  const memData = useSeries(data.memory, range, 1024 * 1024)

  // 网络：按 Deployment 汇总（rx/tx 各一条）
  const netData = useMemo(() => {
    const sum = (arr: Series[]) => {
      const m = new Map<number, number>()
      for (const s of arr) for (const [ts, v] of s.values || []) m.set(ts, (m.get(ts) || 0) + Number(v))
      return [...m.entries()].sort((a, b) => a[0] - b[0])
    }
    const rx = sum(data.net_rx), tx = sum(data.net_tx)
    const peak = Math.max(...rx.map((x) => x[1]), ...tx.map((x) => x[1]), 0)
    const unit = peak >= 1024 * 1024 ? 'MB/s' : peak >= 1024 ? 'KB/s' : 'B/s'
    const div = unit === 'MB/s' ? 1024 * 1024 : unit === 'KB/s' ? 1024 : 1
    const rows = [
      ...rx.map(([ts, v]) => ({ t: fmtT(ts, range), v: Number((v / div).toFixed(2)), type: '入站' })),
      ...tx.map(([ts, v]) => ({ t: fmtT(ts, range), v: Number((v / div).toFixed(2)), type: '出站' })),
    ]
    return { rows, unit }
  }, [data.net_rx, data.net_tx, range])

  // Pod 段：从网络序列的首尾时间推断（每 Pod 一条，用于看重建/滚动更新点）
  const segments = useMemo(() => {
    const all = [...data.net_rx, ...data.net_tx]
    const byPod = new Map<string, { first: number; last: number }>()
    for (const s of all) {
      const vals = s.values || []
      if (!vals.length) continue
      const first = vals[0][0], last = vals[vals.length - 1][0]
      const cur = byPod.get(s.name)
      if (!cur) byPod.set(s.name, { first, last })
      else byPod.set(s.name, { first: Math.min(cur.first, first), last: Math.max(cur.last, last) })
    }
    const list = [...byPod.entries()]
    if (!list.length) return { list: [], min: 0, max: 0 }
    const min = Math.min(...list.map(([, v]) => v.first))
    const max = Math.max(...list.map(([, v]) => v.last))
    return { list, min, max }
  }, [data.net_rx, data.net_tx])

  const base = { data: [], xField: 't', yField: 'v', seriesField: 'type', smooth: true, height: 200, animation: false, legend: { position: 'top' as const } }

  return (
    <Drawer title={target ? `工作负载监控：${target.namespace} / ${target.name}` : '工作负载监控'} open={!!target} onClose={onClose} width={860}>
      <Space style={{ marginBottom: 12 }} wrap>
        <Segmented value={range} onChange={(v) => setRange(v as string)} options={RANGES} />
        <span style={{ fontSize: 12, color: '#8c8c8c' }}>
          <Switch size="small" checked={autoRefresh} onChange={setAutoRefresh} /> 自动刷新(15s)
          {lastUpdated ? ` · 更新于 ${new Date(lastUpdated).toLocaleTimeString()}` : ''}
        </span>
        <Tag color="blue">{containerCount} 个容器</Tag>
        <Tag color="geekblue">{podCount} 个 Pod</Tag>
        {multiContainer && <Tag color="orange">存在多容器 Pod（网络按 Deployment 汇总）</Tag>}
      </Space>

      {loading ? <div style={{ padding: 40, textAlign: 'center' }}><Spin /></div>
        : err ? <Empty description={err} />
        : (!data.cpu.length && !data.net_rx.length) ? <Empty description="暂无指标数据（检查集群 Prometheus 与数据保留期）" />
        : (
          <div>
            <div style={{ marginBottom: 18 }}>
              <b>CPU（core，按容器）</b>
              <Line {...base} data={cpuData} />
            </div>
            <div style={{ marginBottom: 18 }}>
              <b>内存（MB，按容器）</b>
              <Line {...base} data={memData} />
            </div>
            <div style={{ marginBottom: 6 }}>
              <b>网络（{netData.unit}，Deployment 汇总）</b>
              <span style={{ marginLeft: 8, fontSize: 12, color: '#8c8c8c' }}>
                容器共享 netns，网络无法按容器拆分；此处为各 Pod 求和
              </span>
            </div>
            <Line {...base} data={netData.rows} scale={{ y: { nice: true } }} />

            {segments.list.length > 0 && (
              <div style={{ marginTop: 18 }}>
                <b>Pod 段（重建 / 滚动更新）</b>
                <div style={{ marginTop: 8, position: 'relative' }}>
                  {segments.list.map(([pod], i) => {
                    const seg = segments.list[i][1]
                    const span = Math.max(1, segments.max - segments.min)
                    const left = ((seg.first - segments.min) / span) * 100
                    const width = Math.max(1, ((seg.last - seg.first) / span) * 100)
                    return (
                      <div key={pod} style={{ display: 'flex', alignItems: 'center', marginBottom: 4 }}>
                        <div style={{ width: 260, fontSize: 12, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }} title={pod}>{pod}</div>
                        <div style={{ position: 'relative', flex: 1, height: 14, background: '#f0f0f0', borderRadius: 3 }}>
                          <div style={{ position: 'absolute', left: `${left}%`, width: `${width}%`, top: 0, bottom: 0, background: i % 2 ? '#69b1ff' : '#1677ff', borderRadius: 3 }} />
                        </div>
                      </div>
                    )
                  })}
                </div>
                <div style={{ fontSize: 12, color: '#8c8c8c', marginTop: 4 }}>
                  同一段时间有多条 = 滚动更新期间新旧 Pod 并存；曲线已跨 Pod 拼接（容器名不变则连续）。
                </div>
              </div>
            )}

            <div style={{ marginTop: 12, fontSize: 12, color: '#8c8c8c' }}>
              数据源：集群 Prometheus（保留 7 天）；采样粒度 {STEP_LABEL[range] || '-'}；平台不存储这些指标。
            </div>
          </div>
        )}
    </Drawer>
  )
}
