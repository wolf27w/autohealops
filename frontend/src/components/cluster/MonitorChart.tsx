// 2026-11-23 R66：多序列监控折线图（手写 SVG，不引 echarts/recharts）。
// 改动原因：原 MonitorPanel 只有 sumSeries 求和单线、且无 Y 轴刻度/网格线，
// 无法直观对比各容器（或网络各 Pod）的资源消耗。本组件提供：
//  ① 多条曲线 + 图例（点击显隐）；② Y 轴 nice 刻度 + 横向网格线；③ 合计参考线；
//  ④ 序列数 > collapseLimit 时默认 合计 + TopN，其余折叠可展开。
import { useMemo, useState } from 'react'
import { Empty } from 'antd'

export type Pt = { t: number; v: number }
export interface ChartSeries {
  key: string // 唯一标识（容器名 / Pod 名）
  name: string // 图例显示名
  color: string
  points: Pt[]
  peak: number // 峰值（用于 TopN 排序）
}

// 多曲线配色（合计线单独用深灰虚线）
export const PALETTE = [
  '#1677ff', '#52c41a', '#fa8c16', '#722ed1', '#eb2f96', '#13c2c2',
  '#f5222d', '#a0d911', '#2f54eb', '#fa541c', '#c41d7f', '#d48806',
  '#389e0d', '#d46b08', '#08979c', '#531dab',
]
const TOTAL_COLOR = '#595959'

const p2 = (n: number) => String(n).padStart(2, '0')
const fmtT = (ts: number) => {
  const d = new Date(ts * 1000)
  return `${p2(d.getMonth() + 1)}-${p2(d.getDate())} ${p2(d.getHours())}:${p2(d.getMinutes())}`
}

// nice 刻度：把 [min,max] 扩展到 1/2/5×10^n 的整数步长区间，返回刻度值数组与区间
function niceTicks(min: number, max: number, count = 5) {
  if (!Number.isFinite(min) || !Number.isFinite(max)) return { ticks: [] as number[], lo: 0, hi: 0 }
  if (min === max) { min -= 1; max += 1 }
  const raw = (max - min) / Math.max(1, count - 1)
  const mag = Math.pow(10, Math.floor(Math.log10(raw)))
  const n = raw / mag
  let step: number
  if (n <= 1) step = 1
  else if (n <= 2) step = 2
  else if (n <= 5) step = 5
  else step = 10
  step *= mag
  const lo = Math.floor(min / step) * step
  const hi = Math.ceil(max / step) * step
  const cnt = Math.round((hi - lo) / step)
  const ticks: number[] = []
  for (let i = 0; i <= cnt; i++) ticks.push(Number((lo + i * step).toFixed(10)))
  return { ticks, lo, hi }
}

const fmtTick = (v: number) => (Math.abs(v) >= 100 ? String(Math.round(v)) : String(Math.round(v * 100) / 100))
const fmtVal = (v: number) => (Math.abs(v) >= 100 ? v.toFixed(0) : v.toFixed(2))

interface Props {
  series: ChartSeries[] // 已按展示单位缩放、按 peak 降序
  unit: string // 展示单位（core / m / MiB / GiB / KiB/s / MiB/s）
  totalOn: boolean // 「合计」开关：true=只显示求和单线
  collapseLimit?: number // 序列数超过该值时默认折叠
  height?: number
  width?: number
}

export default function MonitorChart({ series, unit, totalOn, collapseLimit = 6, height = 150, width = 720 }: Props) {
  // 图例显隐（用户点击某条曲线隐藏/显示）
  const [hidden, setHidden] = useState<Set<string>>(() => new Set())
  // 序列数 > limit 时「展开其余」的折叠状态
  const [expanded, setExpanded] = useState(false)

  // 合计序列：跨序列按时间戳求和（与旧 sumSeries 等价，单位已缩放）
  const totalPoints = useMemo<Pt[]>(() => {
    const m = new Map<number, number>()
    for (const s of series) for (const p of s.points) m.set(p.t, (m.get(p.t) || 0) + p.v)
    return [...m.entries()].sort((a, b) => a[0] - b[0]).map(([t, v]) => ({ t, v }))
  }, [series])
  const totalPeak = useMemo(() => totalPoints.reduce((a, p) => Math.max(a, p.v), 0), [totalPoints])

  // 决定展示哪些序列：totalOn → 仅合计；否则 ≤limit 全量；>limit → 合计 + TopN + 展开后的其余
  const display = useMemo<ChartSeries[]>(() => {
    if (totalOn) return [{ key: '__total__', name: '合计', color: TOTAL_COLOR, points: totalPoints, peak: totalPeak }]
    if (series.length <= collapseLimit) return series
    const top = series.slice(0, collapseLimit)
    const rest = expanded ? series.slice(collapseLimit) : []
    return [
      { key: '__total__', name: '合计', color: TOTAL_COLOR, points: totalPoints, peak: totalPeak },
      ...top,
      ...rest,
    ]
  }, [totalOn, series, collapseLimit, expanded, totalPoints, totalPeak])

  const visible = useMemo(() => display.filter((s) => !hidden.has(s.key)), [display, hidden])

  // 可见序列的时间/取值边界
  const bounds = useMemo(() => {
    let t0 = Infinity, t1 = -Infinity, vmin = Infinity, vmax = -Infinity, any = false
    for (const s of visible) for (const p of s.points) {
      any = true
      if (p.t < t0) t0 = p.t
      if (p.t > t1) t1 = p.t
      if (p.v < vmin) vmin = p.v
      if (p.v > vmax) vmax = p.v
    }
    return { any, t0, t1, vmin, vmax }
  }, [visible])

  if (!bounds.any) return <Empty description="暂无数据" image={Empty.PRESENTED_IMAGE_SIMPLE} />

  const { t0, t1 } = bounds
  const { ticks, lo, hi } = niceTicks(bounds.vmin, bounds.vmax)
  const spanT = Math.max(1, t1 - t0)
  const spanV = Math.max(1e-9, hi - lo)

  const AXIS_W = 56 // Y 轴标签列宽
  const PAD_X = 4
  const PAD_Y = 6
  const plotW = Math.max(120, width - AXIS_W)
  const H = height

  const x = (t: number) => PAD_X + ((t - t0) / spanT) * (plotW - PAD_X * 2)
  const y = (v: number) => H - PAD_Y - ((v - lo) / spanV) * (H - PAD_Y * 2)
  const pathOf = (pts: Pt[]) => pts.map((p) => `${x(p.t).toFixed(1)},${y(p.v).toFixed(1)}`).join(' ')

  const toggle = (key: string) => setHidden((prev) => {
    const next = new Set(prev)
    if (next.has(key)) next.delete(key); else next.add(key)
    return next
  })

  const showExpand = !totalOn && series.length > collapseLimit
  const restCount = series.length - collapseLimit
  const lastV = totalPoints.length ? totalPoints[totalPoints.length - 1].v : 0

  return (
    <div>
      {/* 图例：点击显隐 */}
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px 12px', marginBottom: 4 }}>
        {display.map((s) => {
          const off = hidden.has(s.key)
          return (
            <span
              key={s.key}
              onClick={() => toggle(s.key)}
              title={off ? '点击显示' : '点击隐藏'}
              style={{ cursor: 'pointer', fontSize: 12, opacity: off ? 0.35 : 1, userSelect: 'none', display: 'inline-flex', alignItems: 'center', gap: 4 }}
            >
              <span style={{ width: 8, height: 8, borderRadius: 2, background: off ? '#d9d9d9' : s.color, display: 'inline-block' }} />
              {s.name}
            </span>
          )
        })}
        {showExpand && (
          <span onClick={() => setExpanded((e) => !e)} style={{ cursor: 'pointer', fontSize: 12, color: '#1677ff', userSelect: 'none' }}>
            {expanded ? '收起' : `展开其余 ${restCount} 个`}
          </span>
        )}
      </div>
      {/* 图主体：左 Y 轴标签列 + 绘图区 */}
      <div style={{ display: 'flex' }}>
        <div style={{ width: AXIS_W, flexShrink: 0, position: 'relative', height: H }}>
          {ticks.map((t) => (
            <div key={t} style={{ position: 'absolute', top: y(t) - 7, right: 6, fontSize: 11, color: '#8c8c8c', lineHeight: '14px' }}>
              {fmtTick(t)}
            </div>
          ))}
        </div>
        <div style={{ flex: 1 }}>
          <svg width="100%" height={H} viewBox={`0 0 ${plotW} ${H}`} preserveAspectRatio="none" style={{ display: 'block' }}>
            {ticks.map((t) => (
              <line key={`g${t}`} x1={PAD_X} y1={y(t)} x2={plotW - PAD_X} y2={y(t)} stroke="#f0f0f0" strokeWidth={1} />
            ))}
            {visible.map((s) => (
              <polyline
                key={s.key}
                points={pathOf(s.points)}
                fill="none"
                stroke={s.color}
                strokeWidth={s.key === '__total__' ? 2 : 1.5}
                strokeDasharray={s.key === '__total__' ? '6 3' : undefined}
              />
            ))}
          </svg>
        </div>
      </div>
      {/* 底部信息：时间范围 + 合计峰值/当前 */}
      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, color: '#8c8c8c', marginTop: 4, paddingLeft: AXIS_W }}>
        <span>{fmtT(t0)}</span>
        <span>峰值 {fmtVal(totalPeak)} {unit} · 当前 {fmtVal(lastV)} {unit}</span>
        <span>{fmtT(t1)}</span>
      </div>
    </div>
  )
}
