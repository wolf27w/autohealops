// 统一运维大屏（P1-R7）—— Netdata 式重做
// 实时监控：维度(节点/无状态/有状态/守护集/容器) × 指标(CPU/内存/网络 [+节点:磁盘/负载]) 同屏展示
import { useEffect, useMemo, useState } from 'react'
import { Card, Select, Tag, Space, Button, Table, Statistic, Row, Col, Empty, Segmented, theme, Input, Progress, Tooltip } from 'antd'
import { FullscreenOutlined, ReloadOutlined, ArrowLeftOutlined, SearchOutlined } from '@ant-design/icons'
import { Line } from '@ant-design/charts'
import { useNavigate } from 'react-router-dom'
import { getDashboard, getClusterRange, getClusterNamespaces } from '../services/api'
import type { DashboardData, ClusterHealthDetail } from '../types'
import type { RangeSeries } from '../services/api'
import { useThemeMode } from '../utils/theme'

// ---------- 常量 ----------
const DIMS = [
  { value: 'node', label: '节点' },
  { value: 'workload', label: '工作负载' },
  { value: 'pod', label: '容器(Pod)' },
]
const WL_TYPES = [
  { value: 'all', label: '全部' },
  { value: 'deploy', label: '无状态' },
  { value: 'stateful', label: '有状态' },
  { value: 'daemonset', label: '守护集' },
]
const WINDOWS = [
  { value: 10, label: '10m' }, { value: 30, label: '30m' },
  { value: 60, label: '1h' }, { value: 180, label: '3h' },
]
// 工作负载名归一（Deployment 双段哈希 / StatefulSet 序号 / DaemonSet 单段哈希）
const WL_REGEX = '(.+?)(-[0-9a-f]{5,10}-[0-9a-z]{5}|-[0-9]+|-[0-9a-f]{5,10})$'
const WL_TYPE_SEL: Record<string, string> = {
  deploy: ',pod=~".+-[0-9a-f]{5,10}-[0-9a-z]{5}$"',
  stateful: ',pod=~"^[^-]+-[0-9]+$"',
  daemonset: ',pod=~"^[^-]+-[0-9a-f]{5,10}$"',
}

const METRIC_DEFS: Record<string, { value: string; label: string }[]> = {
  node: [{ value: 'cpu', label: 'CPU' }, { value: 'mem', label: '内存' }, { value: 'net', label: '网络' }, { value: 'disk', label: '磁盘' }, { value: 'load', label: '负载' }],
  workload: [{ value: 'cpu', label: 'CPU' }, { value: 'mem', label: '内存' }, { value: 'net', label: '网络' }],
  pod: [{ value: 'cpu', label: 'CPU' }, { value: 'mem', label: '内存' }, { value: 'net', label: '网络' }],
}

// ---------- 工具 ----------
function useClock() {
  const [now, setNow] = useState(new Date())
  useEffect(() => { const t = window.setInterval(() => setNow(new Date()), 1000); return () => window.clearInterval(t) }, [])
  return now
}

function buildPromql(dim: string, metric: string, ns: string, wlType: string): string {
  const nsSel = ns ? `namespace="${ns}",` : ''
  const podSel = `${nsSel}container!="",container!="POD"`
  const netSel = `${nsSel}namespace!=""`
  if (dim === 'node') {
    if (metric === 'cpu') return '100 - (avg by(instance)(rate(node_cpu_seconds_total{mode="idle"}[5m])) * 100)'
    if (metric === 'mem') return '(1 - node_memory_MemAvailable_bytes/node_memory_MemTotal_bytes)*100'
    if (metric === 'net') return 'sum by(instance)(rate(node_network_receive_bytes_total[5m]) + rate(node_network_transmit_bytes_total[5m]))'
    if (metric === 'disk') return '100 - (sum by(instance)(node_filesystem_avail_bytes{fstype!~"tmpfs|overlay"}) / sum by(instance)(node_filesystem_size_bytes{fstype!~"tmpfs|overlay"}) * 100)'
    if (metric === 'load') return 'node_load1'
  }
  const typeSel = dim === 'workload' ? (WL_TYPE_SEL[wlType] || '') : ''
  if (dim === 'pod') {
    if (metric === 'cpu') return `sum by(namespace,pod)(rate(container_cpu_usage_seconds_total{${podSel}}[5m]))`
    if (metric === 'mem') return `sum by(namespace,pod)(container_memory_working_set_bytes{${podSel}})`
    if (metric === 'net') return `sum by(namespace,pod)(rate(container_network_receive_bytes_total{${netSel}}[5m]) + rate(container_network_transmit_bytes_total{${netSel}}[5m]))`
  }
  const inner = metric === 'cpu'
    ? `sum by(namespace,pod)(rate(container_cpu_usage_seconds_total{${podSel}${typeSel}}[5m]))`
    : metric === 'mem'
      ? `sum by(namespace,pod)(container_memory_working_set_bytes{${podSel}${typeSel}})`
      : `sum by(namespace,pod)(rate(container_network_receive_bytes_total{${netSel}${typeSel}}[5m]) + rate(container_network_transmit_bytes_total{${netSel}${typeSel}}[5m]))`
  return `sum by(namespace,workload)(label_replace(${inner}, "workload", "$1", "pod", "${WL_REGEX}"))`
}

function unitOf(dim: string, metric: string): string {
  if (metric === 'cpu') return dim === 'node' ? '%' : 'core'
  if (metric === 'mem') return dim === 'node' ? '%' : 'bytes'
  if (metric === 'net') return 'bytes/s'
  if (metric === 'disk') return '%'
  return ''
}

function seriesLabel(m: Record<string, string>): string {
  if (m.instance) return m.instance
  if (m.workload) return `${m.namespace || ''}/${m.workload}`
  if (m.pod) return `${m.namespace || ''}/${m.pod}`
  return JSON.stringify(m)
}
function hhmmss(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString('zh-CN', { hour12: false })
}
function fmtBytes(v: number | null): string {
  if (v === null || v === undefined) return '-'
  const u = ['B', 'KiB', 'MiB', 'GiB', 'TiB']
  let x = v, i = 0
  while (x >= 1024 && i < u.length - 1) { x /= 1024; i++ }
  return `${x.toFixed(i === 0 ? 0 : 1)}${u[i]}`
}
function fmtVal(v: number | null, unit: string): string {
  if (v === null || v === undefined) return '-'
  if (unit === 'bytes' || unit === 'bytes/s') return fmtBytes(v) + (unit === 'bytes/s' ? '/s' : '')
  if (unit === 'core') return v.toFixed(3)
  if (unit === '%' || unit === '') return `${v.toFixed(1)}${unit}`
  return `${v.toFixed(1)}${unit}`
}
function riskLevel(c: ClusterHealthDetail): { text: string; color: string } {
  if (c.status !== 'healthy') return { text: '异常', color: 'red' }
  if ((c.cpu_pct ?? 0) >= 85 || (c.mem_pct ?? 0) >= 85) return { text: '高水位', color: 'orange' }
  if ((c.alert_pending ?? 0) > 0) return { text: '有待办', color: 'gold' }
  return { text: '正常', color: 'green' }
}
const lastVal = (s: RangeSeries) => (s.values.length ? s.values[s.values.length - 1][1] : 0)
const nameOf = (s: RangeSeries) => seriesLabel(s.metric)

function Sparkline({ values, color, width = 70, height = 22 }: { values: number[]; color: string; width?: number; height?: number }) {
  if (values.length < 2) return <div style={{ width, height }} />
  const min = Math.min(...values), max = Math.max(...values), rng = max - min || 1
  const step = width / (values.length - 1)
  const pts = values.map((v, i) => `${(i * step).toFixed(1)},${(height - 3 - ((v - min) / rng) * (height - 6)).toFixed(1)}`)
  return <svg width={width} height={height}><polyline fill="none" stroke={color} strokeWidth="1.3" points={pts.join(' ')} /></svg>
}

// ---------- 实时监控面板（Netdata 式：多指标同屏） ----------
function MonitorPanel({ clusterId, clusterName, nsList }: { clusterId?: number; clusterName: string; nsList: string[] }) {
  const { token } = theme.useToken()
  const { mode } = useThemeMode()
  const chartTheme = mode === 'dark' ? ({ theme: 'classicDark' } as const) : {}
  const [dim, setDim] = useState('node')
  const [wlType, setWlType] = useState('all')
  const [min, setMin] = useState(30)
  const [ns, setNs] = useState('')
  const [kw, setKw] = useState('')
  const [seriesMap, setSeriesMap] = useState<Record<string, RangeSeries[]>>({})
  const [loading, setLoading] = useState(false)

  const metricDefs = METRIC_DEFS[dim]

  useEffect(() => {
    if (!clusterId) { setSeriesMap({}); return }
    let alive = true
    const step = Math.max(15, Math.round(min * 60 / 80))
    const fetchOne = async (m: { value: string }) => {
      try {
        const promql = buildPromql(dim, m.value, dim === 'node' ? '' : ns, wlType)
        const d = await getClusterRange(clusterId, promql, min, step)
        if (alive) setSeriesMap((s) => ({ ...s, [m.value]: d.series || [] }))
      } catch { /* ignore */ }
    }
    setLoading(true)
    Promise.all(metricDefs.map(fetchOne)).finally(() => { if (alive) setLoading(false) })
    const t = window.setInterval(() => Promise.all(metricDefs.map(fetchOne)), 30000)
    return () => { alive = false; window.clearInterval(t) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [clusterId, dim, wlType, min, ns])

  // 名称搜索过滤（作用于各指标系列）
  const filteredMap = useMemo(() => {
    const k = kw.trim().toLowerCase()
    if (!k) return seriesMap
    const out: Record<string, RangeSeries[]> = {}
    for (const key of Object.keys(seriesMap)) out[key] = seriesMap[key].filter((s) => seriesLabel(s.metric).toLowerCase().includes(k))
    return out
  }, [seriesMap, kw])

  // 每个指标：Top6 曲线数据
  const rowsOf = (m: string) => {
    const series = filteredMap[m] || []
    return [...series].sort((a, b) => lastVal(b) - lastVal(a)).slice(0, 6)
      .flatMap((s) => s.values.map(([ts, v]) => ({ t: hhmmss(ts), v: Number(v.toFixed(3)), name: seriesLabel(s.metric) })))
      .sort((a, b) => a.t.localeCompare(b.t))
  }
  // 合并 Top 表：以 CPU 系列为主序，补齐其它指标当前值
  const items = useMemo(() => {
    const cpu = filteredMap['cpu'] || []
    const lastOf = (m: string) => Object.fromEntries((filteredMap[m] || []).map((s) => [nameOf(s), lastVal(s)]))
    const mem = lastOf('mem'), net = lastOf('net'), disk = lastOf('disk'), load = lastOf('load')
    return [...cpu].sort((a, b) => lastVal(b) - lastVal(a)).slice(0, 20).map((s) => {
      const n = nameOf(s)
      return { name: n, cpu: lastVal(s), mem: mem[n], net: net[n], disk: disk?.[n], load: load?.[n], cpuVals: s.values.map(([, v]) => v) }
    })
  }, [filteredMap])

  const cols = [
    { title: '名称', key: 'name', width: 200, render: (x: any) => <Tooltip title={x.name}><span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', display: 'inline-block', maxWidth: 190 }}>{x.name}</span></Tooltip> },
    { title: 'CPU', key: 'cpu', render: (x: any) => <span style={{ color: token.colorPrimary }}>{fmtVal(x.cpu, unitOf(dim, 'cpu'))}</span> },
    { title: '内存', key: 'mem', render: (x: any) => fmtVal(x.mem, unitOf(dim, 'mem')) },
    { title: '网络', key: 'net', render: (x: any) => fmtVal(x.net, 'bytes/s') },
  ]
  if (dim === 'node') {
    cols.push({ title: '磁盘', key: 'disk', render: (x: any) => fmtVal(x.disk, '%') })
    cols.push({ title: '负载', key: 'load', render: (x: any) => fmtVal(x.load, '') })
  }

  return (
    <Card size="small" variant="borderless"
      title={<Space>实时监控<Tag color="blue">{clusterName}</Tag><span style={{ fontSize: 12, color: token.colorTextSecondary }}>CPU / 内存 / 网络{ dim === 'node' ? ' / 磁盘 / 负载' : '' } 同屏，按维度下钻</span></Space>}
      extra={<Space wrap>
        <Segmented size="small" value={dim} onChange={(v) => setDim(v as string)} options={DIMS} />
        {dim === 'workload' && <Segmented size="small" value={wlType} onChange={(v) => setWlType(v as string)} options={WL_TYPES} />}
        <Segmented size="small" value={min} onChange={(v) => setMin(v as number)} options={WINDOWS} />
      </Space>}>
      <Space wrap style={{ marginBottom: 10 }}>
        {dim !== 'node' && (
          <Select allowClear showSearch size="small" style={{ width: 170 }} placeholder="命名空间" value={ns || undefined}
            onChange={(v) => setNs(v || '')} options={nsList.map((n) => ({ value: n, label: n }))} />
        )}
        <Input allowClear size="small" prefix={<SearchOutlined />} placeholder="搜索名称/关键字" style={{ width: 200 }}
          value={kw} onChange={(e) => setKw(e.target.value)} />
        <span style={{ fontSize: 12, color: token.colorTextSecondary }}>曲线各取 Top6，下表按 CPU 排序</span>
      </Space>

      {/* 多指标同屏曲线 */}
      <Row gutter={12} style={{ marginBottom: 10 }}>
        {metricDefs.map((m) => {
          const rows = rowsOf(m.value)
          return (
            <Col key={m.value} flex="1" style={{ minWidth: 200 }}>
              <div style={{ fontSize: 13, fontWeight: 600, color: token.colorText, marginBottom: 4 }}>{m.label}</div>
              {rows.length === 0
                ? <Empty description="无数据" image={Empty.PRESENTED_IMAGE_SIMPLE} style={{ padding: 8 }} />
                : <Line height={190} data={rows} xField="t" yField="v" colorField="name" smooth
                  point={{ size: 1.5, shape: 'circle' }} {...chartTheme}
                  axis={{ x: { label: null } }} legend={false} />}
            </Col>
          )
        })}
      </Row>

      {/* 合并 Top 表 */}
      <Table size="small" rowKey="name" pagination={false} loading={loading && items.length === 0} dataSource={items}
        columns={cols} locale={{ emptyText: <Empty description="无数据（需该集群部署 Prometheus）" image={Empty.PRESENTED_IMAGE_SIMPLE} /> }} />
    </Card>
  )
}

// ---------- 页面 ----------
export default function Screen() {
  const navigate = useNavigate()
  const now = useClock()
  const { token } = theme.useToken()
  const { mode } = useThemeMode()
  const [data, setData] = useState<DashboardData | null>(null)
  const [sel, setSel] = useState<number | 'all'>('all')
  const [loading, setLoading] = useState(false)
  const [nsList, setNsList] = useState<string[]>([])

  const load = () => { setLoading(true); getDashboard().then(setData).catch(() => {}).finally(() => setLoading(false)) }
  useEffect(() => { load(); const t = window.setInterval(load, 15000); return () => window.clearInterval(t) }, [])

  const details: ClusterHealthDetail[] = data?.cluster_details || []
  const shown = sel === 'all' ? details : details.filter((c) => c.id === sel)
  const agg = useMemo(() => {
    const a = { node_total: 0, node_ready: 0, workloads: 0, pods: 0, alert_pending: 0 }
    for (const c of shown) { a.node_total += c.node_total || 0; a.node_ready += c.node_ready || 0; a.workloads += c.workloads || 0; a.pods += c.pods || 0; a.alert_pending += c.alert_pending || 0 }
    return a
  }, [shown])

  const effCluster = sel === 'all' ? details[0]?.id : sel
  const effClusterName = sel === 'all' ? (details[0]?.name || '-') : (details.find((c) => c.id === sel)?.name || '-')

  useEffect(() => {
    setNsList([])
    if (!effCluster) return
    getClusterNamespaces(effCluster).then((d) => setNsList((d as string[]) || [])).catch(() => setNsList([]))
  }, [effCluster])

  const core = data?.core
  const trend24 = data?.trend_24h || []
  const abnormal = (data?.abnormal_pods || []).filter((p) => sel === 'all' || p.cluster_name === shown[0]?.name).slice(0, 12)
  const faultTop = data?.fault_top || []
  const statusDist = (data?.status_dist || []) as { status?: string; count?: number }[]
  const chartTheme = mode === 'dark' ? ({ theme: 'classicDark' } as const) : {}

  const kpi = [
    { label: '集群', value: `${core?.cluster_healthy ?? 0}`, suffix: `/${core?.cluster_total ?? 0}`, color: token.colorPrimary },
    { label: '节点就绪', value: `${agg.node_ready}`, suffix: `/${agg.node_total}`, color: '#0ea5e9' },
    { label: 'Pod', value: `${agg.pods}`, suffix: '', color: '#8b5cf6' },
    { label: '工作负载', value: `${agg.workloads}`, suffix: '', color: '#0ea5e9' },
    { label: '待处理告警', value: `${core?.alert_pending ?? 0}`, suffix: '', color: (core?.alert_pending ?? 0) > 0 ? '#ef4444' : '#12b981' },
    { label: '自愈率', value: `${((core?.heal_rate ?? 0) * 100).toFixed(0)}`, suffix: '%', color: '#12b981' },
  ]

  const enterFull = () => { document.documentElement.requestFullscreen?.() }

  return (
    <div style={{ minHeight: '100vh', background: token.colorBgLayout, padding: 16 }}>
      {/* 头部 */}
      <Card size="small" variant="borderless" style={{ marginBottom: 12, background: token.colorBgContainer }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 8 }}>
          <Space size={16} wrap>
            <span style={{ fontSize: 20, fontWeight: 700, color: token.colorPrimary }}>API-OPS 运维大屏</span>
            <Select value={sel} onChange={(v) => setSel(v)} style={{ width: 160 }}
              options={[{ value: 'all', label: '全部集群' }, ...details.map((c) => ({ value: c.id, label: c.name }))]} />
            <Tag color={data?.health?.api_server && data?.health?.database ? 'green' : 'red'}>
              {data?.health?.api_server && data?.health?.database ? '平台健康' : '平台异常'}
            </Tag>
          </Space>
          <Space size={12} wrap>
            <span style={{ fontSize: 18, fontWeight: 600, fontVariantNumeric: 'tabular-nums', color: token.colorText }}>{now.toLocaleTimeString('zh-CN', { hour12: false })}</span>
            <Button size="small" icon={<ReloadOutlined />} loading={loading} onClick={load}>刷新</Button>
            <Button size="small" icon={<FullscreenOutlined />} onClick={enterFull}>全屏</Button>
            <Button size="small" icon={<ArrowLeftOutlined />} onClick={() => navigate(-1)}>返回</Button>
          </Space>
        </div>
      </Card>

      {/* KPI 条 */}
      <Row gutter={[12, 12]} style={{ marginBottom: 12 }}>
        {kpi.map((k) => (
          <Col key={k.label} xs={12} sm={8} md={4} flex="1">
            <Card size="small" variant="borderless" styles={{ body: { padding: '12px 16px' } }}>
              <div style={{ color: token.colorTextSecondary, fontSize: 12 }}>{k.label}</div>
              <div style={{ fontSize: 28, fontWeight: 700, lineHeight: 1.2, color: k.color }}>
                {k.value}<span style={{ fontSize: 15, fontWeight: 500, color: token.colorTextSecondary }}>{k.suffix}</span>
              </div>
            </Card>
          </Col>
        ))}
      </Row>

      {/* 实时监控（主角，整行） */}
      <Row gutter={[12, 12]} style={{ marginBottom: 12 }}>
        <Col span={24}>
          <MonitorPanel clusterId={effCluster} clusterName={effClusterName} nsList={nsList} />
        </Col>
      </Row>

      {/* 集群健康墙 + 告警趋势（对等） */}
      <Row gutter={[12, 12]} style={{ marginBottom: 12 }}>
        <Col xs={24} lg={12}>
          <Card size="small" title="集群健康墙" variant="borderless" extra={<Tag color="blue">{shown.length} 集群</Tag>}>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10, minHeight: 260 }}>
              {shown.map((c) => {
                const r = riskLevel(c)
                return (
                  <div key={c.id} style={{ border: `1px solid ${token.colorBorderSecondary}`, borderRadius: 8, padding: '8px 12px' }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                      <b style={{ fontSize: 14 }}>{c.name}</b><Tag color={r.color}>{r.text}</Tag>
                    </div>
                    <div style={{ fontSize: 12, color: token.colorTextSecondary, margin: '6px 0' }}>
                      节点 {c.node_ready}/{c.node_total} · 工作负载 {c.workloads} · Pod {c.pods} · 待办 {c.alert_pending}
                    </div>
                    <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
                      <div style={{ flex: 1 }}>
                        <div style={{ fontSize: 11, color: token.colorTextSecondary, display: 'flex', justifyContent: 'space-between' }}><span>CPU</span><span>{c.cpu_pct ?? '-'}%</span></div>
                        <Progress percent={c.cpu_pct ?? 0} size="small" showInfo={false} strokeColor={c.cpu_pct && c.cpu_pct >= 85 ? '#ef4444' : c.cpu_pct && c.cpu_pct >= 70 ? '#f59e0b' : '#12b981'} />
                      </div>
                      <div style={{ flex: 1 }}>
                        <div style={{ fontSize: 11, color: token.colorTextSecondary, display: 'flex', justifyContent: 'space-between' }}><span>内存</span><span>{c.mem_pct ?? '-'}%</span></div>
                        <Progress percent={c.mem_pct ?? 0} size="small" showInfo={false} strokeColor={c.mem_pct && c.mem_pct >= 85 ? '#ef4444' : c.mem_pct && c.mem_pct >= 70 ? '#f59e0b' : '#12b981'} />
                      </div>
                    </div>
                  </div>
                )
              })}
              {shown.length === 0 && <Empty description="无集群" />}
              {statusDist.length > 0 && (
                <div style={{ borderTop: `1px dashed ${token.colorBorderSecondary}`, paddingTop: 8, marginTop: 2 }}>
                  <div style={{ fontSize: 12, color: token.colorTextSecondary, marginBottom: 6 }}>Pod 状态分布</div>
                  <Space wrap>
                    {statusDist.map((s) => (
                      <Tag key={s.status} color={s.status === 'Running' ? 'green' : s.status === 'Pending' ? 'orange' : s.status === 'Failed' ? 'red' : 'default'}>
                        {s.status || '-'} {s.count ?? 0}
                      </Tag>
                    ))}
                  </Space>
                </div>
              )}
            </div>
          </Card>
        </Col>
        <Col xs={24} lg={12}>
          <Card size="small" title="告警趋势（24h）" variant="borderless">
            <div style={{ minHeight: 260 }}>
              {trend24.length > 0 ? (
                <Line height={260} data={trend24.flatMap((p) => {
                  const g = p as { hour?: string; alerts: number; healed: number }
                  return [{ hour: g.hour || '', type: '告警', value: g.alerts }, { hour: g.hour || '', type: '已解决', value: g.healed }]
                })} xField="hour" yField="value" colorField="type" smooth point={{ size: 2 }} {...chartTheme} legend={{ color: { position: 'top' } }} />
              ) : <Empty description="暂无趋势数据" />}
            </div>
          </Card>
        </Col>
      </Row>

      {/* 异常 Pod + 故障 Top（对等） */}
      <Row gutter={[12, 12]}>
        <Col xs={24} lg={12}>
          <Card size="small" title="异常 Pod" variant="borderless">
            <Table rowKey={(r) => `${r.cluster_name}/${r.namespace}/${r.pod_name}`} size="small" dataSource={abnormal} pagination={false}
              columns={[
                { title: '命名空间', dataIndex: 'namespace', width: 130 },
                { title: 'Pod', dataIndex: 'pod_name' },
                { title: '故障', dataIndex: 'fault_type', width: 130, render: (v) => v ? <Tag color="volcano">{v}</Tag> : '-' },
                { title: '次', dataIndex: 'alert_count', width: 60 },
              ]}
              locale={{ emptyText: <Empty description="无异常 Pod" /> }} />
          </Card>
        </Col>
        <Col xs={24} lg={12}>
          <Card size="small" title="故障类型 Top" variant="borderless">
            {faultTop.length === 0 ? <Empty description="暂无数据" /> : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 10, paddingTop: 4 }}>
                {faultTop.slice(0, 10).map((f, i) => {
                  const max = Math.max(...faultTop.map((x) => x.count), 1)
                  return (
                    <div key={f.fault_type}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 13 }}>
                        <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: '78%' }}>{i + 1}. {f.fault_type}</span>
                        <b>{f.count}</b>
                      </div>
                      <div style={{ height: 7, background: token.colorFillSecondary, borderRadius: 3, marginTop: 4 }}>
                        <div style={{ width: `${(f.count / max) * 100}%`, height: '100%', borderRadius: 3, background: token.colorPrimary }} />
                      </div>
                    </div>
                  )
                })}
              </div>
            )}
          </Card>
        </Col>
      </Row>
    </div>
  )
}
