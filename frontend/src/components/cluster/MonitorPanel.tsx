// 工作负载详情页「监控」页签内容：用 getWorkloadMetrics 拉 5m~168h 数据。
// 2026-11-23 R66 重构渲染：不再 sumSeries 求和单线，改为
//  CPU / 内存 / 网络接收 / 网络发送 四张图，每张默认按容器（网络按 Pod）拆多条曲线，
//  增加 Y 轴 nice 刻度 + 横向网格线 + 可点击图例，以及「合计」开关（回到求和视图）。
// 数据获取/轮询逻辑保持不变，只改数据整形与渲染。不引图表库，手写 SVG。
import { useEffect, useMemo, useRef, useState } from 'react'
import { Segmented, Button, Empty, Spin, Space, Switch } from 'antd'
import { ReloadOutlined } from '@ant-design/icons'
import { getWorkloadMetrics } from '../../services/api'
import MonitorChart, { ChartSeries, PALETTE } from './MonitorChart'

type Series = { name: string; values: [number, string][] }
interface MetricsPayload {
  cpu: Series[]
  memory: Series[]
  net_rx: Series[]
  net_tx: Series[]
  pod_container_count: Record<string, number>
}

// 与「容器监控」（Pod 监控抽屉）保持完全一致的时间范围
const RANGES = [
  { value: '10m', label: '10分钟' },
  { value: '1h', label: '1小时' },
  { value: '24h', label: '24小时' },
  { value: '72h', label: '3天' },
  { value: '168h', label: '7天' },
]

// 2026-11-23 R66：把后端返回的 Series[]（每 Series = 一个容器/Pod 名）整形为图表序列，
// 并按全局最大值选择展示单位（CPU m/core、内存 MiB/GiB、网络 KiB/s|MiB/s），峰值降序。
function buildSeries(series: Series[], kind: 'cpu' | 'memory' | 'network'): { series: ChartSeries[]; unit: string; has: boolean } {
  const raw = (series || [])
    .map((s) => ({
      name: s.name,
      points: (s.values || []).map(([ts, v]) => ({ t: ts, v: Number(v) })).filter((p) => Number.isFinite(p.v)),
    }))
    .filter((s) => s.points.length > 0)

  let maxRaw = 0
  for (const s of raw) for (const p of s.points) if (p.v > maxRaw) maxRaw = p.v

  // 选择展示单位（乘数 scale：展示值 = 原始值 × scale）
  let scale = 1, unit = ''
  if (kind === 'cpu') {
    if (maxRaw > 0 && maxRaw < 1) { scale = 1000; unit = 'm' } else { scale = 1; unit = 'core' }
  } else if (kind === 'memory') {
    const mib = maxRaw / (1024 * 1024)
    if (mib >= 1024) { scale = 1 / (1024 * 1024 * 1024); unit = 'GiB' } else { scale = 1 / (1024 * 1024); unit = 'MiB' }
  } else {
    const kib = maxRaw / 1024
    if (kib >= 1024) { scale = 1 / (1024 * 1024); unit = 'MiB/s' } else { scale = 1 / 1024; unit = 'KiB/s' }
  }

  const out: ChartSeries[] = raw.map((s, i) => ({
    key: s.name,
    name: s.name,
    color: PALETTE[i % PALETTE.length],
    points: s.points.map((p) => ({ t: p.t, v: p.v * scale })),
    peak: Math.max(...s.points.map((p) => p.v)) * scale,
  }))
  out.sort((a, b) => b.peak - a.peak)
  return { series: out, unit, has: out.length > 0 }
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
  // 2026-11-23 R66：「合计」开关，false=按容器拆分（默认），true=求和单线
  const [totalOn, setTotalOn] = useState(false)

  // 图宽自适应：ResizeObserver 实测容器宽度，兜底 720
  const wrapRef = useRef<HTMLDivElement>(null)
  const [wrapWidth, setWrapWidth] = useState(720)
  useEffect(() => {
    const el = wrapRef.current
    if (!el) return
    const measure = () => { if (el.clientWidth) setWrapWidth(el.clientWidth) }
    measure()
    if (typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver(measure)
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

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

  const cpuB = useMemo(() => buildSeries(data.cpu, 'cpu'), [data.cpu])
  const memB = useMemo(() => buildSeries(data.memory, 'memory'), [data.memory])
  const netRxB = useMemo(() => buildSeries(data.net_rx, 'network'), [data.net_rx])
  const netTxB = useMemo(() => buildSeries(data.net_tx, 'network'), [data.net_tx])

  const hasAny = cpuB.has || memB.has || netRxB.has || netTxB.has

  return (
    <div ref={wrapRef}>
      <Space style={{ marginBottom: 12 }} wrap>
        <Segmented value={range} onChange={(v) => setRange(v as string)} options={RANGES} />
        <Button size="small" icon={<ReloadOutlined />} onClick={() => load(range)} loading={loading}>刷新</Button>
        <Switch size="small" checked={totalOn} onChange={setTotalOn} checkedChildren="合计" unCheckedChildren="按容器" />
      </Space>
      {loading && !hasAny ? (
        <div style={{ textAlign: 'center', padding: 40 }}><Spin /></div>
      ) : err && !hasAny ? (
        <Empty description={err} />
      ) : !hasAny ? (
        <Empty description="暂无指标数据（检查集群 Prometheus 与数据保留期）" />
      ) : (
        <div>
          <div style={{ marginBottom: 16 }}>
            <b>CPU（{cpuB.unit}，按容器）</b>
            <MonitorChart series={cpuB.series} unit={cpuB.unit} totalOn={totalOn} width={wrapWidth} />
          </div>
          <div style={{ marginBottom: 16 }}>
            <b>内存（{memB.unit}，按容器）</b>
            <MonitorChart series={memB.series} unit={memB.unit} totalOn={totalOn} width={wrapWidth} />
          </div>
          <div style={{ marginBottom: 16 }}>
            <b>网络接收（{netRxB.unit}，按 Pod）</b>
            <MonitorChart series={netRxB.series} unit={netRxB.unit} totalOn={totalOn} width={wrapWidth} />
          </div>
          <div style={{ marginBottom: 8 }}>
            <b>网络发送（{netTxB.unit}，按 Pod）</b>
            <MonitorChart series={netTxB.series} unit={netTxB.unit} totalOn={totalOn} width={wrapWidth} />
          </div>
          <div style={{ fontSize: 12, color: '#8c8c8c' }}>
            数据源：集群 Prometheus（保留 7 天）；采样间隔随范围自动调整；平台不存储这些指标。
            网络为 Pod 级（容器共享 netns），按 Pod 拆分；CPU / 内存按容器拆分。
          </div>
        </div>
      )}
    </div>
  )
}
