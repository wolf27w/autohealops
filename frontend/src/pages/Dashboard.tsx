// 仪表盘 v4：态势脊柱 + 左右两栏（2026-11-17 按 design-taste-zh 重设计）
// 结构：顶部工具栏（筛选 + 平台健康）→ 态势脊柱（健康分拆解 + 4 KPI 一条横带）
//      → 左列（待办队列 / 告警趋势 / 容量提示）｜右列（集群水位 / 数据库健康 / AI 处置构成）
// 数据源：GET /api/v1/stats/dashboard?range|start/end&cluster_id（5s 轮询，SSE 即时失效）+ /stats/platform-health
// 注：按老板口径只按「集群」维度看，不做项目筛选；大屏 /screen 现为独立 iframe，不复用本组件。
import { ENV_OPTIONS, envLabel, envColor } from '../utils/env'
import { useEffect, useState } from 'react'
import { keepPreviousData, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Card, Row, Col, Select, Table, Tag, Space, Empty, Badge, DatePicker, Switch, theme, Tooltip,
} from 'antd'
import { useNavigate } from 'react-router-dom'
import dayjs, { type Dayjs } from 'dayjs'
import { getDashboard, getPlatformHealth } from '../services/api'
import SeverityTag from '../components/SeverityTag'
import StatusTag from '../components/StatusTag'
import { ALERT_STATUS_LABEL } from '../constants'
import type { DashboardData, HealthScore, RealtimeAlert, DbHealthItem, ClusterWaterline } from '../types'

// 全量告警处置结果（互斥且穷尽），与 stats.py ai_result_dist 口径一致
const AI_RESULT_META: Record<string, { label: string; color: string }> = {
  self_heal: { label: '自愈成功', color: '#0d7a55' },
  approval: { label: '审批后修复', color: '#2563eb' },
  manual: { label: '人工处理', color: '#b54708' },
  auto_resolved: { label: '自动恢复关闭', color: '#0e7490' },
  open: { label: '未解决', color: '#b42318' },
}
// 待办队列优先级排序
const SEVERITY_ORDER: Record<string, number> = { critical: 0, high: 1, medium: 2, low: 3 }

const scoreColor = (v: number) => (v >= 80 ? '#0d7a55' : v >= 60 ? '#b54708' : '#b42318')
const clampPct = (v: number | null | undefined) =>
  v === null || v === undefined ? undefined : Math.max(0, Math.min(100, v))
const fmtTime = (t: string | null | undefined) => (t ? String(t).slice(5, 16) : '-')

// 细进度条（4px，替代 antd Progress 的厚重感）
function ThinBar({ pct, color }: { pct?: number | null; color: string }) {
  const v = clampPct(pct)
  return (
    <span style={{ display: 'inline-block', height: 4, borderRadius: 2, background: '#e3e8e5', width: '100%', overflow: 'hidden' }}>
      <span style={{ display: 'block', height: '100%', borderRadius: 2, background: color, width: `${v ?? 0}%` }} />
    </span>
  )
}

// 平滑折线 path（坐标 0-100 百分比，viewBox 100x100 + preserveAspectRatio none，vector-effect 保线宽）
function smoothPath(vals: number[], max: number): string {
  const n = vals.length
  if (!n) return ''
  const pts: [number, number][] = vals.map((v, i) => [n === 1 ? 50 : (i / (n - 1)) * 100, 100 - (v / max) * 88 - 4])
  if (n === 1) return `M ${pts[0][0]},${pts[0][1]}`
  let d = `M ${pts[0][0]},${pts[0][1]}`
  for (let i = 1; i < n; i++) {
    const [x0, y0] = pts[i - 1], [x1, y1] = pts[i]
    d += ` C ${(x0 + x1) / 2},${y0} ${(x0 + x1) / 2},${y1} ${x1},${y1}`
  }
  return d
}

export default function Dashboard() {
  const navigate = useNavigate()
  const { token } = theme.useToken()
  const ink = token.colorText
  const muted = token.colorTextSecondary
  const faint = token.colorTextTertiary
  const linkColor = token.colorPrimary

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
    ? '实时（最近 24 小时）'
    : (customRange ? `${customRange[0].format('MM-DD HH:mm')} ~ ${customRange[1].format('MM-DD HH:mm')}` : '')
  const [clusterId, setClusterId] = useState<number | undefined>(undefined)
  const [env, setEnv] = useState<string | undefined>(undefined)

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
  const { data, isFetching } = useQuery<DashboardData>({
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
  // 告警趋势：后端 24h 是 5 分钟桶（~289 点），按小时聚合后用 Date 时间轴折线（time scale 自动抽稀刻度，标签不重叠）
  const rawTrend = data?.alert_trend ?? []
  const hourMap = new Map<string, { n: number; r: number; s: number }>()
  for (const t of rawTrend) {
    const h = String(t.bucket || '').slice(0, 13)  // "YYYY-MM-DD HH"
    const e = hourMap.get(h) ?? { n: 0, r: 0, s: 0 }
    e.n += t.new; e.r += t.resolved; e.s += t.self_healed
    hourMap.set(h, e)
  }
  const hourPoints = [...hourMap.entries()].sort((a, b) => a[0].localeCompare(b[0]))
    .map(([k, v]) => ({ hour: k.slice(11), new: v.n, resolved: v.r, selfHealed: v.s }))
  const trendMax = Math.max(1, ...hourPoints.map((p) => Math.max(p.new, p.resolved, p.selfHealed)))
  // Y 轴取整刻度（4 / 10 / 20 / 10 的倍数），保证刻度是整数且包住最大值
  const trendScaleMax = trendMax <= 4 ? 4 : trendMax <= 10 ? 10 : trendMax <= 20 ? 20 : Math.ceil(trendMax / 10) * 10
  const trendSeries = [
    { label: '新增', color: token.colorTextTertiary, d: smoothPath(hourPoints.map((p) => p.new), trendScaleMax) },
    { label: '已解决', color: token.colorPrimary, d: smoothPath(hourPoints.map((p) => p.resolved), trendScaleMax) },
    { label: '自愈', color: token.colorInfo, d: smoothPath(hourPoints.map((p) => p.selfHealed), trendScaleMax) },
  ]
  const trendLabels = (() => {
    if (!hourPoints.length) return []
    const n = hourPoints.length
    const idxs = [...new Set([0, Math.floor((n - 1) * 0.2), Math.floor((n - 1) * 0.4), Math.floor((n - 1) * 0.6), Math.floor((n - 1) * 0.8), n - 1])]
    return idxs.map((i) => hourPoints[i].hour)
  })()
  const aiDistData = (data?.ai_result_dist ?? []).map((d) => ({
    key: AI_RESULT_META[d.key]?.label ?? d.key,
    color: AI_RESULT_META[d.key]?.color ?? '#626f69',
    value: d.count ?? d.value ?? 0,
  }))
  const aiTotal = aiDistData.reduce((s, d) => s + (d.value || 0), 0)
  const aiDistPct = aiDistData.map((d) => ({
    ...d,
    pct: aiTotal ? Math.round((d.value / aiTotal) * 1000) / 10 : 0,
  }))
  const realtimeAlerts: RealtimeAlert[] = data?.realtime_alerts ?? []
  const waterline: ClusterWaterline[] = data?.cluster_waterline ?? []
  const dbHealth: DbHealthItem[] = data?.db_health ?? []

  // 待办队列：按告警级别排序（P0 优先）
  const todoAlerts = [...realtimeAlerts].sort(
    (a, b) => (SEVERITY_ORDER[a.severity] ?? 9) - (SEVERITY_ORDER[b.severity] ?? 9),
  )

  const platformOk = (ok: boolean | undefined) => (ok === undefined ? '#cbd4cf' : ok ? '#0d7a55' : '#b42318')

  // ---- 态势脊柱：健康分拆解 + 4 个 KPI ----
  const subScores = [
    { label: '集群', value: hs.cluster_score },
    { label: '告警', value: hs.alert_score },
    { label: '数据库', value: hs.db_score },
  ]
  const spineKpis = [
    { label: '未修复告警', value: kpi?.alert_pending ?? 0, foot: '待处理+待审批+已转工单（同故障已合并）', alert: (kpi?.alert_pending ?? 0) > 0 },
    { label: '待人工', value: kpi?.manual_pending ?? 0, foot: '需人工介入', alert: (kpi?.manual_pending ?? 0) > 0 },
    { label: '自动处理 (24h)', value: kpi?.auto_processed_24h ?? 0, foot: '自愈 + 自动执行', alert: false },
    { label: '待审核', value: kpi?.approval_pending ?? 0, foot: '变更审批待办', alert: (kpi?.approval_pending ?? 0) > 0 },
  ]

  // 容量提示（纯前端推导：集群水位 + DB 使用率，不依赖新后端接口）
  const capacityHints: Array<{ name: string; env?: string; text: string; level: 'high' | 'medium' }> = []
  for (const w of waterline) {
    if ((w.mem_pct ?? 0) >= 80) capacityHints.push({ name: w.cluster_name, env: w.env_tag, text: `内存水位 ${w.mem_pct}%`, level: 'high' })
    else if ((w.cpu_pct ?? 0) >= 80) capacityHints.push({ name: w.cluster_name, env: w.env_tag, text: `CPU 水位 ${w.cpu_pct}%`, level: 'high' })
    else if ((w.cpu_pct ?? 0) >= 60 || (w.mem_pct ?? 0) >= 60) capacityHints.push({ name: w.cluster_name, env: w.env_tag, text: `水位偏高（CPU ${w.cpu_pct ?? '-'}% / 内存 ${w.mem_pct ?? '-'}%）`, level: 'medium' })
  }
  for (const d of dbHealth) {
    if (d.usage_pct >= 80) capacityHints.push({ name: d.name, text: `连接使用率 ${d.usage_pct}%`, level: 'medium' })
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      {/* ===== 工具栏：筛选 + 平台健康（刷新统一在顶栏，不再重复） ===== */}
      <Card size="small" style={{ marginBottom: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 10 }}>
          <span style={{ fontSize: 16, fontWeight: 600, marginRight: 4 }}>运行态势</span>
          <Select
            allowClear placeholder="全部环境" style={{ width: 112 }}
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
            <Switch size="small" checked={live} onChange={(v: boolean) => setLive(v)} />
            <span style={{ fontSize: 13, color: muted }}>实时</span>
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
            style={{ width: 240 }}
          />
          <span style={{ fontSize: 12, color: faint }}>{rangeLabel}</span>

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
          <span style={{ fontSize: 12, color: faint, fontVariantNumeric: 'tabular-nums' }}>
            {isFetching ? '刷新中…' : '已更新'}
          </span>
        </div>
      </Card>

      {/* ===== 态势脊柱：健康分 + 4 KPI 一条横带 ===== */}
      <Card bodyStyle={{ padding: '16px 20px' }}>
        <div style={{ display: 'flex', flexWrap: 'wrap' }}>
          {/* 健康分 */}
          <div style={{ flex: '0 0 280px', minWidth: 240, paddingRight: 20, marginRight: 20, borderRight: `1px solid ${token.colorBorderSecondary}` }}>
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 6 }}>
              <span style={{ fontSize: 34, fontWeight: 600, lineHeight: 1, color: scoreColor(hs.total), fontVariantNumeric: 'tabular-nums' }}>{Math.round(hs.total)}</span>
              <span style={{ fontSize: 12, color: muted }}>/ 100 全局健康分</span>
            </div>
            <div style={{ display: 'grid', gap: 8, marginTop: 12 }}>
              {subScores.map((s) => (
                <div key={s.label} style={{ display: 'grid', gridTemplateColumns: '44px minmax(0,1fr) 28px', gap: 8, alignItems: 'center', fontSize: 12, color: muted }}>
                  <span>{s.label}</span>
                  <ThinBar pct={s.value} color={scoreColor(s.value)} />
                  <span style={{ textAlign: 'right', color: ink, fontVariantNumeric: 'tabular-nums', fontWeight: 600 }}>{Math.round(s.value)}</span>
                </div>
              ))}
            </div>
          </div>
          {/* 4 KPI */}
          {spineKpis.map((k) => (
            <div key={k.label} style={{ flex: '1 1 150px', minWidth: 130, padding: '4px 20px', borderRight: `1px solid ${token.colorBorderSecondary}` }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, color: muted }}>
                <span style={{ width: 6, height: 6, borderRadius: '50%', background: k.alert ? token.colorError : token.colorPrimary, flex: '0 0 auto' }} />
                {k.label}
              </div>
              <div style={{ fontSize: 26, fontWeight: 600, marginTop: 4, color: ink, fontVariantNumeric: 'tabular-nums' }}>{k.value}</div>
              <div style={{ fontSize: 12, color: faint, marginTop: 2 }}>{k.foot}</div>
            </div>
          ))}
        </div>
      </Card>

      {/* ===== 主两栏 ===== */}
      <Row gutter={[12, 12]} align="top">
        {/* 左列 */}
        <Col xs={24} lg={16}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            {/* 待办队列 */}
            <Card
              title="待办队列"
              size="small"
              extra={
                <Space size={16}>
                  <span style={{ fontSize: 12.5, color: linkColor, cursor: 'pointer' }} onClick={() => navigate('/alerts/tickets')}>
                    待办工单 {kpi?.manual_pending ?? 0} ›
                  </span>
                  <span style={{ fontSize: 12.5, color: linkColor, cursor: 'pointer' }} onClick={() => navigate('/alerts/handling')}>
                    待审核 {kpi?.approval_pending ?? 0} ›
                  </span>
                </Space>
              }
            >
              <Table
                rowKey="id" size="small" pagination={false}
                dataSource={todoAlerts}
                scroll={{ y: 300 }}
                locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前无待处理告警" /> }}
                onRow={() => ({ onClick: () => navigate('/alerts/handling'), style: { cursor: 'pointer' } })}
                columns={[
                  { title: '级别', dataIndex: 'severity', width: 72, render: (s: string) => <SeverityTag severity={s} /> },
                  { title: '事件', dataIndex: 'resource_name', ellipsis: true, render: (v: string, r: RealtimeAlert) => (
                    <span>{v || '-'}{r.fault_type ? <span style={{ color: faint, marginLeft: 6 }}>{r.fault_type}</span> : null}</span>
                  ) },
                  { title: '环境', dataIndex: 'env', width: 76, render: (v: string) => v ? <Tag color={envColor(v)}>{envLabel(v)}</Tag> : '-' },
                  { title: '命名空间', dataIndex: 'namespace', width: 120, ellipsis: true },
                  { title: '状态', dataIndex: 'status', width: 96, render: (s: string) => <StatusTag status={s} label={ALERT_STATUS_LABEL[s] || s} /> },
                  { title: '时间', dataIndex: 'detected_at', width: 104, render: fmtTime },
                ]}
              />
            </Card>

            {/* 告警趋势（按小时聚合，SVG 折线，轴刻度可控不重叠） */}
            <Card
              title="告警趋势（最近 24 小时）"
              size="small"
              extra={
                <span style={{ fontSize: 12, color: faint, display: 'inline-flex', gap: 12 }}>
                  {trendSeries.map((s) => (
                    <span key={s.label}>
                      <i style={{ display: 'inline-block', width: 12, height: 3, borderRadius: 2, background: s.color, marginRight: 4, verticalAlign: 'middle' }} />
                      {s.label}
                    </span>
                  ))}
                </span>
              }
            >
              {hourPoints.length ? (
                <div>
                  <div style={{ display: 'flex', gap: 6 }}>
                    {/* Y 轴数值刻度（与 SVG 的 0~100 映射对齐：max→8%、半值→52%、0→96%） */}
                    <div style={{ width: 26, height: 160, position: 'relative', flex: 'none', fontSize: 11, color: faint, fontVariantNumeric: 'tabular-nums' }}>
                      <span style={{ position: 'absolute', top: '8%', right: 2, transform: 'translateY(-50%)' }}>{trendScaleMax}</span>
                      <span style={{ position: 'absolute', top: '52%', right: 2, transform: 'translateY(-50%)' }}>{Math.round(trendScaleMax / 2)}</span>
                      <span style={{ position: 'absolute', top: '96%', right: 2, transform: 'translateY(-50%)' }}>0</span>
                    </div>
                    {/* 折线区（含网格线 + 逐小时悬停） */}
                    <div style={{ flex: 1, minWidth: 0, position: 'relative', height: 160 }}>
                      <svg viewBox="0 0 100 100" preserveAspectRatio="none" width="100%" height={160} style={{ display: 'block' }}>
                        {[8, 52, 96].map((y) => (
                          <line key={y} x1="0" y1={y} x2="100" y2={y} stroke={token.colorBorderSecondary} strokeWidth={1} strokeDasharray="3 3" vectorEffect="non-scaling-stroke" />
                        ))}
                        {trendSeries.map((s) => (
                          <path key={s.label} d={s.d} fill="none" stroke={s.color} strokeWidth={1.6} vectorEffect="non-scaling-stroke" strokeLinecap="round" strokeLinejoin="round" />
                        ))}
                      </svg>
                      {/* 悬停层：每个小时一列，显示该小时三项数量 */}
                      <div style={{ position: 'absolute', inset: 0, display: 'flex' }}>
                        {hourPoints.map((p) => (
                          <Tooltip
                            key={p.hour}
                            title={`${p.hour}:00 · 新增 ${p.new} · 已解决 ${p.resolved} · 自愈 ${p.selfHealed}`}
                          >
                            <div style={{ flex: 1, height: '100%' }} />
                          </Tooltip>
                        ))}
                      </div>
                    </div>
                  </div>
                  <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 6, marginLeft: 32, fontSize: 11, color: faint, fontVariantNumeric: 'tabular-nums' }}>
                    {trendLabels.map((h) => <span key={h}>{h}:00</span>)}
                  </div>
                </div>
              ) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无数据" />}
            </Card>

            {/* 容量提示 */}
            <Card title="容量提示" size="small" extra={<span style={{ fontSize: 12, color: faint }}>按当前水位实时推导</span>}>
              {capacityHints.length ? (
                <div>
                  {capacityHints.map((h, i) => (
                    <div key={`${h.name}-${i}`} style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '9px 0', borderBottom: i < capacityHints.length - 1 ? `1px solid ${token.colorBorderSecondary}` : 'none' }}>
                      <span style={{ fontWeight: 500, fontSize: 13, color: ink, display: 'flex', alignItems: 'center', gap: 6 }}>
                        {h.name}
                        {h.env ? <Tag color={envColor(h.env)} style={{ marginLeft: 4 }}>{envLabel(h.env)}</Tag> : null}
                      </span>
                      <span style={{ flex: 1, fontSize: 12, color: muted }}>{h.text}</span>
                      <Tag color={h.level === 'high' ? 'red' : 'orange'}>{h.level === 'high' ? '建议扩容' : '关注'}</Tag>
                    </div>
                  ))}
                </div>
              ) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前无容量风险" />}
            </Card>
          </div>
        </Col>

        {/* 右列 */}
        <Col xs={24} lg={8}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            {/* 集群水位 */}
            <Card title="集群资源水位" size="small" extra={<span style={{ fontSize: 12, color: linkColor, cursor: 'pointer' }} onClick={() => navigate('/clusters')}>集群详情 ›</span>}>
              <Table
                rowKey="cluster_id" size="small" pagination={false}
                dataSource={waterline}
                locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无集群数据" /> }}
                onRow={(r) => ({ onClick: () => navigate(`/clusters/${r.cluster_id}`), style: { cursor: 'pointer' } })}
                columns={[
                  { title: '集群', dataIndex: 'cluster_name', ellipsis: true, render: (v: string, r: ClusterWaterline) => (
                    <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                      {r.env_tag ? <Tag color={envColor(r.env_tag)}>{envLabel(r.env_tag)}</Tag> : null}
                      <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{v}</span>
                    </span>
                  ) },
                  { title: 'CPU', dataIndex: 'cpu_pct', width: 90, render: (v: number | null) => v === null || v === undefined ? '-' : (
                    <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                      <span style={{ flex: 1 }}><ThinBar pct={v} color={v >= 80 ? token.colorError : v >= 60 ? token.colorWarning : token.colorSuccess} /></span>
                      <span style={{ fontSize: 12, color: ink, fontVariantNumeric: 'tabular-nums' }}>{v}%</span>
                    </span>
                  ) },
                  { title: '内存', dataIndex: 'mem_pct', width: 90, render: (v: number | null) => v === null || v === undefined ? '-' : (
                    <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                      <span style={{ flex: 1 }}><ThinBar pct={v} color={v >= 80 ? token.colorError : v >= 60 ? token.colorWarning : token.colorSuccess} /></span>
                      <span style={{ fontSize: 12, color: ink, fontVariantNumeric: 'tabular-nums' }}>{v}%</span>
                    </span>
                  ) },
                  { title: '待处理', dataIndex: 'alert_pending', width: 68, align: 'right' as const, render: (v: number) => <b style={{ color: v > 0 ? token.colorError : token.colorSuccess, fontVariantNumeric: 'tabular-nums' }}>{v}</b> },
                ]}
              />
            </Card>

            {/* 数据库健康 */}
            <Card title="数据库健康" size="small">
              <Table
                rowKey="id" size="small" pagination={false}
                dataSource={dbHealth}
                locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无数据库连接" /> }}
                columns={[
                  { title: '名称', dataIndex: 'name', ellipsis: true, render: (v: string, r: DbHealthItem) => (
                    <span>{v}<span style={{ color: faint, marginLeft: 6, fontSize: 12 }}>{r.db_type}</span></span>
                  ) },
                  { title: '状态', dataIndex: 'status', width: 60, render: (s: string) => <Tag color={s === 'failed' ? 'red' : 'green'}>{s === 'failed' ? '异常' : '正常'}</Tag> },
                  { title: '连接', width: 96, render: (_: unknown, r: DbHealthItem) => (
                    <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                      <span style={{ flex: 1 }}><ThinBar pct={r.usage_pct} color={r.usage_pct >= 80 ? token.colorError : r.usage_pct >= 60 ? token.colorWarning : token.colorSuccess} /></span>
                      <span style={{ fontSize: 12, color: ink, fontVariantNumeric: 'tabular-nums' }}>{r.conn_used}/{r.conn_max}</span>
                    </span>
                  ) },
                ]}
              />
            </Card>

            {/* AI 处置构成 */}
            <Card title="AI 诊断处置（最近 24h）" size="small">
              {aiTotal > 0 ? (
                <div style={{ display: 'grid', gap: 12 }}>
                  <div style={{ display: 'flex', height: 8, borderRadius: 2, overflow: 'hidden', background: token.colorBorderSecondary }}>
                    {aiDistPct.filter((d) => (d.value || 0) > 0).map((d) => (
                      <span key={d.key} style={{ width: `${d.pct}%`, background: d.color }} />
                    ))}
                  </div>
                  <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: '8px 16px' }}>
                    {aiDistPct.map((d) => (
                      <div key={d.key} style={{ display: 'flex', alignItems: 'center', fontSize: 13 }}>
                        <span style={{ width: 8, height: 8, borderRadius: 2, background: d.color, marginRight: 6, flex: '0 0 auto', display: 'inline-block' }} />
                        <span style={{ flex: 1, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', color: muted }}>{d.key}</span>
                        <span style={{ marginLeft: 6, fontWeight: 600, fontVariantNumeric: 'tabular-nums' }}>{d.pct}%</span>
                      </div>
                    ))}
                  </div>
                </div>
              ) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无数据" />}
            </Card>
          </div>
        </Col>
      </Row>
    </div>
  )
}
