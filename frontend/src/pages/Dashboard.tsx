// 仪表盘 v3：5 段布局（决策优先 + 概览卡合并版）
// 自上而下：健康分/健康总览 → KPI 卡 → 趋势/构成 → 待办队列（待处理告警/工单）→ 集群水位/DB 健康
// 数据源：GET /api/v1/stats/dashboard?range|start/end&cluster_id（5s 轮询，SSE 即时失效）+ /stats/platform-health
// 注：按老板口径只按「集群」维度看，不做项目筛选；大屏 /screen 现为独立 iframe，不再复用本组件。
import { ENV_OPTIONS, envLabel, envColor } from '../utils/env'
import { useEffect, useState } from 'react'
import { keepPreviousData, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Card, Row, Col, Select, Button, Table, Tag, Space, Progress, Empty, Badge, DatePicker, Switch, theme,
} from 'antd'
import { ReloadOutlined, FullscreenOutlined, FullscreenExitOutlined } from '@ant-design/icons'
import { Line, Pie } from '@ant-design/charts'
import { useNavigate } from 'react-router-dom'
import dayjs, { type Dayjs } from 'dayjs'
import { getDashboard, getPlatformHealth } from '../services/api'
import { useThemeMode } from '../utils/theme'
import SeverityTag from '../components/SeverityTag'
import StatusTag from '../components/StatusTag'
import { ALERT_STATUS_LABEL } from '../constants'
import type { DashboardData, HealthScore, RealtimeAlert, DbHealthItem, ClusterWaterline } from '../types'

// 全量告警处置结果（互斥且穷尽），与 stats.py ai_result_dist 口径一致
const AI_RESULT_META: Record<string, { label: string; color: string }> = {
  self_heal: { label: '自愈成功', color: '#52c41a' },
  approval: { label: '审批后修复', color: '#1677ff' },
  manual: { label: '人工处理', color: '#faad14' },
  auto_resolved: { label: '自动恢复关闭', color: '#13c2c2' },
  open: { label: '未解决', color: '#ff4d4f' },
}
// 待办队列优先级排序
const SEVERITY_ORDER: Record<string, number> = { critical: 0, high: 1, medium: 2, low: 3 }

const scoreColor = (v: number) => (v >= 80 ? '#52c41a' : v >= 60 ? '#faad14' : '#ff4d4f')
const clampPct = (v: number | null | undefined) =>
  v === null || v === undefined ? undefined : Math.max(0, Math.min(100, v))
const fmtTime = (t: string | null | undefined) => (t ? String(t).slice(5, 16) : '-')

export default function Dashboard() {
  const navigate = useNavigate()
  const { token } = theme.useToken()
  const { mode } = useThemeMode()
  const isDark = mode === 'dark'
  const ink = token.colorText
  const muted = token.colorTextSecondary
  const linkColor = token.colorPrimary
  const chartTheme = isDark ? ({ theme: 'classicDark' } as const) : {}

  // 时间范围：默认【实时】（跟随最新，滚动窗口 24h），也可自定义起止时间段
  const [live, setLive] = useState(true)
  const [customRange, setCustomRange] = useState<[Dayjs, Dayjs] | null>(null)
  const LIVE_RANGE = '24h'
  const AI_RANGE = '24h'
  const presetValue = (key: string): [Dayjs, Dayjs] => {
    const span: Record<string, [number, string]> = { '10m': [10, 'minute'], '3h': [3, 'hour'], '8h': [8, 'hour'], '24h': [24, 'hour'] }
    const [n, unit] = span[key] || span['10m']
    return [dayjs().subtract(n, unit as dayjs.ManipulateType), dayjs()]
  }
  const applyPick = (v: [Dayjs | null, Dayjs | null] | null) => {
    if (!v || !v[0] || !v[1]) return
    setCustomRange([v[0], v[1]])
    setLive(false) // 选了具体时间段 → 退出实时模式
  }
  const rangeLabel = live
    ? '实时（最近 24 小时，自动刷新）'
    : (customRange ? `${customRange[0].format('MM-DD HH:mm')} ~ ${customRange[1].format('MM-DD HH:mm')}（自定义）` : '')
  const [clusterId, setClusterId] = useState<number | undefined>(undefined)
  const [env, setEnv] = useState<string | undefined>(undefined)
  const [fullscreen, setFullscreen] = useState(false)

  useEffect(() => {
    const onFs = () => setFullscreen(!!document.fullscreenElement)
    document.addEventListener('fullscreenchange', onFs)
    return () => document.removeEventListener('fullscreenchange', onFs)
  }, [])
  const toggleFullscreen = () => {
    if (document.fullscreenElement) document.exitFullscreen()
    else document.documentElement.requestFullscreen?.()
  }

  // 实时推送（SSE）：后端缓存代次变化（告警/工单/审批/任务变更）→ 立即刷新，5s 轮询作兜底
  const qc = useQueryClient()
  useEffect(() => {
    const t = localStorage.getItem('token')
    if (!t) return
    const es = new EventSource(`/api/v1/stats/dashboard/stream?token=${encodeURIComponent(t)}`)
    es.addEventListener('dashboard', () => {
      qc.invalidateQueries({ queryKey: ['dashboard-v3'] })
      qc.invalidateQueries({ queryKey: ['platform-health'] })
    })
    return () => es.close()
  }, [qc])

  // 平台健康（右上角小标，独立接口）
  const { data: platformHealth } = useQuery({
    queryKey: ['platform-health'],
    queryFn: () => getPlatformHealth(),
    refetchInterval: 5000,
  })

  // 仪表盘主数据（5s 轮询；placeholderData: keepPreviousData 切筛选时先展示上一次数据，不闪空白）
  const { data, isFetching, refetch } = useQuery<DashboardData>({
    queryKey: ['dashboard-v3', live, customRange?.[0]?.valueOf(), customRange?.[1]?.valueOf(), clusterId, env],
    queryFn: () => (!live && customRange
      ? getDashboard({
          start: customRange[0].format('YYYY-MM-DD HH:mm:ss'),
          end: customRange[1].format('YYYY-MM-DD HH:mm:ss'),
          cluster_id: clusterId, env_tag: env, ai_range: AI_RANGE,
        })
      : getDashboard({ range: LIVE_RANGE, cluster_id: clusterId, env_tag: env, ai_range: AI_RANGE })),
    placeholderData: keepPreviousData,
    refetchInterval: 5000,
  })

  const hs: HealthScore = data?.health_score ?? { total: 0, cluster_score: 0, alert_score: 0, db_score: 0 }
  const kpi = data?.kpi
  const clusters = data?.clusters ?? []

  // ---- 派生数据 ----
  const trendData = (data?.alert_trend ?? []).flatMap((t) => {
    const label = String(t.bucket || '')
    const short = label.length > 11 ? label.slice(5) : label
    return [
      { bucket: short, type: '新增', value: t.new },
      { bucket: short, type: '已解决', value: t.resolved },
      { bucket: short, type: '自愈', value: t.self_healed },
    ]
  })
  const aiDistData = (data?.ai_result_dist ?? []).map((d) => ({
    key: AI_RESULT_META[d.key]?.label ?? d.key,
    color: AI_RESULT_META[d.key]?.color ?? '#8c8c8c',
    value: d.count ?? d.value ?? 0,
  }))
  const aiTotal = aiDistData.reduce((s, d) => s + (d.value || 0), 0)
  const aiDistPct = aiDistData.map((d) => ({
    ...d,
    pct: aiTotal ? Math.round((d.value / aiTotal) * 1000) / 10 : 0,
  }))
  const aiDistDraw = aiDistPct.filter((d) => (d.value || 0) > 0)
  const realtimeAlerts: RealtimeAlert[] = data?.realtime_alerts ?? []
  const waterline: ClusterWaterline[] = data?.cluster_waterline ?? []
  const dbHealth: DbHealthItem[] = data?.db_health ?? []

  // 待办队列：按告警级别排序（P0 优先）
  const todoAlerts = [...realtimeAlerts].sort(
    (a, b) => (SEVERITY_ORDER[a.severity] ?? 9) - (SEVERITY_ORDER[b.severity] ?? 9),
  )

  const platformOk = (ok: boolean | undefined) => (ok === undefined ? '#d9d9d9' : ok ? '#52c41a' : '#ff4d4f')

  // ---- 第 1 段：健康分/健康总览 ----
  const subScores = [
    { label: '集群健康', value: hs.cluster_score, weight: '权重 40%' },
    { label: '告警健康', value: hs.alert_score, weight: '权重 30%' },
    { label: '数据库健康', value: hs.db_score, weight: '权重 30%' },
  ]

  // ---- 第 2 段：KPI 卡（5 项） ----
  const kpiCards = [
    { title: '未处理告警', value: kpi?.alert_pending ?? 0, color: (kpi?.alert_pending ?? 0) > 0 ? '#ff4d4f' : '#1677ff', desc: '待响应告警' },
    { title: '处理中', value: kpi?.plan_running ?? 0, color: (kpi?.plan_running ?? 0) > 0 ? '#faad14' : '#1677ff', desc: '自愈计划执行中' },
    { title: '自动处理（24h）', value: kpi?.auto_processed_24h ?? 0, color: '#13c2c2', desc: '自愈 + 自动执行' },
    { title: '待人工', value: kpi?.manual_pending ?? 0, color: (kpi?.manual_pending ?? 0) > 0 ? '#faad14' : '#1677ff', desc: '需人工介入' },
    { title: '待审核', value: kpi?.approval_pending ?? 0, color: (kpi?.approval_pending ?? 0) > 0 ? '#1677ff' : '#8c8c8c', desc: '变更审批待办' },
  ]

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      {/* ===== 顶部栏：上下文筛选 + 全局健康分 + 平台健康 ===== */}
      <Card bodyStyle={{ padding: '10px 16px' }} style={{ marginBottom: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 12 }}>
          <span style={{ fontSize: 16, fontWeight: 600, marginRight: 4 }}>仪表盘</span>
          <Space size={8} wrap>
            <Select
              allowClear placeholder="全部环境" style={{ width: 120 }}
              value={env}
              onChange={(v) => setEnv(v)}
              options={ENV_OPTIONS.map((e) => ({ value: e.value, label: e.label }))}
            />
            <Select
              allowClear placeholder="全部集群" style={{ width: 160 }}
              value={clusterId}
              onChange={(v) => setClusterId(v)}
              options={clusters.map((c) => ({ value: c.id, label: c.name }))}
            />
            <Space size={6}>
              <Switch checked={live} onChange={(v: boolean) => setLive(v)} />
              <span style={{ fontSize: 13 }}>实时</span>
            </Space>
            <DatePicker.RangePicker
              showTime={{ format: 'HH:mm' }}
              format="MM-DD HH:mm"
              allowClear={false}
              disabled={live}
              value={live ? null : customRange}
              placeholder={['实时（跟随最新）', '实时（跟随最新）']}
              onChange={(v) => applyPick(v as [Dayjs | null, Dayjs | null] | null)}
              presets={[
                { label: '最近 10 分钟', value: presetValue('10m') },
                { label: '最近 3 小时', value: presetValue('3h') },
                { label: '最近 8 小时', value: presetValue('8h') },
                { label: '最近 24 小时', value: presetValue('24h') },
              ]}
              style={{ width: 250 }}
            />
            <span style={{ fontSize: 12, color: muted }}>{rangeLabel}</span>
          </Space>

          <div style={{ flex: 1 }} />

          {/* 平台健康（右上角） */}
          <Space size={6} style={{ padding: '0 8px', borderLeft: `1px solid ${token.colorBorderSecondary}` }}>
            <span style={{ fontSize: 12, color: muted }}>平台</span>
            <Badge color={platformOk(platformHealth?.api_server)} />
            <span style={{ fontSize: 12, color: muted }}>DB</span>
            <Badge color={platformOk(platformHealth?.database)} />
            <span style={{ fontSize: 12, color: muted }}>Redis</span>
            <Badge color={platformOk(platformHealth?.redis)} />
            <span style={{ fontSize: 12, color: muted }}>executor</span>
            <Badge color={platformOk(platformHealth?.executor ?? undefined)} />
          </Space>

          <Button icon={<ReloadOutlined spin={isFetching} />} onClick={() => refetch()}>刷新</Button>
          <Button
            type="text"
            icon={fullscreen ? <FullscreenExitOutlined /> : <FullscreenOutlined />}
            onClick={toggleFullscreen}
          />
        </div>
      </Card>

      {/* ===== 第 1 段 · 健康分/健康总览（决策优先） ===== */}
      <Card bodyStyle={{ padding: '16px 20px' }}>
        <Row gutter={[24, 16]} align="middle">
          <Col flex="0 0 auto">
            <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
              <Progress
                type="circle" size={96} percent={clampPct(hs.total)}
                strokeColor={scoreColor(hs.total)}
                strokeWidth={10}
                format={() => (
                  <span style={{ fontSize: 24, fontWeight: 700, color: scoreColor(hs.total), fontVariantNumeric: 'tabular-nums' }}>
                    {Math.round(hs.total)}
                  </span>
                )}
              />
              <div>
                <div style={{ fontSize: 15, fontWeight: 600, color: ink }}>全局健康分</div>
                <div style={{ fontSize: 12.5, color: muted, lineHeight: 1.7, marginTop: 4, maxWidth: 240 }}>
                  综合集群 / 告警 / 数据库三维健康，分值越高越稳定
                </div>
              </div>
            </div>
          </Col>
          <Col flex="auto">
            <Row gutter={[16, 12]}>
              {subScores.map((s) => (
                <Col key={s.label} flex="1 1 160px">
                  <div style={{ borderLeft: `3px solid ${scoreColor(s.value)}`, paddingLeft: 12 }}>
                    <div style={{ fontSize: 12.5, color: muted }}>{s.label}</div>
                    <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, marginTop: 2 }}>
                      <span style={{ fontSize: 26, fontWeight: 700, color: scoreColor(s.value), fontVariantNumeric: 'tabular-nums' }}>{Math.round(s.value)}</span>
                      <span style={{ fontSize: 12, color: muted }}>{s.weight}</span>
                    </div>
                  </div>
                </Col>
              ))}
            </Row>
          </Col>
        </Row>
      </Card>

      {/* ===== 第 2 段 · KPI 卡（5 项） ===== */}
      <Row gutter={[12, 12]} align="stretch">
        {kpiCards.map((k) => (
          <Col key={k.title} flex="1 1 170px">
            <Card bodyStyle={{ padding: '14px 16px' }} style={{ height: '100%' }}>
              <div style={{ fontSize: 13, color: muted }}>{k.title}</div>
              <div style={{ fontSize: 26, fontWeight: 700, marginTop: 4, color: k.color, fontVariantNumeric: 'tabular-nums' }}>
                {k.value}
              </div>
              <div style={{ fontSize: 12, color: muted, marginTop: 2 }}>{k.desc}</div>
            </Card>
          </Col>
        ))}
      </Row>

      {/* ===== 第 3 段 · 趋势/构成图（告警趋势 2/3 + AI 处置构成 1/3） ===== */}
      <Row gutter={[12, 12]} align="stretch">
        <Col xs={24} lg={16}>
          <Card title="告警趋势（新增 / 已解决 / 自愈）" size="small" style={{ height: '100%' }}>
            {trendData.length ? (
              <Line
                data={trendData} xField="bucket" yField="value" colorField="type"
                height={300} smooth {...chartTheme}
                legend={{ color: { position: 'top' } }}
                scale={{ color: { range: ['#1677ff', '#52c41a', '#13c2c2'] } }}
              />
            ) : <Empty description="暂无数据" />}
          </Card>
        </Col>
        <Col xs={24} lg={8}>
          <Card title="AI 诊断处置构成（最近 24 小时）" size="small" style={{ height: '100%' }}>
            {aiTotal > 0 ? (
              <>
                <div style={{ position: 'relative' }}>
                  <Pie
                    data={aiDistDraw} angleField="value" colorField="key"
                    height={250} radius={0.72} innerRadius={0.56} {...chartTheme}
                    legend={false}
                    label={false}
                    scale={{ color: { domain: aiDistDraw.map((d) => d.key), range: aiDistDraw.map((d) => d.color) } }}
                  />
                  <div style={{
                    position: 'absolute', top: '50%', left: '50%', transform: 'translate(-50%, -50%)',
                    textAlign: 'center', pointerEvents: 'none', lineHeight: 1.25,
                  }}>
                    <div style={{ fontSize: 13, color: muted }}>总计</div>
                    <div style={{ fontSize: 22, fontWeight: 700, color: ink }}>{aiTotal}</div>
                  </div>
                </div>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: '6px 16px', marginTop: 6 }}>
                  {aiDistPct.map((d) => (
                    <div key={d.key} style={{ display: 'flex', alignItems: 'center', fontSize: 13, color: ink }}>
                      <span style={{ width: 10, height: 10, borderRadius: 2, background: d.color, marginRight: 6, flex: '0 0 auto', display: 'inline-block' }} />
                      <span style={{ flex: 1, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{d.key}</span>
                      <span style={{ marginLeft: 6, fontWeight: 700, fontVariantNumeric: 'tabular-nums' }}>{d.pct}%</span>
                    </div>
                  ))}
                </div>
              </>
            ) : <Empty description="暂无数据" />}
          </Card>
        </Col>
      </Row>

      {/* ===== 第 4 段 · 待办队列（待处理告警 / 工单） ===== */}
      <Card
        title="待办队列（待处理告警 / 工单）"
        size="small"
        extra={
          <Space size={16}>
            <span style={{ fontSize: 12.5, color: linkColor, cursor: 'pointer' }} onClick={() => navigate('/tickets')}>
              待办工单 {kpi?.manual_pending ?? 0} ›
            </span>
            <span style={{ fontSize: 12.5, color: linkColor, cursor: 'pointer' }} onClick={() => navigate('/alert-handling')}>
              待审核 {kpi?.approval_pending ?? 0} ›
            </span>
          </Space>
        }
      >
        <Table
          rowKey="id" size="small" pagination={false}
          dataSource={todoAlerts}
          scroll={{ y: 260 }}
          locale={{ emptyText: <Empty description="当前无待处理告警" /> }}
          onRow={() => ({ onClick: () => navigate('/alert-handling'), style: { cursor: 'pointer' } })}
          columns={[
            { title: '优先级', dataIndex: 'severity', width: 80, render: (s: string) => <SeverityTag severity={s} /> },
            { title: '告警', dataIndex: 'resource_name', ellipsis: true, render: (v: string, r: RealtimeAlert) => (
              <span>{v || '-'}{r.fault_type ? <span style={{ color: muted, marginLeft: 6 }}>{r.fault_type}</span> : null}</span>
            ) },
            { title: '环境', dataIndex: 'env', width: 80, render: (v: string) => v ? <Tag color={envColor(v)}>{envLabel(v)}</Tag> : '-' },
            { title: '命名空间', dataIndex: 'namespace', width: 110, ellipsis: true },
            { title: '状态', dataIndex: 'status', width: 96, render: (s: string) => <StatusTag status={s} label={ALERT_STATUS_LABEL[s] || s} /> },
            { title: '时间', dataIndex: 'detected_at', width: 110, render: fmtTime },
            { title: '', width: 76, render: () => <span style={{ color: linkColor }}>去处理 ›</span> },
          ]}
        />
      </Card>

      {/* ===== 第 5 段 · 集群资源水位 / DB 健康 ===== */}
      <Row gutter={[12, 12]} align="stretch">
        <Col xs={24} lg={12}>
          <Card title="集群资源水位（点击进集群详情）" size="small" style={{ height: '100%' }}>
            <Table
              rowKey="cluster_id" size="small" pagination={false}
              dataSource={waterline}
              scroll={{ y: 260 }}
              locale={{ emptyText: <Empty description="暂无集群数据" /> }}
              onRow={(r) => ({ onClick: () => navigate(`/clusters/${r.cluster_id}`), style: { cursor: 'pointer' } })}
              columns={[
                { title: '环境', dataIndex: 'env_tag', width: 70, render: (v: string) => v ? <Tag color={envColor(v)}>{envLabel(v)}</Tag> : '-' },
                { title: '集群', dataIndex: 'cluster_name', ellipsis: true },
                { title: 'CPU 水位', dataIndex: 'cpu_pct', width: 120, render: (v: number | null) => v === null || v === undefined ? '-' : (
                  <Progress percent={clampPct(v)} size="small" strokeColor={v >= 80 ? '#ff4d4f' : v >= 60 ? '#faad14' : '#52c41a'} format={(p) => `${p ?? 0}%`} />
                ) },
                { title: '内存水位', dataIndex: 'mem_pct', width: 120, render: (v: number | null) => v === null || v === undefined ? '-' : (
                  <Progress percent={clampPct(v)} size="small" strokeColor={v >= 80 ? '#ff4d4f' : v >= 60 ? '#faad14' : '#52c41a'} format={(p) => `${p ?? 0}%`} />
                ) },
                { title: 'Pod 数', dataIndex: 'pods', width: 76 },
                { title: '待处理', dataIndex: 'alert_pending', width: 76, render: (v: number) => <b style={{ color: v > 0 ? '#ff4d4f' : '#52c41a' }}>{v}</b> },
              ]}
            />
          </Card>
        </Col>
        <Col xs={24} lg={12}>
          <Card title="数据库健康" size="small" style={{ height: '100%' }}>
            <Table
              rowKey="id" size="small" pagination={false}
              dataSource={dbHealth}
              scroll={{ y: 260 }}
              locale={{ emptyText: <Empty description="暂无数据库连接" /> }}
              columns={[
                { title: '名称', dataIndex: 'name', ellipsis: true },
                { title: '类型', dataIndex: 'db_type', width: 70 },
                { title: '状态', dataIndex: 'status', width: 70, render: (s: string) => <Tag color={s === 'failed' ? 'red' : 'green'}>{s === 'failed' ? '异常' : '正常'}</Tag> },
                { title: '连接', width: 90, render: (_: unknown, r: DbHealthItem) => `${r.conn_used}/${r.conn_max}` },
                { title: '使用率', dataIndex: 'usage_pct', width: 110, render: (v: number) => (
                  <Progress percent={clampPct(v)} size="small" strokeColor={v >= 80 ? '#ff4d4f' : v >= 60 ? '#faad14' : '#52c41a'} format={(p) => `${p ?? 0}%`} />
                ) },
                { title: 'QPS', dataIndex: 'qps', width: 70 },
                { title: 'TPS', dataIndex: 'tps', width: 70 },
              ]}
            />
          </Card>
        </Col>
      </Row>
    </div>
  )
}
