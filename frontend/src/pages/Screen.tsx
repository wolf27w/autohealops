// 运行大屏（/dashboard 内嵌，实时数据）· 深色 · 单屏 · 15s 轮询
import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Statistic, Table, Tag, Empty, Badge, Typography, Space, ConfigProvider, theme } from 'antd'
import { useNavigate } from 'react-router-dom'
import { Line } from '@ant-design/charts'
import { getDashboard } from '../services/api'
import type { DashboardData } from '../types'

const { Text } = Typography

const BG = '#0b1526'
const PANEL = '#111c30'
const BORDER = '1px solid rgba(90,140,220,0.25)'

const panelStyle: React.CSSProperties = { background: PANEL, border: BORDER, borderRadius: 8 }
const titleStyle: React.CSSProperties = { color: '#8fb6ff', fontWeight: 600, fontSize: 13 }

function Panel({ title, children, style }: { title: string; children: React.ReactNode; style?: React.CSSProperties }) {
  return (
    <div style={{ ...panelStyle, padding: 10, display: 'flex', flexDirection: 'column', minHeight: 0, ...style }}>
      <div style={{ ...titleStyle, marginBottom: 6 }}>{title}</div>
      <div style={{ flex: 1, minHeight: 0, overflow: 'hidden' }}>{children}</div>
    </div>
  )
}

const pct = (v: number) => `${((v || 0) * 100).toFixed(0)}%`
const SEV_COLOR: Record<string, string> = { critical: '#ff4d4f', high: '#ff7a45', medium: '#faad14', low: '#8c8c8c' }
const STATUS_LABEL: Record<string, string> = {
  pending: '待处理', self_healed: '已自愈', resolved: '已解决', awaiting_approval: '审批中',
}

interface TrendPoint {
  hour: string
  type: string
  value: number
}

interface FlowItem {
  id: number
  kind?: string
  status?: string
  flow_name?: string
  created_at?: string
}

export default function Screen({ embedded = false }: { embedded?: boolean }) {
  // react-query：大屏数据（15s 轮询 + 缓存）
  const { data } = useQuery({
    queryKey: ['dashboard'],
    queryFn: () => getDashboard(),
    refetchInterval: 15000,
  })
  const [now, setNow] = useState(new Date())
  const navigate = useNavigate()

  useEffect(() => {
    const t3 = setInterval(() => setNow(new Date()), 1000)
    return () => clearInterval(t3)
  }, [])

  const core = (data?.core || {}) as DashboardData['core']
  const trend: TrendPoint[] = []
  ;(data?.trend_24h || []).forEach((t) => {
    trend.push({ hour: t.hour, type: '新增', value: t.alerts })
    trend.push({ hour: t.hour, type: '已解决', value: t.healed })
  })
  const clusters = data?.cluster_details || []
  // 实时报警仅展示未处理（排除已自愈/已解决）
  const activeAlerts = (data?.recent_alerts || []).filter((a) => !['resolved', 'self_healed'].includes((a as { status: string }).status))
  const maxPending = Math.max(1, ...clusters.map((c) => c.alert_pending || 0))
  const healthDot = (ok: boolean) => (ok ? '#52c41a' : '#ff4d4f')
  const fmtPct = (v: number | null | undefined) => (v === null || v === undefined ? '-' : `${v}%`)
  const heatColor = (v: number | null | undefined) => (v === null || v === undefined ? '#8c8c8c' : v >= 80 ? '#ff4d4f' : v >= 60 ? '#faad14' : '#52c41a')
  // 采集新鲜度：快照超过 10 分钟视为陈旧
  const staleMinutes = (ts: string) => {
    if (!ts) return null
    const t = new Date(String(ts).replace(' ', 'T')).getTime()
    if (Number.isNaN(t)) return null
    return Math.floor((Date.now() - t) / 60000)
  }

  // 第 1 行：整体健康大 KPI
  const kpis = [
    { title: '集群健康', value: `${core.cluster_healthy || 0}/${core.cluster_total || 0}`, color: (core.cluster_healthy || 0) >= (core.cluster_total || 0) ? '#52c41a' : '#ff4d4f' },
    { title: '待处理告警', value: core.alert_pending || 0, color: (core.alert_pending || 0) > 0 ? '#ff4d4f' : '#52c41a' },
    { title: '审批中', value: core.approval_pending || 0, color: '#faad14' },
    { title: '需人工协助', value: core.manual_pending || 0, color: '#ff7875' },
    { title: '今日已处理', value: core.today_resolved || 0, color: '#40a9ff' },
    { title: 'AI 自愈成功率', value: pct(core.heal_rate), color: '#36cfc9' },
    { title: '修复成功率', value: pct(core.plan_success_rate), color: '#13c2c2' },
    { title: 'MTTR（分钟）', value: core.mttr_minutes ?? 0, color: '#b37feb' },
  ]

  return (
    <ConfigProvider
      theme={{
        algorithm: theme.darkAlgorithm,
        token: { colorBgContainer: PANEL, colorText: '#d6e2f5', colorBorderSecondary: 'rgba(90,140,220,0.2)' },
        components: {
          Table: {
            colorBgContainer: PANEL, headerBg: '#16233c', headerColor: '#9db8e0',
            borderColor: 'rgba(90,140,220,0.2)', rowHoverBg: '#16233c', colorText: '#d6e2f5',
          },
        },
      }}
    >
      <div style={embedded
        ? { height: '100%', background: BG, color: '#d6e2f5', padding: 12, display: 'flex', flexDirection: 'column', gap: 10, overflow: 'hidden', borderRadius: 8 }
        : { position: 'fixed', inset: 0, background: BG, color: '#d6e2f5', padding: 12, display: 'flex', flexDirection: 'column', gap: 10, overflow: 'hidden' }}>
        {/* 顶栏 */}
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flex: '0 0 auto' }}>
          <div style={{ fontSize: 20, fontWeight: 700, letterSpacing: 2, color: '#cfe1ff' }}>API-OPS 自愈平台 · 运行大屏</div>
          <Space size={14}>
            <Space size={6}><span style={{ color: '#7f9dc7', fontSize: 12 }}>平台</span>
              <Badge color={healthDot(!!data?.health?.api_server)} /><span style={{ color: '#7f9dc7', fontSize: 12 }}>DB</span>
              <Badge color={healthDot(!!data?.health?.database)} /><span style={{ color: '#7f9dc7', fontSize: 12 }}>Redis</span>
              <Badge color={healthDot(!!data?.health?.redis)} />
            </Space>
            <Text style={{ color: '#8fb6ff' }}>{now.toLocaleString('zh-CN', { hour12: false })}</Text>
            {!embedded && <Tag color="blue" style={{ cursor: 'pointer' }} onClick={() => navigate('/dashboard')}>返回</Tag>}
          </Space>
        </div>

        {/* 第 1 行 · 整体健康 */}
        <div style={{ flex: '0 0 auto', display: 'grid', gridTemplateColumns: 'repeat(8, 1fr)', gap: 8 }}>
          {kpis.map((k) => (
            <div key={k.title} style={{ ...panelStyle, padding: '10px 12px' }}>
              <Statistic title={<span style={{ color: '#8fb6ff', fontSize: 12 }}>{k.title}</span>} value={k.value} valueStyle={{ color: k.color, fontSize: 26, fontWeight: 700 }} />
            </div>
          ))}
        </div>

        {/* 第 2 行 · 集群 + 状态 + 异常对比 */}
        <div style={{ flex: '1 1 38%', minHeight: 0, display: 'flex', gap: 10 }}>
          <Panel title="集群概览（一屏扫全部）" style={{ flex: '0 0 60%' }}>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, height: '100%', overflow: 'auto', alignContent: 'flex-start' }}>
              {clusters.length ? clusters.map((c) => (
                <div key={c.id} style={{ flex: '1 1 230px', minWidth: 210, background: PANEL, border: BORDER, borderRadius: 8, padding: 10 }}>
                  <Space size={8} style={{ marginBottom: 6 }}>
                    <Badge color={healthDot(c.status === 'healthy')} />
                    <span style={{ fontSize: 15, fontWeight: 600 }}>{c.name}</span>
                    <Tag color={c.rbac_mode === 'rbac' ? 'green' : 'default'} style={{ marginInlineEnd: 0 }}>{c.rbac_mode || 'off'}</Tag>
                    {(() => {
                      const mins = staleMinutes(c.last_snapshot)
                      if (mins === null || mins < 10) return null
                      return <Tag color="red" title={`最近采集：${c.last_snapshot}`}>数据陈旧 {mins} 分钟</Tag>
                    })()}
                    <span style={{ color: '#7f9dc7', fontSize: 12 }}>{c.version || '-'}</span>
                  </Space>
                  <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '4px 8px', fontSize: 12 }}>
                    <div><span style={{ color: '#7f9dc7' }}>待处理 </span><b style={{ color: (c.alert_pending || 0) > 0 ? '#ff4d4f' : '#52c41a' }}>{c.alert_pending || 0}</b></div>
                    <div><span style={{ color: '#7f9dc7' }}>已自愈 </span><b style={{ color: '#52c41a' }}>{c.alert_self_healed || 0}</b></div>
                    <div><span style={{ color: '#7f9dc7' }}>已解决 </span><b style={{ color: '#40a9ff' }}>{c.alert_resolved || 0}</b></div>
                    <div><span style={{ color: '#7f9dc7' }}>审批中 </span><b style={{ color: '#faad14' }}>{c.alert_awaiting || 0}</b></div>
                    <div><span style={{ color: '#7f9dc7' }}>Pod </span><b>{c.pods || 0}</b></div>
                    <div><span style={{ color: '#7f9dc7' }}>负载 </span><b>{c.workloads || 0}</b></div>
                  </div>
                  <div style={{ display: 'flex', gap: 12, fontSize: 12, marginTop: 6, flexWrap: 'wrap' }}>
                    <span><span style={{ color: '#7f9dc7' }}>节点 </span><b style={{ color: (c.node_ready ?? 0) >= (c.node_total || 0) ? '#52c41a' : '#ff4d4f' }}>{c.node_ready ?? 0}/{c.node_total ?? 0}</b></span>
                    <span><span style={{ color: '#7f9dc7' }}>CPU </span><b style={{ color: heatColor(c.cpu_pct) }}>{fmtPct(c.cpu_pct)}</b></span>
                    <span><span style={{ color: '#7f9dc7' }}>内存 </span><b style={{ color: heatColor(c.mem_pct) }}>{fmtPct(c.mem_pct)}</b></span>
                  </div>
                  <div style={{ color: '#5c7ba8', fontSize: 11, marginTop: 4 }}>NS {c.namespaces || 0} · 采集 {c.last_snapshot ? String(c.last_snapshot).slice(5, 19) : '-'}</div>
                  <div style={{ fontSize: 11, marginTop: 4, color: '#9db8e0' }}>
                    <span style={{ color: '#7f9dc7' }}>Top 异常：</span>
                    {(c.fault_top || []).length
                      ? c.fault_top.map((f, i) => (<span key={f.fault_type}>{i > 0 ? ' · ' : ''}{f.fault_type} <b style={{ color: '#ff9c6e' }}>{f.count}</b></span>))
                      : <span style={{ color: '#5c7ba8' }}>无</span>}
                  </div>
                </div>
              )) : <Empty description="暂无集群" />}
            </div>
          </Panel>
          <Panel title="集群异常对比" style={{ flex: '1 1 40%' }}>
            {clusters.length ? (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 10, height: '100%', overflow: 'auto' }}>
                {clusters.map((c) => {
                  const top = (c.fault_top || [])[0]
                  const p = c.alert_pending || 0
                  return (
                    <div key={c.id}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', fontSize: 12, marginBottom: 3 }}>
                        <Space size={6}><Badge color={healthDot(c.status === 'healthy')} /><span style={{ fontSize: 13 }}>{c.name}</span></Space>
                        <span style={{ color: '#7f9dc7' }}>{top ? `${top.fault_type} ×${top.count}` : '无异常'}</span>
                        <b style={{ color: p > 0 ? '#ff4d4f' : '#52c41a' }}>待处理 {p}</b>
                      </div>
                      <div style={{ height: 8, background: 'rgba(90,140,220,0.15)', borderRadius: 4 }}>
                        <div style={{ width: `${(p / maxPending) * 100}%`, height: '100%', background: p > 0 ? '#ff4d4f' : '#52c41a', borderRadius: 4, minWidth: p > 0 ? 4 : 0 }} />
                      </div>
                    </div>
                  )
                })}
              </div>
            ) : <Empty description="暂无数据" />}
          </Panel>
        </div>

        {/* 第 3 行 · 趋势 + 实时报警 + 流水 */}
        <div style={{ flex: '1 1 44%', minHeight: 0, display: 'flex', gap: 10 }}>
          <Panel title="告警趋势（近 24h：新增 / 已解决）" style={{ flex: '0 0 34%' }}>
            {trend.length ? <Line data={trend} xField="hour" yField="value" colorField="type" theme="classicDark" height={210} smooth legend={{ color: { position: 'top' } }} scale={{ color: { range: ['#ff7875', '#52c41a'] } }} /> : <Empty description="暂无数据" />}
          </Panel>
          <Panel title="实时报警（未处理）" style={{ flex: '0 0 40%' }}>
            <Table
              rowKey="id" size="small" pagination={false}
              dataSource={activeAlerts}
              scroll={{ y: 210 }}
              locale={{ emptyText: <Empty description="当前无未处理告警" /> }}
              columns={[
                { title: '集群', dataIndex: 'cluster_name', width: 96, ellipsis: true },
                { title: '资源', dataIndex: 'resource_name', ellipsis: true },
                { title: '命名空间', dataIndex: 'namespace', width: 92, ellipsis: true },
                { title: '故障', dataIndex: 'fault_type', width: 136, ellipsis: true },
                { title: '级别', dataIndex: 'severity', width: 70, render: (s) => <span style={{ color: SEV_COLOR[s] || '#d6e2f5' }}>{s}</span> },
                { title: '状态', dataIndex: 'status', width: 86, render: (s) => STATUS_LABEL[s] || s },
                { title: '时间', dataIndex: 'detected_at', width: 124, render: (v) => (v ? String(v).slice(5, 19) : '-') },
              ]}
            />
          </Panel>
          <Panel title="自愈 / 审批流水" style={{ flex: '1 1 26%' }}>
            {(data && data.flow?.length) ? (data.flow as unknown as FlowItem[]).map((f) => (
              <div key={f.id} style={{ padding: '4px 0', borderBottom: '1px solid rgba(90,140,220,0.12)', fontSize: 13 }}>
                <Space size={6}>
                  <Tag color={f.kind === 'manual_assist' ? 'orange' : 'blue'} style={{ marginInlineEnd: 0 }}>{f.kind === 'manual_assist' ? '人工协助' : '风险审批'}</Tag>
                  <span>#{f.id}</span>
                  <span style={{ color: f.status === 'approved' ? '#52c41a' : f.status === 'rejected' ? '#ff4d4f' : f.status === 'timeout' ? '#8c8c8c' : '#faad14' }}>{f.status}</span>
                </Space>
                <div style={{ color: '#7f9dc7', fontSize: 12, marginTop: 2 }}>{f.flow_name || '-'} · {String(f.created_at).slice(5, 16)}</div>
              </div>
            )) : <Empty description="暂无审批流水" />}
          </Panel>
        </div>
      </div>
    </ConfigProvider>
  )
}
