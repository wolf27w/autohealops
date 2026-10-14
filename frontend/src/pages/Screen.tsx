// 统一监控大屏（P1-R7）：多集群总览（深色、全屏、无侧栏）
// 数据源：/stats/dashboard（聚合 + 集群明细 + 趋势 + 异常 Pod + 故障 Top）
import { useEffect, useMemo, useState, type CSSProperties } from 'react'
import { Select, Tag, Space, Button, Table, Empty } from 'antd'
import { FullscreenOutlined, ReloadOutlined, ArrowLeftOutlined } from '@ant-design/icons'
import { Line } from '@ant-design/charts'
import { useNavigate } from 'react-router-dom'
import { getDashboard } from '../services/api'
import type { DashboardData, ClusterHealthDetail } from '../types'

function useClock() {
  const [now, setNow] = useState(new Date())
  useEffect(() => { const t = window.setInterval(() => setNow(new Date()), 1000); return () => window.clearInterval(t) }, [])
  return now
}

const PANEL: CSSProperties = {
  background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(120,170,255,0.18)',
  borderRadius: 10, padding: '12px 16px',
}
const LABEL: CSSProperties = { color: '#8ba3c7', fontSize: 13 }
const BIG: CSSProperties = { color: '#e6f0ff', fontSize: 30, fontWeight: 700, lineHeight: 1.1 }

function riskLevel(c: ClusterHealthDetail): { text: string; color: string } {
  if (c.status !== 'healthy') return { text: '异常', color: '#ff4d4f' }
  if ((c.cpu_pct ?? 0) >= 85 || (c.mem_pct ?? 0) >= 85) return { text: '高水位', color: '#faad14' }
  if ((c.alert_pending ?? 0) > 0) return { text: '有待办', color: '#faad14' }
  return { text: '正常', color: '#52c41a' }
}

export default function Screen() {
  const navigate = useNavigate()
  const now = useClock()
  const [data, setData] = useState<DashboardData | null>(null)
  const [sel, setSel] = useState<number | 'all'>('all')
  const [loading, setLoading] = useState(false)

  const load = () => {
    setLoading(true)
    getDashboard().then((d) => setData(d)).catch(() => {}).finally(() => setLoading(false))
  }
  useEffect(() => { load(); const t = window.setInterval(load, 15000); return () => window.clearInterval(t) }, [])

  const details: ClusterHealthDetail[] = data?.cluster_details || []
  const shown = sel === 'all' ? details : details.filter((c) => c.id === sel)
  const agg = useMemo(() => {
    const acc = { node_total: 0, node_ready: 0, workloads: 0, pods: 0, namespaces: 0, alert_pending: 0 }
    for (const c of shown) {
      acc.node_total += c.node_total || 0; acc.node_ready += c.node_ready || 0
      acc.workloads += c.workloads || 0; acc.pods += c.pods || 0
      acc.namespaces += c.namespaces || 0; acc.alert_pending += c.alert_pending || 0
    }
    return acc
  }, [shown])

  const core = data?.core
  const trend = (data?.alert_trend && data.alert_trend.length ? data.alert_trend : data?.trend_24h) || []
  const abnormal = (data?.abnormal_pods || []).filter((p) => sel === 'all' || p.cluster_name === shown[0]?.name).slice(0, 10)
  const faultTop = data?.fault_top || []

  const enterFull = () => { const el = document.documentElement; if (el.requestFullscreen) el.requestFullscreen() }

  return (
    <div style={{ minHeight: '100vh', background: '#070b16', color: '#e6f0ff', padding: 16, fontFamily: 'system-ui, sans-serif' }}>
      {/* 头部 */}
      <div style={{ ...PANEL, display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
        <Space size={16}>
          <span style={{ fontSize: 22, fontWeight: 700, letterSpacing: 2, background: 'linear-gradient(90deg,#4dabf7,#69db7c)', WebkitBackgroundClip: 'text', color: 'transparent' }}>API-OPS 运维大屏</span>
          <Select value={sel} onChange={(v) => setSel(v)} style={{ width: 160 }}
            options={[{ value: 'all', label: '全部集群' }, ...details.map((c) => ({ value: c.id, label: c.name }))]} />
          <Tag color={data?.health?.api_server && data?.health?.database ? 'green' : 'red'}>
            平台 {data?.health?.api_server && data?.health?.database ? '健康' : '异常'}
          </Tag>
        </Space>
        <Space size={12}>
          <span style={{ fontSize: 20, fontWeight: 600, fontVariantNumeric: 'tabular-nums' }}>{now.toLocaleTimeString('zh-CN', { hour12: false })}</span>
          <Button size="small" icon={<ReloadOutlined />} loading={loading} onClick={load}>刷新</Button>
          <Button size="small" icon={<FullscreenOutlined />} onClick={enterFull}>全屏</Button>
          <Button size="small" icon={<ArrowLeftOutlined />} onClick={() => navigate(-1)}>返回</Button>
        </Space>
      </div>

      {/* KPI 行 */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(8, 1fr)', gap: 10, marginBottom: 12 }}>
        {[
          ['集群', `${core?.cluster_healthy ?? 0}/${core?.cluster_total ?? 0}`],
          ['节点就绪', `${agg.node_ready}/${agg.node_total}`],
          ['命名空间', `${agg.namespaces}`],
          ['工作负载', `${agg.workloads}`],
          ['Pod', `${agg.pods}`],
          ['待处理告警', `${core?.alert_pending ?? 0}`],
          ['自愈成功率', `${((core?.heal_rate ?? 0) * 100).toFixed(0)}%`],
          ['MTTR', `${core?.mttr_minutes ?? 0} 分`],
        ].map(([label, val]) => (
          <div key={label} style={PANEL}>
            <div style={LABEL}>{label}</div>
            <div style={BIG}>{val}</div>
          </div>
        ))}
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1.6fr 1fr', gap: 12, marginBottom: 12 }}>
        {/* 集群墙 */}
        <div style={PANEL}>
          <div style={{ ...LABEL, marginBottom: 8 }}>集群总览（节点/工作负载/Pod/水位/待办）</div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(240px, 1fr))', gap: 10 }}>
            {shown.map((c) => {
              const r = riskLevel(c)
              return (
                <div key={c.id} style={{ border: `1px solid ${r.color}55`, borderRadius: 8, padding: 10, background: 'rgba(255,255,255,0.02)' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <b style={{ fontSize: 15 }}>{c.name}</b>
                    <Tag color={r.color}>{r.text}</Tag>
                  </div>
                  <div style={{ ...LABEL, marginTop: 6, lineHeight: 1.9 }}>
                    节点 {c.node_ready}/{c.node_total} · 工作负载 {c.workloads} · Pod {c.pods}<br />
                    CPU {c.cpu_pct ?? '-'}% · 内存 {c.mem_pct ?? '-'}% · 待办告警 {c.alert_pending}
                  </div>
                </div>
              )
            })}
            {shown.length === 0 && <Empty description="无集群" />}
          </div>
        </div>

        {/* 告警趋势 */}
        <div style={PANEL}>
          <div style={{ ...LABEL, marginBottom: 8 }}>告警趋势（24h：告警 / 已解决）</div>
          <div style={{ height: 220 }}>
            {trend.length > 0 ? (
              <Line data={trend.flatMap((p) => [
                { hour: (p as { hour?: string; time?: string }).hour || (p as { time?: string }).time || '', type: '告警', value: (p as { alerts: number }).alerts },
                { hour: (p as { hour?: string; time?: string }).hour || (p as { time?: string }).time || '', type: '已解决', value: (p as { healed: number }).healed },
              ])}
                xField="hour" yField="value" colorField="type" height={220} smooth theme="classicDark"
                legend={{ color: { position: 'top' } }} />
            ) : <Empty description="暂无趋势数据" />}
          </div>
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1.6fr 1fr', gap: 12 }}>
        {/* Top10 异常 Pod */}
        <div style={PANEL}>
          <div style={{ ...LABEL, marginBottom: 8 }}>异常 Pod（近条数排行）</div>
          <Table rowKey={(r) => `${r.cluster_name}/${r.namespace}/${r.pod_name}`} size="small" dataSource={abnormal} pagination={false}
            columns={[
              { title: '集群', dataIndex: 'cluster_name', width: 130 },
              { title: '命名空间', dataIndex: 'namespace', width: 130 },
              { title: 'Pod', dataIndex: 'pod_name' },
              { title: '故障', dataIndex: 'fault_type', width: 130, render: (v) => v ? <Tag color="volcano">{v}</Tag> : '-' },
              { title: '次数', dataIndex: 'alert_count', width: 70 },
            ]}
            locale={{ emptyText: <Empty description="无异常 Pod" /> }} />
        </div>

        {/* 故障类型 Top */}
        <div style={PANEL}>
          <div style={{ ...LABEL, marginBottom: 8 }}>故障类型 Top</div>
          {faultTop.length === 0 ? <Empty description="暂无数据" /> : (
            <div>
              {faultTop.slice(0, 10).map((f, i) => {
                const max = Math.max(...faultTop.map((x) => x.count), 1)
                return (
                  <div key={f.fault_type} style={{ marginBottom: 8 }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 13 }}>
                      <span>{i + 1}. {f.fault_type}</span><span style={{ color: '#8ba3c7' }}>{f.count}</span>
                    </div>
                    <div style={{ height: 6, background: 'rgba(255,255,255,0.08)', borderRadius: 3, marginTop: 3 }}>
                      <div style={{ width: `${(f.count / max) * 100}%`, height: '100%', borderRadius: 3, background: 'linear-gradient(90deg,#4dabf7,#69db7c)' }} />
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
