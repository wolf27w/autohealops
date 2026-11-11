// 工作负载详情页「监控」页签内容：用 getWorkloadMetrics 拉 1h/6h/24h 数据。
// 不引图表库，用 SVG polyline 画简单折线（CPU / 内存，跨容器跨 Pod 求和）；无数据时显示空态。
import { useEffect, useMemo, useState } from 'react'
import { Segmented, Button, Empty, Spin, Space, Tag } from 'antd'
import { ReloadOutlined } from '@ant-design/icons'
import { getWorkloadMetrics } from '../../services/api'

type Series = { name: string; values: [number, string][] }
interface MetricsPayload {
  cpu: Series[]
  memory: Series[]
  net_rx: Series[]
  net_tx: Series[]
  pod_container_count: Record<string, number>
}

const RANGES = [
  { value: '1h', label: '1小时' },
  { value: '6h', label: '6小时' },
  { value: '24h', label: '24小时' },
]

// 跨容器 / 跨 Pod 按时间戳求和 → 单一折线
function sumSeries(series: Series[]): { t: number; v: number }[] {
  const m = new Map<number, number>()
  for (const s of series || []) {
    for (const [ts, v] of s.values || []) {
      m.set(ts, (m.get(ts) || 0) + Number(v))
    }
  }
  return [...m.entries()].sort((a, b) => a[0] - b[0]).map(([t, v]) => ({ t, v }))
}

const p2 = (n: number) => String(n).padStart(2, '0')
const fmtT = (ts: number) => {
  const d = new Date(ts * 1000)
  return `${p2(d.getMonth() + 1)}-${p2(d.getDate())} ${p2(d.getHours())}:${p2(d.getMinutes())}`
}

function SparkLine({ points, color, unit, height = 130 }: {
  points: { t: number; v: number }[]
  color: string
  unit: string
  height?: number
}) {
  if (!points || points.length === 0) return <Empty description="暂无数据" image={Empty.PRESENTED_IMAGE_SIMPLE} />
  const W = 720, H = height, PAD = 10
  const vals = points.map((p) => p.v)
  let min = Math.min(...vals), max = Math.max(...vals)
  if (min === max) { min -= 1; max += 1 }
  const t0 = points[0].t, t1 = points[points.length - 1].t
  const spanT = Math.max(1, t1 - t0)
  const spanV = max - min
  const xy = points.map((p) => {
    const x = PAD + ((p.t - t0) / spanT) * (W - PAD * 2)
    const y = H - PAD - ((p.v - min) / spanV) * (H - PAD * 2)
    return [x, y]
  })
  const last = points[points.length - 1]
  const peak = max
  return (
    <div>
      <svg width="100%" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" style={{ display: 'block' }}>
        {xy.length > 1 && (
          <polygon
            points={`${PAD},${H - PAD} ${xy.map((p) => `${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(' ')} ${(W - PAD)},${H - PAD}`}
            fill={color}
            opacity={0.08}
          />
        )}
        {xy.length > 1
          ? <polyline points={xy.map((p) => `${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(' ')} fill="none" stroke={color} strokeWidth={2} />
          : <circle cx={xy[0][0].toFixed(1)} cy={xy[0][1].toFixed(1)} r={3} fill={color} />}
      </svg>
      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, color: '#8c8c8c', marginTop: 4 }}>
        <span>{points.length > 1 ? fmtT(points[0].t) : ''}</span>
        <span>峰值 {peak.toFixed(2)} {unit} · 当前 {last.v.toFixed(2)} {unit}</span>
        <span>{points.length > 1 ? fmtT(last.t) : ''}</span>
      </div>
    </div>
  )
}

export default function MonitorPanel({ clusterId, kind, namespace, name }: {
  clusterId: number
  kind: string
  namespace: string
  name: string
}) {
  const [range, setRange] = useState('1h')
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState('')
  const [data, setData] = useState<MetricsPayload>({ cpu: [], memory: [], net_rx: [], net_tx: [], pod_container_count: {} })

  const load = (r: string) => {
    setLoading(true)
    setErr('')
    getWorkloadMetrics(clusterId, kind, namespace, name, r)
      .then((raw) => setData(raw as MetricsPayload))
      .catch((e) => setErr(String((e as { response?: { data?: { message?: string } }; message?: string })?.response?.data?.message || (e as Error)?.message || '查询失败（检查集群 Prometheus 是否可用）')))
      .finally(() => setLoading(false))
  }

  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { load(range) }, [clusterId, kind, namespace, name, range])

  const cpu = useMemo(() => sumSeries(data.cpu), [data.cpu])
  const mem = useMemo(() => sumSeries(data.memory).map((p) => ({ ...p, v: p.v / 1024 / 1024 })), [data.memory])

  const containerCount = Math.max(1, data.cpu.length)
  const podCount = Object.keys(data.pod_container_count).length

  return (
    <div>
      <Space style={{ marginBottom: 12 }} wrap>
        <Segmented value={range} onChange={(v) => setRange(v as string)} options={RANGES} />
        <Button size="small" icon={<ReloadOutlined />} onClick={() => load(range)} loading={loading}>刷新</Button>
        <Tag color="blue">{containerCount} 个容器</Tag>
        <Tag color="geekblue">{podCount} 个 Pod</Tag>
      </Space>
      {loading && cpu.length === 0 && mem.length === 0 ? (
        <div style={{ textAlign: 'center', padding: 40 }}><Spin /></div>
      ) : err && cpu.length === 0 && mem.length === 0 ? (
        <Empty description={err} />
      ) : cpu.length === 0 && mem.length === 0 ? (
        <Empty description="暂无指标数据（检查集群 Prometheus 与数据保留期）" />
      ) : (
        <div>
          <div style={{ marginBottom: 16 }}>
            <b>CPU（core，跨容器求和）</b>
            <SparkLine points={cpu} color="#1677ff" unit="core" />
          </div>
          <div style={{ marginBottom: 8 }}>
            <b>内存（MB，跨容器求和）</b>
            <SparkLine points={mem} color="#52c41a" unit="MB" />
          </div>
          <div style={{ fontSize: 12, color: '#8c8c8c' }}>
            数据源：集群 Prometheus（保留 7 天）；采样间隔随范围自动调整；平台不存储这些指标。
          </div>
        </div>
      )}
    </div>
  )
}
