// 集群「监控告警」页（替换 ComingSoon 占位）：集群维度资源指标（CPU/内存为核心）+ 趋势图。
// 数据源：集群自带 Prometheus（走 getClusterMetric 即时查询 + getClusterMetricRange 趋势曲线），平台不落库。
// 指标：node_cpu / node_mem（核心，各节点 % 求均值 = 集群维度）；node_fs（磁盘）/ node_net_rx（网络入站，各节点求和）为补充。
// 图表：复用 MonitorPanel 的 SVG 折线写法（不引 ECharts，前端未装 echarts 依赖）。
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Card, Col, Row, Statistic, Progress, Segmented, Button, Space, Spin, Empty, Tag, Typography } from 'antd'
import { ReloadOutlined } from '@ant-design/icons'
import { getClusterMetric, getClusterMetricRange } from '../../services/api'
import type { MonitoringItem, RangeSeries } from '../../services/api'

// ============ 类型 ============
interface CurrentMetric { metric: string; label: string; unit: string; items: MonitoringItem[] }
interface RangeMetric { metric: string; label: string; unit: string; series: RangeSeries[] }

interface MetricDef { key: string; title: string; color: string; unit: string; kind: 'avg' | 'sum' }

const CORE_METRICS: MetricDef[] = [
  { key: 'node_cpu', title: 'CPU 使用率', color: '#1677ff', unit: '%', kind: 'avg' },
  { key: 'node_mem', title: '内存使用率', color: '#52c41a', unit: '%', kind: 'avg' },
]
const EXTRA_METRICS: MetricDef[] = [
  { key: 'node_fs', title: '磁盘使用率', color: '#faad14', unit: '%', kind: 'avg' },
  { key: 'node_net_rx', title: '网络入站', color: '#13c2c2', unit: 'B/s', kind: 'sum' },
]
const ALL_METRICS: MetricDef[] = [...CORE_METRICS, ...EXTRA_METRICS]

// 趋势范围（minutes / step 秒；minutes 上限 7 天 = 10080，与后端一致）
const RANGES = [
  { value: '30m', label: '30分钟', minutes: 30, step: 60 },
  { value: '1h', label: '1小时', minutes: 60, step: 60 },
  { value: '6h', label: '6小时', minutes: 360, step: 300 },
  { value: '24h', label: '24小时', minutes: 1440, step: 600 },
  { value: '3d', label: '3天', minutes: 4320, step: 1800 },
  { value: '7d', label: '7天', minutes: 10080, step: 3600 },
]

// ============ 聚合工具 ============
const num = (v: unknown): number | null => {
  const n = Number(v)
  return Number.isFinite(n) ? n : null
}

// 即时值：多节点聚合（avg=均值 / sum=求和）
function aggItems(items: MonitoringItem[] | undefined, kind: 'avg' | 'sum'): number | null {
  const vals = (items || [])
    .map((i) => num(i.value))
    .filter((v): v is number => v != null)
  if (!vals.length) return null
  if (kind === 'sum') return vals.reduce((a, b) => a + b, 0)
  return vals.reduce((a, b) => a + b, 0) / vals.length
}

// 趋势：多序列（每节点一条）按时间戳聚合为单线
function aggSeries(series: RangeSeries[] | undefined, kind: 'avg' | 'sum'): { t: number; v: number }[] {
  const m = new Map<number, { sum: number; n: number }>()
  for (const s of series || []) {
    for (const [ts, v] of s.values || []) {
      const cur = m.get(ts) || { sum: 0, n: 0 }
      cur.sum += Number(v)
      cur.n += 1
      m.set(ts, cur)
    }
  }
  return [...m.entries()]
    .sort((a, b) => a[0] - b[0])
    .map(([t, x]) => ({ t, v: kind === 'sum' ? x.sum : x.sum / x.n }))
}

// ============ 格式化 ============
function fmtBytes(b: number): string {
  if (b >= 1024 ** 3) return (b / 1024 ** 3).toFixed(2) + ' GB'
  if (b >= 1024 ** 2) return (b / 1024 ** 2).toFixed(2) + ' MB'
  if (b >= 1024) return (b / 1024).toFixed(2) + ' KB'
  return b.toFixed(1) + ' B'
}
function fmtVal(v: number, unit: string): string {
  if (unit === '%') return v.toFixed(0)
  if (unit === 'B/s') return fmtBytes(v) + '/s'
  return v.toFixed(1)
}
const p2 = (n: number) => String(n).padStart(2, '0')
const fmtT = (ts: number) => {
  const d = new Date(ts * 1000)
  return `${p2(d.getMonth() + 1)}-${p2(d.getDate())} ${p2(d.getHours())}:${p2(d.getMinutes())}`
}
const pctColor = (v?: number | null) => (!v && v !== 0) ? '#999' : v >= 85 ? '#ff4d4f' : v >= 70 ? '#faad14' : '#52c41a'

function niceCeil(v: number): number {
  if (v <= 0) return 1
  const p = Math.pow(10, Math.floor(Math.log10(v)))
  const n = v / p
  const m = n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10
  return m * p
}

// ============ SVG 趋势图（无第三方库，仿 MonitorPanel） ============
function TrendChart({ points, color, unit }: { points: { t: number; v: number }[]; color: string; unit: string }) {
  if (!points.length) return <Empty description="暂无数据（检查集群 Prometheus 与保留期）" image={Empty.PRESENTED_IMAGE_SIMPLE} />
  const W = 760, H = 200
  const PADL = 52, PADR = 14, PADT = 12, PADB = 26
  const innerW = W - PADL - PADR
  const innerH = H - PADT - PADB
  const vals = points.map((p) => p.v)
  const top = niceCeil(Math.max(...vals))
  const t0 = points[0].t
  const spanT = Math.max(1, points[points.length - 1].t - t0)
  const x = (t: number) => PADL + ((t - t0) / spanT) * innerW
  const y = (v: number) => PADT + (1 - v / top) * innerH
  const xy = points.map((p) => `${x(p.t).toFixed(1)},${y(p.v).toFixed(1)}`)
  const grid = [0, 0.25, 0.5, 0.75, 1]
  const last = points[points.length - 1]
  return (
    <div>
      <svg width="100%" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" style={{ display: 'block' }}>
        {grid.map((g) => {
          const gy = PADT + (1 - g) * innerH
          return (
            <g key={g}>
              <line x1={PADL} y1={gy} x2={W - PADR} y2={gy} stroke="#f0f0f0" strokeWidth={1} />
              <text x={PADL - 6} y={gy + 3} fontSize={10} fill="#8c8c8c" textAnchor="end">
                {fmtVal(top * g, unit)}
              </text>
            </g>
          )
        })}
        {xy.length > 1 && (
          <polygon
            points={`${PADL},${PADT + innerH} ${xy.join(' ')} ${(W - PADR)},${PADT + innerH}`}
            fill={color}
            opacity={0.08}
          />
        )}
        {xy.length > 1
          ? <polyline points={xy.join(' ')} fill="none" stroke={color} strokeWidth={2} />
          : <circle cx={x(points[0].t).toFixed(1)} cy={y(points[0].v).toFixed(1)} r={3} fill={color} />}
      </svg>
      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, color: '#8c8c8c', marginTop: 4 }}>
        <span>{points.length > 1 ? fmtT(points[0].t) : ''}</span>
        <span>峰值 {fmtVal(Math.max(...vals), unit)} · 当前 {fmtVal(last.v, unit)}</span>
        <span>{points.length > 1 ? fmtT(last.t) : ''}</span>
      </div>
    </div>
  )
}

// ============ 页面 ============
export default function ClusterMonitoring({ clusterId }: { clusterId: number }) {
  const [range, setRange] = useState('1h')
  const [current, setCurrent] = useState<Record<string, CurrentMetric>>({})
  const [trends, setTrends] = useState<Record<string, RangeMetric>>({})
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState('')

  const load = useCallback(() => {
    setLoading(true)
    setErr('')
    const spec = RANGES.find((r) => r.value === range) || RANGES[1]
    const keys = ALL_METRICS.map((m) => m.key)
    Promise.allSettled(keys.map((k) => getClusterMetric(clusterId, k))).then((results) => {
      const cur: Record<string, CurrentMetric> = {}
      results.forEach((r, i) => { if (r.status === 'fulfilled') cur[keys[i]] = r.value as CurrentMetric })
      setCurrent(cur)
    })
    Promise.allSettled(keys.map((k) => getClusterMetricRange(clusterId, k, spec.minutes, spec.step))).then((results) => {
      const tr: Record<string, RangeMetric> = {}
      results.forEach((r, i) => { if (r.status === 'fulfilled') tr[keys[i]] = r.value as RangeMetric })
      setTrends(tr)
    }).catch(() => setErr('趋势查询失败')).finally(() => setLoading(false))
  }, [clusterId, range])

  useEffect(() => { load() }, [load])

  const nodeCount = useMemo(() => (current['node_cpu']?.items || []).length, [current])

  const cardVals = useMemo(() => {
    const out: Record<string, number | null> = {}
    for (const m of ALL_METRICS) out[m.key] = aggItems(current[m.key]?.items, m.kind)
    return out
  }, [current])

  const trendPts = useMemo(() => {
    const out: Record<string, { t: number; v: number }[]> = {}
    for (const m of ALL_METRICS) out[m.key] = aggSeries(trends[m.key]?.series, m.kind)
    return out
  }, [trends])

  const hasAnyData = ALL_METRICS.some((m) => trendPts[m.key].length > 0 || cardVals[m.key] != null)

  const card = (m: MetricDef) => {
    const v = cardVals[m.key]
    const pct = m.unit === '%' ? v : null
    return (
      <Card size="small">
        <Statistic title={m.title} value={v == null ? '-' : fmtVal(v, m.unit)} suffix={m.unit === '%' ? '%' : ''} />
        {pct != null && (
          <Progress percent={Math.min(100, Math.max(0, pct))} showInfo={false} status={pct > 85 ? 'exception' : 'normal'} />
        )}
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          {nodeCount > 0 ? `集群 ${nodeCount} 个节点 ${m.kind === 'sum' ? '求和' : '均值'}` : '暂无数据'}
        </Typography.Text>
      </Card>
    )
  }

  const chart = (m: MetricDef) => (
    <div>
      <div style={{ fontWeight: 600, marginBottom: 8 }}>{m.title}</div>
      <TrendChart points={trendPts[m.key]} color={m.color} unit={m.unit} />
    </div>
  )

  return (
    <div>
      <Space style={{ marginBottom: 12 }} wrap>
        <Segmented value={range} onChange={(v) => setRange(v as string)} options={RANGES.map((r) => ({ value: r.value, label: r.label }))} />
        <Button size="small" icon={<ReloadOutlined />} onClick={load} loading={loading}>刷新</Button>
        <Tag color="blue">{nodeCount} 个节点</Tag>
      </Space>

      {loading && !hasAnyData ? (
        <div style={{ textAlign: 'center', padding: 60 }}><Spin /></div>
      ) : err && !hasAnyData ? (
        <Empty description={err} />
      ) : !hasAnyData ? (
        <Empty description="暂无指标数据（检查集群 Prometheus 与数据保留期）" />
      ) : (
        <>
          <Row gutter={12}>
            {CORE_METRICS.map((m) => <Col key={m.key} flex="1">{card(m)}</Col>)}
            {EXTRA_METRICS.map((m) => <Col key={m.key} flex="1">{card(m)}</Col>)}
          </Row>
          <Card size="small" title="趋势" style={{ marginTop: 12 }}>
            <Row gutter={[24, 24]}>
              {CORE_METRICS.map((m) => <Col key={m.key} span={12}>{chart(m)}</Col>)}
              {EXTRA_METRICS.map((m) => <Col key={m.key} span={12}>{chart(m)}</Col>)}
            </Row>
            <div style={{ fontSize: 12, color: '#8c8c8c', marginTop: 12 }}>
              数据源：集群 Prometheus（保留 7 天）；采样间隔随范围自动调整；平台不存储这些指标。
            </div>
          </Card>
        </>
      )}
    </div>
  )
}
