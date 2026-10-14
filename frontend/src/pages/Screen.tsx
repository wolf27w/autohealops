// 统一监控大屏（P1-R7）：多集群总览 —— 配色跟随系统主题（antd token，浅/深色自适应）
// 数据源：/stats/dashboard（聚合/集群明细/趋势/异常 Pod/故障 Top）+ /monitoring（实时监控：节点/工作负载/容器 × CPU/内存/网络）
import { useEffect, useMemo, useState } from 'react'
import { Card, Select, Tag, Space, Button, Table, Statistic, Row, Col, Empty, Segmented, theme, Input } from 'antd'
import { FullscreenOutlined, ReloadOutlined, ArrowLeftOutlined, SearchOutlined } from '@ant-design/icons'
import { Line } from '@ant-design/charts'
import { useNavigate } from 'react-router-dom'
import { getDashboard, getClusterRange, getClusterNamespaces } from '../services/api'
import type { DashboardData, ClusterHealthDetail } from '../types'
import type { RangeSeries } from '../services/api'
import { useThemeMode } from '../utils/theme'

function useClock() {
  const [now, setNow] = useState(new Date())
  useEffect(() => { const t = window.setInterval(() => setNow(new Date()), 1000); return () => window.clearInterval(t) }, [])
  return now
}

const DIMS = [
  { value: 'node', label: '节点' },
  { value: 'workload', label: '工作负载' },
  { value: 'pod', label: '容器(Pod)' },
]
const METRICS = [
  { value: 'cpu', label: 'CPU' },
  { value: 'mem', label: '内存' },
  { value: 'net', label: '网络' },
]
const WINDOWS = [
  { value: 10, label: '10m' }, { value: 30, label: '30m' },
  { value: 60, label: '1h' }, { value: 180, label: '3h' },
]

// 工作负载名：从 Pod 名剥离 ReplicaSet 哈希+Pod 哈希（Deployment），或 StatefulSet 序号
const WL_REGEX = '(.+?)(-[0-9a-f]{5,10}-[0-9a-z]{5}|-[0-9]+)$'

function buildPromql(dim: string, metric: string, ns: string): string {
  const nsSel = ns ? `namespace="${ns}",` : ''
  const podSel = `${nsSel}container!="",container!="POD"`
  const netSel = `${nsSel}namespace!=""`
  if (dim === 'node') {
    if (metric === 'cpu') return '100 - (avg by(instance)(rate(node_cpu_seconds_total{mode="idle"}[5m])) * 100)'
    if (metric === 'mem') return '(1 - node_memory_MemAvailable_bytes/node_memory_MemTotal_bytes)*100'
    if (metric === 'net') return 'sum by(instance)(rate(node_network_receive_bytes_total[5m]) + rate(node_network_transmit_bytes_total[5m]))'
  }
  if (dim === 'pod') {
    if (metric === 'cpu') return `sum by(namespace,pod)(rate(container_cpu_usage_seconds_total{${podSel}}[5m]))`
    if (metric === 'mem') return `sum by(namespace,pod)(container_memory_working_set_bytes{${podSel}})`
    if (metric === 'net') return `sum by(namespace,pod)(rate(container_network_receive_bytes_total{${netSel}}[5m]) + rate(container_network_transmit_bytes_total{${netSel}}[5m]))`
  }
  // workload（Deployment / StatefulSet / DaemonSet）：pod 名 label_replace 归一到 workload
  const inner = (m: string) => {
    if (metric === 'cpu') return `sum by(namespace,pod)(rate(container_cpu_usage_seconds_total{${podSel}}[5m]))`
    if (metric === 'mem') return `sum by(namespace,pod)(container_memory_working_set_bytes{${podSel}})`
    return `sum by(namespace,pod)(rate(container_network_receive_bytes_total{${netSel}}[5m]) + rate(container_network_transmit_bytes_total{${netSel}}[5m]))`
  }
  return `sum by(namespace,workload)(label_replace(${inner('x')}, "workload", "$1", "pod", "${WL_REGEX}"))`
}

function unitOf(dim: string, metric: string): string {
  if (metric === 'cpu') return dim === 'node' ? '%' : 'core'
  if (metric === 'mem') return dim === 'node' ? '%' : 'bytes'
  return 'bytes/s'
}

function seriesLabel(m: Record<string, string>): string {
  if (m.instance) return m.instance
  if (m.workload) return `${m.namespace || ''}/${m.workload}`
  if (m.container) return m.pod ? `${m.container} · ${m.pod}` : m.container
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
  return `${x.toFixed(i === 0 ? 0 : 1)} ${u[i]}`
}
function fmtVal(v: number | null, unit: string): string {
  if (v === null || v === undefined) return '-'
  if (unit === 'bytes' || unit === 'bytes/s') return fmtBytes(v) + (unit === 'bytes/s' ? '/s' : '')
  if (unit === 'core') return v.toFixed(3)
  return `${v.toFixed(1)}${unit}`
}
function riskLevel(c: ClusterHealthDetail): { text: string; color: string } {
  if (c.status !== 'healthy') return { text: '异常', color: 'red' }
  if ((c.cpu_pct ?? 0) >= 85 || (c.mem_pct ?? 0) >= 85) return { text: '高水位', color: 'orange' }
  if ((c.alert_pending ?? 0) > 0) return { text: '有待办', color: 'gold' }
  return { text: '正常', color: 'green' }
}

const lastVal = (s: RangeSeries) => (s.values.length ? s.values[s.values.length - 1][1] : 0)
function trendRowsOf(series: RangeSeries[]) {
  return [...series].sort((a, b) => lastVal(b) - lastVal(a)).slice(0, 6)
    .flatMap((s) => {
      const nm = seriesLabel(s.metric)
      return s.values.map(([ts, v]) => ({ t: hhmmss(ts), v: Number(v.toFixed(3)), name: nm }))
    })
}
function topListOf(series: RangeSeries[], limit = 100) {
  return [...series].sort((a, b) => lastVal(b) - lastVal(a)).slice(0, limit)
    .map((s) => ({ name: seriesLabel(s.metric), v: lastVal(s) }))
}

// 实时监控面板：维度(节点/工作负载/容器) × 指标(CPU/内存/网络)，Netdata 式
function MonitorPanel({ clusterId, clusterName, nsList }: { clusterId?: number; clusterName: string; nsList: string[] }) {
  const { token } = theme.useToken()
  const { mode } = useThemeMode()
  const chartTheme = mode === 'dark' ? ({ theme: 'classicDark' } as const) : {}
  const [dim, setDim] = useState('node')
  const [metric, setMetric] = useState('cpu')
  const [min, setMin] = useState(30)
  const [ns, setNs] = useState('')
  const [kw, setKw] = useState('')
  const [series, setSeries] = useState<RangeSeries[]>([])
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!clusterId) { setSeries([]); return }
    let alive = true
    const promql = buildPromql(dim, metric, dim === 'node' ? '' : ns)
    const step = Math.max(15, Math.round(min * 60 / 80))
    const fetchT = () => {
      setLoading(true)
      getClusterRange(clusterId, promql, min, step)
        .then((d) => { if (alive) setSeries(d.series || []) })
        .catch(() => { if (alive) setSeries([]) })
        .finally(() => { if (alive) setLoading(false) })
    }
    fetchT()
    const t = window.setInterval(fetchT, 30000)
    return () => { alive = false; window.clearInterval(t) }
  }, [clusterId, dim, metric, min, ns])

  const filtered = useMemo(() => {
    const k = kw.trim().toLowerCase()
    if (!k) return series
    return series.filter((s) => seriesLabel(s.metric).toLowerCase().includes(k))
  }, [series, kw])
  const rows = useMemo(() => trendRowsOf(filtered), [filtered])
  const top = useMemo(() => topListOf(filtered), [filtered])
  const maxT = Math.max(...top.map((x) => x.v), 1)
  const unit = unitOf(dim, metric)

  return (
    <Card size="small" variant="borderless"
      title={<Space>实时监控<Tag color="blue">{clusterName}</Tag><span style={{ fontSize: 12, color: token.colorTextSecondary }}>（节点 / 工作负载 / 容器 × CPU / 内存 / 网络）</span></Space>}
      extra={<Space wrap>
        <Segmented size="small" value={dim} onChange={(v) => setDim(v as string)} options={DIMS} />
        <Segmented size="small" value={metric} onChange={(v) => setMetric(v as string)} options={METRICS} />
        <Segmented size="small" value={min} onChange={(v) => setMin(v as number)} options={WINDOWS} />
      </Space>}>
      <Space wrap style={{ marginBottom: 8 }}>
        {dim !== 'node' && (
          <Select allowClear showSearch size="small" style={{ width: 180 }} placeholder="命名空间（空=全部）" value={ns || undefined}
            onChange={(v) => setNs(v || '')} options={nsList.map((n) => ({ value: n, label: n }))} />
        )}
        <Input allowClear size="small" prefix={<SearchOutlined />} placeholder="搜索筛选（名称/关键字）" style={{ width: 230 }}
          value={kw} onChange={(e) => setKw(e.target.value)} />
        <span style={{ fontSize: 12, color: token.colorTextSecondary }}>显示 {filtered.length} / {series.length} 条（曲线 Top6，列表可滚动）</span>
      </Space>
      <Row gutter={12}>
        <Col xs={24} md={16}>
          {loading && rows.length === 0 ? <div style={{ padding: 20, textAlign: 'center', color: token.colorTextSecondary }}>加载中…</div>
            : rows.length === 0 ? <Empty description="无数据（需该集群部署 Prometheus）" />
              : <Line height={240} data={rows} xField="t" yField="v" colorField="name" smooth
                point={{ size: 2 }} {...chartTheme}
                legend={{ color: { position: 'top' } }} />}
        </Col>
        <Col xs={24} md={8}>
          <div style={{ fontSize: 12, color: token.colorTextSecondary, marginBottom: 6 }}>当前值 Top（按最新）</div>
          {top.length === 0 ? <Empty description="无数据" image={Empty.PRESENTED_IMAGE_SIMPLE} />
            : <div style={{ maxHeight: 240, overflow: 'auto' }}>
                {top.map((x, i) => (
                  <div key={i} style={{ marginBottom: 6 }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12 }}>
                      <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: '68%' }}>{x.name}</span>
                      <b>{fmtVal(x.v, unit)}</b>
                    </div>
                    <div style={{ height: 6, borderRadius: 3, background: token.colorFillSecondary, marginTop: 2 }}>
                      <div style={{ width: `${(x.v / maxT) * 100}%`, height: '100%', borderRadius: 3, background: token.colorPrimary }} />
                    </div>
                  </div>
                ))}
              </div>}
        </Col>
      </Row>
    </Card>
  )
}

export default function Screen() {
  const navigate = useNavigate()
  const now = useClock()
  const { token } = theme.useToken()
  const { mode } = useThemeMode()
  const [data, setData] = useState<DashboardData | null>(null)
  const [sel, setSel] = useState<number | 'all'>('all')
  const [loading, setLoading] = useState(false)
  const [nsList, setNsList] = useState<string[]>([])

  const load = () => {
    setLoading(true)
    getDashboard().then((d) => setData(d)).catch(() => {}).finally(() => setLoading(false))
  }
  useEffect(() => { load(); const t = window.setInterval(load, 15000); return () => window.clearInterval(t) }, [])

  const details: ClusterHealthDetail[] = data?.cluster_details || []
  const shown = sel === 'all' ? details : details.filter((c) => c.id === sel)
  const agg = useMemo(() => {
    const a = { node_total: 0, node_ready: 0, workloads: 0, pods: 0, namespaces: 0, alert_pending: 0 }
    for (const c of shown) {
      a.node_total += c.node_total || 0; a.node_ready += c.node_ready || 0
      a.workloads += c.workloads || 0; a.pods += c.pods || 0
      a.namespaces += c.namespaces || 0; a.alert_pending += c.alert_pending || 0
    }
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
  const trend = (data?.alert_trend && data.alert_trend.length ? data.alert_trend : data?.trend_24h) || []
  const abnormal = (data?.abnormal_pods || []).filter((p) => sel === 'all' || p.cluster_name === shown[0]?.name).slice(0, 10)
  const faultTop = data?.fault_top || []
  const statusDist = (data?.status_dist || []) as { status?: string; count?: number }[]
  const chartTheme = mode === 'dark' ? ({ theme: 'classicDark' } as const) : {}

  const kpi = [
    { label: '集群', value: `${core?.cluster_healthy ?? 0}/${core?.cluster_total ?? 0}` },
    { label: '节点就绪', value: `${agg.node_ready}/${agg.node_total}` },
    { label: '命名空间', value: `${agg.namespaces}` },
    { label: '工作负载', value: `${agg.workloads}` },
    { label: 'Pod', value: `${agg.pods}` },
    { label: '待处理告警', value: `${core?.alert_pending ?? 0}` },
    { label: '自愈成功率', value: `${((core?.heal_rate ?? 0) * 100).toFixed(0)}%` },
    { label: 'MTTR(分)', value: `${core?.mttr_minutes ?? 0}` },
  ]

  const enterFull = () => { const el = document.documentElement; el.requestFullscreen?.() }

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
              平台 {data?.health?.api_server && data?.health?.database ? '健康' : '异常'}
            </Tag>
          </Space>
          <Space size={12} wrap>
            <span style={{ fontSize: 18, fontWeight: 600, fontVariantNumeric: 'tabular-nums', color: token.colorText }}>
              {now.toLocaleTimeString('zh-CN', { hour12: false })}
            </span>
            <Button size="small" icon={<ReloadOutlined />} loading={loading} onClick={load}>刷新</Button>
            <Button size="small" icon={<FullscreenOutlined />} onClick={enterFull}>全屏</Button>
            <Button size="small" icon={<ArrowLeftOutlined />} onClick={() => navigate(-1)}>返回</Button>
          </Space>
        </div>
      </Card>

      {/* KPI */}
      <Row gutter={[12, 12]} style={{ marginBottom: 12 }}>
        {kpi.map((k) => (
          <Col key={k.label} xs={12} sm={8} md={6} lg={3} flex="1">
            <Card size="small" variant="borderless" styles={{ body: { padding: '10px 14px' } }}>
              <Statistic title={k.label} value={k.value} valueStyle={{ fontSize: 24, fontWeight: 700 }} />
            </Card>
          </Col>
        ))}
      </Row>

      {/* 集群墙 + 告警趋势 */}
      <Row gutter={[12, 12]} style={{ marginBottom: 12 }}>
        <Col xs={24} lg={15}>
          <Card size="small" title="集群总览（节点 / 工作负载 / Pod / 水位 / 待办）" variant="borderless" style={{ height: '100%' }}>
            <Row gutter={[10, 10]}>
              {shown.map((c) => {
                const r = riskLevel(c)
                return (
                  <Col key={c.id} xs={24} sm={12} xl={8}>
                    <Card size="small" style={{ borderColor: token.colorBorderSecondary }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                        <b>{c.name}</b><Tag color={r.color}>{r.text}</Tag>
                      </div>
                      <div style={{ color: token.colorTextSecondary, fontSize: 12, marginTop: 6, lineHeight: 1.9 }}>
                        节点 {c.node_ready}/{c.node_total} · 工作负载 {c.workloads} · Pod {c.pods}<br />
                        CPU {c.cpu_pct ?? '-'}% · 内存 {c.mem_pct ?? '-'}% · 待办告警 {c.alert_pending}
                      </div>
                    </Card>
                  </Col>
                )
              })}
              {shown.length === 0 && <Col span={24}><Empty description="无集群" /></Col>}
            </Row>
          </Card>
        </Col>
        <Col xs={24} lg={9}>
          <Card size="small" title="告警趋势（24h：告警 / 已解决）" variant="borderless" style={{ height: '100%' }}>
            {trend.length > 0 ? (
              <Line height={230} data={trend.flatMap((p) => {
                const g = p as { hour?: string; time?: string; alerts: number; healed: number }
                const h = g.hour || g.time || ''
                return [{ hour: h, type: '告警', value: g.alerts }, { hour: h, type: '已解决', value: g.healed }]
              })} xField="hour" yField="value" colorField="type" smooth {...chartTheme}
                legend={{ color: { position: 'top' } }} />
            ) : <Empty description="暂无趋势数据" />}
          </Card>
        </Col>
      </Row>

      {/* 实时监控 */}
      <Row gutter={[12, 12]} style={{ marginBottom: 12 }}>
        <Col span={24}>
          <MonitorPanel clusterId={effCluster} clusterName={effClusterName} nsList={nsList} />
        </Col>
      </Row>

      {/* Pod 聚合 */}
      <Row gutter={[12, 12]} style={{ marginBottom: 12 }}>
        <Col span={24}>
          <Card size="small" title="Pod 聚合" variant="borderless">
            <Row gutter={12}>
              <Col span={8}>
                <Statistic title="Pod 总数" value={agg.pods} valueStyle={{ color: token.colorPrimary }} />
                <div style={{ marginTop: 12 }}>
                  <div style={{ color: token.colorTextSecondary, fontSize: 12, marginBottom: 4 }}>状态分布</div>
                  {statusDist.length === 0 ? <span style={{ color: token.colorTextSecondary }}>-</span> : statusDist.map((s) => (
                    <div key={s.status} style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12 }}>
                      <span>{s.status || '-'}</span><b>{s.count ?? 0}</b>
                    </div>
                  ))}
                </div>
              </Col>
              <Col span={16}>
                <Table size="small" rowKey="name" pagination={false} dataSource={shown}
                  columns={[
                    { title: '集群', dataIndex: 'name', width: 120 },
                    { title: 'Pod', dataIndex: 'pods', width: 80 },
                    { title: '工作负载', dataIndex: 'workloads', width: 100 },
                    { title: '命名空间', dataIndex: 'namespaces', width: 100 },
                  ]}
                  locale={{ emptyText: <Empty description="无数据" /> }} />
              </Col>
            </Row>
          </Card>
        </Col>
      </Row>

      {/* 异常 Pod + 故障 Top */}
      <Row gutter={[12, 12]}>
        <Col xs={24} lg={15}>
          <Card size="small" title="异常 Pod" variant="borderless">
            <Table rowKey={(r) => `${r.cluster_name}/${r.namespace}/${r.pod_name}`} size="small" dataSource={abnormal} pagination={false}
              columns={[
                { title: '集群', dataIndex: 'cluster_name', width: 120 },
                { title: '命名空间', dataIndex: 'namespace', width: 130 },
                { title: 'Pod', dataIndex: 'pod_name' },
                { title: '故障', dataIndex: 'fault_type', width: 130, render: (v) => v ? <Tag color="volcano">{v}</Tag> : '-' },
                { title: '次数', dataIndex: 'alert_count', width: 70 },
              ]}
              locale={{ emptyText: <Empty description="无异常 Pod" /> }} />
          </Card>
        </Col>
        <Col xs={24} lg={9}>
          <Card size="small" title="故障类型 Top" variant="borderless">
            {faultTop.length === 0 ? <Empty description="暂无数据" /> : (
              <div>
                {faultTop.slice(0, 10).map((f, i) => {
                  const max = Math.max(...faultTop.map((x) => x.count), 1)
                  return (
                    <div key={f.fault_type} style={{ marginBottom: 8 }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 13 }}>
                        <span>{i + 1}. {f.fault_type}</span><span style={{ color: token.colorTextSecondary }}>{f.count}</span>
                      </div>
                      <div style={{ height: 6, background: token.colorFillSecondary, borderRadius: 3, marginTop: 3 }}>
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
