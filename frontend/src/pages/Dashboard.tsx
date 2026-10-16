// 仪表盘 v2：6 段布局（集群/时间筛选 + 全局健康分 + 平台健康 + KPI + 趋势/分布 + 实时告警/排行 + 异常 Pod/DB 健康 + 动作统计/时间线 + 集群水位）
// 注：按老板口径只按「集群」维度看，不做项目筛选（项目颗粒度太粗）
// 数据源：GET /api/v1/stats/dashboard?range|start/end&cluster_id（15s 轮询，后端每 10s 预热）+ /stats/platform-health
import { useEffect, useState } from 'react'
import { keepPreviousData, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Card, Row, Col, Statistic, Select, Button, Table, Tag, Space, Progress, Empty, Badge, Tooltip, DatePicker, Switch, theme,
} from 'antd'
import { ReloadOutlined, FullscreenOutlined, FullscreenExitOutlined, ThunderboltOutlined } from '@ant-design/icons'
import { Line, Pie, Bar } from '@ant-design/charts'
import { useNavigate } from 'react-router-dom'
import dayjs, { type Dayjs } from 'dayjs'
import { getDashboard, getPlatformHealth } from '../services/api'
import { useThemeMode } from '../utils/theme'
import SeverityTag from '../components/SeverityTag'
import StatusTag from '../components/StatusTag'
import { ALERT_STATUS_LABEL } from '../constants'
import type {
  DashboardData, HealthScore, RealtimeAlert, AbnormalPod, DbHealthItem,
  ActionTimelineItem, ClusterWaterline,
} from '../types'

// 告警状态/级别：走公共常量 + SeverityTag/StatusTag 组件（去重）
const AI_RESULT_META: Record<string, { label: string; color: string }> = {
  // 全量告警处置结果（互斥且穷尽），与 stats.py ai_result_dist 口径一致
  self_heal: { label: '自愈成功', color: '#52c41a' },
  approval: { label: '审批后修复', color: '#1677ff' },
  manual: { label: '人工处理', color: '#faad14' },
  auto_resolved: { label: '自动恢复关闭', color: '#13c2c2' },
  open: { label: '未解决', color: '#ff4d4f' },
}
const ACTION_META: Record<string, { label: string; color: string }> = {
  self_heal: { label: '自愈', color: 'green' },
  restart: { label: '重启', color: 'blue' },
  scale: { label: '伸缩', color: 'gold' },
  rollback: { label: '回滚', color: 'red' },
}

const scoreColor = (v: number) => (v >= 80 ? '#52c41a' : v >= 60 ? '#faad14' : '#ff4d4f')
const clampPct = (v: number | null | undefined) =>
  v === null || v === undefined ? undefined : Math.max(0, Math.min(100, v))
const fmtTime = (t: string | null | undefined) => (t ? String(t).slice(5, 16) : '-')

export default function Dashboard({ screen = false }: { screen?: boolean }) {
  const navigate = useNavigate()
  const { token } = theme.useToken()
  const { mode } = useThemeMode()
  const isDark = mode === 'dark'
  const ink = token.colorText
  const muted = token.colorTextSecondary
  const chartTheme = isDark ? ({ theme: 'classicDark' } as const) : {}
  // 时间范围：默认【实时】（跟随最新，滚动窗口），也可自定义起止时间段
  const [live, setLive] = useState(true)
  const [customRange, setCustomRange] = useState<[Dayjs, Dayjs] | null>(null)
  // 实时模式滚动窗口：普通仪表盘=最近 24 小时；大屏（/screen）总览=最近 7 天
  // （24h 内无新告警时，趋势图/AI 分布会全 0，大屏显得「没数据」，放宽到 7 天更贴合总览定位）
  const LIVE_RANGE = screen ? '7d' : '24h'
  const AI_RANGE = screen ? '7d' : '24h'
  const presetValue = (key: string): [Dayjs, Dayjs] => {
    const span: Record<string, [number, string]> = { '10m': [10, 'minute'], '3h': [3, 'hour'], '8h': [8, 'hour'], '24h': [24, 'hour'] }
    const [n, unit] = span[key] || span['10m']
    return [dayjs().subtract(n, unit as dayjs.ManipulateType), dayjs()]
  }
  const applyPick = (v: [Dayjs | null, Dayjs | null] | null) => {
    if (!v || !v[0] || !v[1]) return
    setCustomRange([v[0], v[1]])
    setLive(false)  // 选了具体时间段 → 退出实时模式
  }
  const rangeLabel = live
    ? (screen ? '实时（最近 7 天，自动刷新）' : '实时（最近 24 小时，自动刷新）')
    : (customRange ? `${customRange[0].format('MM-DD HH:mm')} ~ ${customRange[1].format('MM-DD HH:mm')}（自定义）` : '')
  const [clusterId, setClusterId] = useState<number | undefined>(undefined)
  const [env, setEnv] = useState<string | undefined>(undefined)
  const [fullscreen, setFullscreen] = useState(false)

  // 大屏实时时钟（仅 /screen 需要）
  const [clock, setClock] = useState('')
  useEffect(() => {
    if (!screen) return
    const tick = () => {
      const d = new Date()
      const p = (n: number) => String(n).padStart(2, '0')
      setClock(`${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`)
    }
    tick()
    const t = setInterval(tick, 1000)
    return () => clearInterval(t)
  }, [screen])

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
    const token = localStorage.getItem('token')
    if (!token) return
    const es = new EventSource(`/api/v1/stats/dashboard/stream?token=${encodeURIComponent(token)}`)
    es.addEventListener('dashboard', () => {
      qc.invalidateQueries({ queryKey: ['dashboard-v2'] })
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

  // 仪表盘主数据（5s 轮询：后端缓存键内嵌代次，告警/工单/审批变更即时失效 → 解决后 ≤5s 反映）
  // placeholderData: keepPreviousData → 切筛选/回页面时先展示上一次数据，不再短暂空白
  const { data, isFetching, refetch } = useQuery<DashboardData>({
    queryKey: ['dashboard-v2', live, customRange?.[0]?.valueOf(), customRange?.[1]?.valueOf(), clusterId, env],
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
    const short = label.length > 11 ? label.slice(5) : label  // 2026-09-20 18:00 → 09-20 18:00
    return [
      { bucket: short, type: '新增', value: t.new },
      { bucket: short, type: '已解决', value: t.resolved },
      { bucket: short, type: '自愈', value: t.self_healed },
    ]
  })
  const aiDistData = (data?.ai_result_dist ?? []).map((d) => ({
    rawKey: d.key,
    key: AI_RESULT_META[d.key]?.label ?? d.key,
    color: AI_RESULT_META[d.key]?.color ?? '#8c8c8c',
    value: d.count ?? d.value ?? 0,
  }))
  // 百分比展示（老板要求：AI 诊断结果分布用百分比）
  const aiTotal = aiDistData.reduce((s, d) => s + (d.value || 0), 0)
  const aiDistPct = aiDistData.map((d) => ({
    ...d,
    pct: aiTotal ? Math.round((d.value / aiTotal) * 1000) / 10 : 0,
  }))
  // 只画非零扇区：零值扇区的外侧标签会堆在同一点上重叠（图例仍展示四项含 0）
  const aiDistDraw = aiDistPct.filter((d) => (d.value || 0) > 0)
  const faultTopData = (data?.fault_top ?? []).map((f) => ({ fault_type: f.fault_type, count: f.count }))
  const realtimeAlerts: RealtimeAlert[] = data?.realtime_alerts ?? []
  const abnormalPods: AbnormalPod[] = data?.abnormal_pods ?? []
  const dbHealth: DbHealthItem[] = data?.db_health ?? []
  const actionStat = data?.action_stat ?? { self_heal: 0, restart: 0, scale: 0, rollback: 0 }
  const actionAlertCount = (data?.action_alert_count as number | undefined) ?? 0
  const actionHealedWithoutAction = (data?.action_healed_without_action as number | undefined) ?? 0
  const timeline: ActionTimelineItem[] = data?.action_timeline ?? []
  const waterline: ClusterWaterline[] = data?.cluster_waterline ?? []

  const platformOk = (ok: boolean | undefined) => (ok === undefined ? '#d9d9d9' : ok ? '#52c41a' : '#ff4d4f')

  const kpiItems = [
    { title: '集群数', value: kpi?.cluster_total ?? 0, color: '#1677ff' },
    { title: '活跃告警', value: kpi?.alert_pending ?? 0, color: (kpi?.alert_pending ?? 0) > 0 ? '#ff4d4f' : '#1677ff' },
    { title: '自动处理（24小时）', value: kpi?.auto_processed_24h ?? 0, color: '#13c2c2' },
    { title: '待人工', value: kpi?.manual_pending ?? 0, color: (kpi?.manual_pending ?? 0) > 0 ? '#faad14' : '#1677ff' },
    { title: '待审核', value: kpi?.approval_pending ?? 0, color: (kpi?.approval_pending ?? 0) > 0 ? '#1677ff' : '#8c8c8c' },
    { title: '今日已审核', value: kpi?.approval_decided_today ?? 0, color: '#52c41a' },
    { title: 'DB 异常', value: kpi?.db_abnormal ?? 0, color: (kpi?.db_abnormal ?? 0) > 0 ? '#ff4d4f' : '#1677ff' },
    { title: '成功率（24小时）', value: `${(((kpi?.heal_rate ?? 0) * 100).toFixed(1))}%`, color: '#52c41a' },
    { title: 'MTTR（分钟·24小时）', value: kpi?.mttr_minutes ?? 0, color: '#722ed1' },
  ]

  const actionItems = [
    { key: 'self_heal', value: actionStat.self_heal },
    { key: 'restart', value: actionStat.restart },
    { key: 'scale', value: actionStat.scale },
    { key: 'rollback', value: actionStat.rollback },
  ]

  return (
    <div style={{
      display: 'flex', flexDirection: 'column', gap: 12,
      // 大屏（/screen）：无侧栏/顶栏框架，自行补边距与底色，内容与仪表盘完全一致
      ...(screen ? { padding: 12, minHeight: '100vh', background: token.colorBgLayout, boxSizing: 'border-box' as const } : {}),
    }}>
      {/* ===== 第 1 段 · 顶部栏 ===== */}
      {screen ? (
        <Card bodyStyle={{ padding: '16px 24px' }} style={{ marginBottom: 0, border: 'none', boxShadow: 'none', background: 'linear-gradient(90deg, rgba(18,185,129,0.10), rgba(4,140,99,0.05) 60%, rgba(18,185,129,0.02))' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 20 }}>
            <div>
              <div style={{ fontSize: 24, fontWeight: 700, letterSpacing: 1, color: ink }}>API-OPS 智能运维大屏</div>
              <div style={{ fontSize: 13, color: muted, marginTop: 3 }}>
                {new Date().toLocaleDateString('zh-CN', { year: 'numeric', month: 'long', day: 'numeric', weekday: 'long' })} · 聚合视图 · 最近 7 天 · 5 秒自动刷新
              </div>
            </div>
            <div style={{ flex: 1 }} />
            <div style={{ textAlign: 'right' }}>
              <div style={{ fontSize: 12, color: muted }}>全局健康分</div>
              <div style={{ fontSize: 32, fontWeight: 700, lineHeight: 1.1, color: scoreColor(hs.total), fontVariantNumeric: 'tabular-nums' }}>{Math.round(hs.total)}</div>
            </div>
            <div style={{ textAlign: 'right' }}>
              <div style={{ fontSize: 12, color: muted }}>当前时间</div>
              <div style={{ fontSize: 32, fontWeight: 700, lineHeight: 1.1, color: ink, fontVariantNumeric: 'tabular-nums' }}>{clock}</div>
            </div>
            <Space size={6} style={{ padding: '0 8px', borderLeft: `1px solid ${token.colorBorderSecondary}` }}>
              <span style={{ fontSize: 12, color: muted }}>平台</span>
              <Badge color={platformOk(platformHealth?.api_server)} />
              <span style={{ fontSize: 12, color: muted }}>DB</span>
              <Badge color={platformOk(platformHealth?.database)} />
              <span style={{ fontSize: 12, color: muted }}>Redis</span>
              <Badge color={platformOk(platformHealth?.redis)} />
            </Space>
            <Button type="text" icon={fullscreen ? <FullscreenExitOutlined /> : <FullscreenOutlined />} onClick={toggleFullscreen} />
          </div>
        </Card>
      ) : (
      <Card bodyStyle={{ padding: '10px 16px' }} style={{ marginBottom: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 12 }}>
          <span style={{ fontSize: 16, fontWeight: 600, marginRight: 4 }}>仪表盘</span>
          <Space size={8} wrap>
            <Select
              allowClear placeholder="全部环境" style={{ width: 120 }}
              value={env}
              onChange={(v) => setEnv(v)}
              options={[{ value: 'qa', label: 'qa' }, { value: 'release', label: 'release' }, { value: 'prod', label: 'prod' }]}
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

          {/* 全局健康分 */}
          <Tooltip title={`集群健康 ${hs.cluster_score} · 活跃告警 ${hs.alert_score} · DB 健康 ${hs.db_score}`}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'default' }}>
              <Progress
                type="circle" size={62} percent={clampPct(hs.total)}
                strokeColor={scoreColor(hs.total)}
                format={() => (
                  <span style={{ fontSize: 16, fontWeight: 700, color: scoreColor(hs.total) }}>
                    {Math.round(hs.total)}
                  </span>
                )}
              />
              <span style={{ fontSize: 12, color: muted, lineHeight: 1.2 }}>全局<br />健康分</span>
            </div>
          </Tooltip>

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
      )}

      {/* ===== 第 2 段 · KPI 行（7 项） ===== */}
      <Row gutter={[12, 12]} align="stretch">
        {kpiItems.map((k) => (
          <Col key={k.title} flex="1 1 150px">
            <Card bodyStyle={{ padding: '12px 16px' }}>
              <Statistic
                title={k.title} value={k.value}
                valueStyle={{ color: k.color, fontSize: screen ? 30 : 24, fontWeight: 700 }}
              />
            </Card>
          </Col>
        ))}
      </Row>

      {/* ===== 第 3 段 · 告警趋势（2/3） + AI 诊断结果分布（1/3） ===== */}
      <Row gutter={[12, 12]} align="stretch">
        <Col xs={24} lg={16}>
          <Card title="告警趋势（新增 / 已解决 / 自愈）" size="small" style={{ height: '100%' }}>
            {trendData.length ? (
              <Line
                data={trendData} xField="bucket" yField="value" colorField="type"
                height={screen ? 340 : 320} smooth {...chartTheme}
                legend={{ color: { position: 'top' } }}
                scale={{ color: { range: ['#1677ff', '#52c41a', '#13c2c2'] } }}
              />
            ) : <Empty description="暂无数据" />}
          </Card>
        </Col>
        <Col xs={24} lg={8}>
          <Card title="AI 诊断结果分布（最近 24 小时）" size="small" style={{ height: '100%' }}>
            {aiTotal > 0 ? (
              <>
                <div style={{ position: 'relative' }}>
                  <Pie
                    data={aiDistDraw} angleField="value" colorField="key"
                    height={screen ? 300 : 250} radius={0.72} innerRadius={0.56} {...chartTheme}
                    legend={false}
                    label={{
                      // 环外标签用 spider 布局（带引导线自动避让），小扇区也不会叠字
                      text: (d: { key: string; pct: number }) => `${d.key} ${d.pct}%`,
                      position: 'spider',
                      style: { fontSize: 13, fontWeight: 600, fill: ink },
                    }}
                    tooltip={{ items: [{ channel: 'y', name: '告警数', valueFormatter: (v: number) => `${v} 条（${aiTotal ? Math.round((v / aiTotal) * 1000) / 10 : 0}%）` }] }}
                    scale={{ color: { domain: aiDistDraw.map((d) => d.key), range: aiDistDraw.map((d) => d.color) } }}
                  />
                  {/* 环形中心总量（HTML 叠层，字号可控） */}
                  <div style={{
                    position: 'absolute', top: '50%', left: '50%', transform: 'translate(-50%, -50%)',
                    textAlign: 'center', pointerEvents: 'none', lineHeight: 1.25,
                  }}>
                    <div style={{ fontSize: 13, color: muted }}>总计</div>
                    <div style={{ fontSize: 22, fontWeight: 700, color: ink }}>{aiTotal}</div>
                  </div>
                </div>
                {/* 分列表格图例：名称 / 百分比 / 条数（不再用环外标签，避免小扇区标签重叠） */}
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: '6px 16px', marginTop: 6 }}>
                  {aiDistPct.map((d) => (
                    <div key={d.key} style={{ display: 'flex', alignItems: 'center', fontSize: 13, color: ink }}>
                      <span style={{ width: 10, height: 10, borderRadius: 2, background: d.color, marginRight: 6, flex: '0 0 auto', display: 'inline-block' }} />
                      <span style={{ flex: 1, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{d.key}</span>
                      <span style={{ marginLeft: 6, fontWeight: 700, fontVariantNumeric: 'tabular-nums' }}>{d.pct}%</span>
                      <span style={{ marginLeft: 4, color: muted }}>（{d.value}）</span>
                    </div>
                  ))}
                </div>
              </>
            ) : <Empty description="暂无数据" />}
          </Card>
        </Col>
      </Row>

      {/* ===== 第 4 段 · 实时告警流（2/3） + Top 告警类型（1/3） ===== */}
      <Row gutter={[12, 12]} align="stretch">
        <Col xs={24} lg={16}>
          <Card title="实时告警流（未处理）" size="small" style={{ height: '100%' }}>
            <Table
              rowKey="id" size="small" pagination={false}
              dataSource={realtimeAlerts}
              scroll={{ y: screen ? 300 : 240 }}
              locale={{ emptyText: <Empty description="当前无未处理告警" /> }}
              columns={[
                { title: '集群', dataIndex: 'cluster_name', width: 110, ellipsis: true },
                { title: '资源', dataIndex: 'resource_name', ellipsis: true },
                { title: '命名空间', dataIndex: 'namespace', width: 100, ellipsis: true },
                { title: '故障', dataIndex: 'fault_type', width: 150, ellipsis: true },
                { title: '级别', dataIndex: 'severity', width: 72, render: (s: string) => <SeverityTag severity={s} /> },
                { title: '状态', dataIndex: 'status', width: 86, render: (s: string) => <StatusTag status={s} label={ALERT_STATUS_LABEL[s] || s} /> },
                { title: '时间', dataIndex: 'detected_at', width: 120, render: fmtTime },
              ]}
            />
          </Card>
        </Col>
        <Col xs={24} lg={8}>
          <Card title="Top 告警类型" size="small" style={{ height: '100%' }}>
            {faultTopData.length ? (
              <Bar
                data={faultTopData} xField="count" yField="fault_type"
                height={screen ? 280 : 240} colorField="fault_type" {...chartTheme}
                legend={false}
              />
            ) : <Empty description="暂无数据" />}
          </Card>
        </Col>
      </Row>

      {/* ===== 第 5 段 · 异常 Pod Top N（1/2） + 数据库健康概览（1/2） ===== */}
      <Row gutter={[12, 12]} align="stretch">
        <Col xs={24} lg={12}>
          <Card title="异常 Pod Top 20（按历史告警次数）" size="small" style={{ height: '100%' }}>
            <Table
              rowKey={(r) => `${r.cluster_name}-${r.namespace}-${r.pod_name}`}
              size="small" pagination={false}
              dataSource={abnormalPods}
              scroll={{ y: 240 }}
              locale={{ emptyText: <Empty description="暂无历史告警" /> }}
              columns={[
                { title: '集群', dataIndex: 'cluster_name', width: 100, ellipsis: true },
                { title: '命名空间', dataIndex: 'namespace', width: 110, ellipsis: true },
                { title: 'Pod', dataIndex: 'pod_name', ellipsis: true },
                { title: '告警数', dataIndex: 'alert_count', width: 80, defaultSortOrder: 'descend', sorter: (a: AbnormalPod, b: AbnormalPod) => (a.alert_count || 0) - (b.alert_count || 0) },
                { title: '已处理', width: 90, render: (_: unknown, r: AbnormalPod) => `${r.done_count ?? 0}/${r.alert_count ?? 0}` },
                { title: '故障类型', dataIndex: 'faults', ellipsis: true, render: (v: string) => v || '-' },
                { title: '最近', dataIndex: 'last_at', width: 120, render: fmtTime },
              ]}
            />
          </Card>
        </Col>
        <Col xs={24} lg={12}>
          <Card title="数据库健康概览" size="small" style={{ height: '100%' }}>
            <Table
              rowKey="id" size="small" pagination={false}
              dataSource={dbHealth}
              scroll={{ y: 240 }}
              locale={{ emptyText: <Empty description="暂无数据库连接" /> }}
              columns={[
                { title: '名称', dataIndex: 'name', ellipsis: true },
                { title: '类型', dataIndex: 'db_type', width: 76 },
                { title: '状态', dataIndex: 'status', width: 80, render: (s: string) => <Tag color={s === 'failed' ? 'red' : 'green'}>{s === 'failed' ? '异常' : '正常'}</Tag> },
                { title: '连接', width: 90, render: (_: unknown, r: DbHealthItem) => `${r.conn_used}/${r.conn_max}` },
                { title: '使用率', dataIndex: 'usage_pct', width: 110, render: (v: number) => (
                  <Progress percent={clampPct(v)} size="small" strokeColor={v >= 80 ? '#ff4d4f' : v >= 60 ? '#faad14' : '#52c41a'} format={(p) => `${p ?? 0}%`} />
                ) },
                { title: 'QPS', dataIndex: 'qps', width: 72 },
                { title: 'TPS', dataIndex: 'tps', width: 72 },
                { title: '缓存命中', dataIndex: 'cache_hit', width: 86, render: (v: number) => `${v}%` },
              ]}
            />
          </Card>
        </Col>
      </Row>

      {/* ===== 第 6 段 · 自动处理动作统计/时间线（1/2） + 集群资源水位 Top（1/2） ===== */}
      <Row gutter={[12, 12]} align="stretch">
        <Col xs={24} lg={12}>
          <Card title="自动处理动作（最近 24 小时）" size="small" style={{ height: '100%' }}>
            <Row gutter={[12, 12]} style={{ marginBottom: 12 }}>
              {actionItems.map((a) => (
                <Col key={a.key} span={6}>
                  <Card bodyStyle={{ padding: '8px 12px', textAlign: 'center' }}>
                    <Statistic
                      title={ACTION_META[a.key]?.label ?? a.key}
                      value={a.value}
                      valueStyle={{ color: ACTION_META[a.key]?.color === 'green' ? '#52c41a' : undefined, fontSize: 22, fontWeight: 700 }}
                    />
                  </Card>
                </Col>
              ))}
            </Row>
            <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', marginBottom: 6, color: muted, fontSize: 12 }}>
              <span>涉及告警 <b style={{ color: '#13c2c2' }}>{actionAlertCount}</b> 条</span>
              <span>其中平台标记自愈（无动作）<b style={{ color: '#faad14' }}>{actionHealedWithoutAction}</b> 条</span>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 6, color: muted, fontSize: 12 }}>
              <ThunderboltOutlined /> 最近动作时间线（倒序）
            </div>
            {timeline.length ? (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 6, maxHeight: 220, overflow: 'auto' }}>
                {timeline.map((t, i) => (
                  <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12 }}>
                    <Tag color={ACTION_META[t.action]?.color} style={{ marginInlineEnd: 0 }}>{ACTION_META[t.action]?.label ?? t.action}</Tag>
                    <span style={{ color: muted, width: 120, flexShrink: 0 }}>{fmtTime(t.time)}</span>
                    <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{t.target || '-'}</span>
                    <Tag color={t.status === 'success' ? 'green' : t.status === 'failed' ? 'red' : 'default'} style={{ marginInlineEnd: 0 }}>
                      {t.status === 'success' ? '成功' : t.status === 'failed' ? '失败' : t.status}
                    </Tag>
                  </div>
                ))}
              </div>
            ) : <Empty description="暂无动作记录" />}
          </Card>
        </Col>
        <Col xs={24} lg={12}>
          <Card title="集群资源水位 Top（点击进集群详情）" size="small" style={{ height: '100%' }}>
            <Table
              rowKey="cluster_id" size="small" pagination={false}
              dataSource={waterline}
              scroll={{ y: 320 }}
              locale={{ emptyText: <Empty description="暂无集群数据" /> }}
              onRow={(r) => ({ onClick: () => navigate(`/clusters/${r.cluster_id}`), style: { cursor: 'pointer' } })}
              columns={[
                { title: '集群', dataIndex: 'cluster_name', ellipsis: true },
                { title: 'CPU 水位', dataIndex: 'cpu_pct', width: 130, render: (v: number | null) => v === null || v === undefined ? '-' : (
                  <Progress percent={clampPct(v)} size="small" strokeColor={v >= 80 ? '#ff4d4f' : v >= 60 ? '#faad14' : '#52c41a'} format={(p) => `${p ?? 0}%`} />
                ) },
                { title: '内存水位', dataIndex: 'mem_pct', width: 130, render: (v: number | null) => v === null || v === undefined ? '-' : (
                  <Progress percent={clampPct(v)} size="small" strokeColor={v >= 80 ? '#ff4d4f' : v >= 60 ? '#faad14' : '#52c41a'} format={(p) => `${p ?? 0}%`} />
                ) },
                { title: 'Pod 数', dataIndex: 'pods', width: 80 },
                { title: '负载均衡', dataIndex: 'lb_count', width: 100, render: (v: number, r: ClusterWaterline) => (
                  <Tooltip title={`服务总数 ${r.service_total ?? 0}（其中 type=LoadBalancer ${v ?? 0}）`}>
                    <span style={{ fontWeight: 600 }}>{v ?? 0}</span>
                    <span style={{ color: muted, marginLeft: 4, fontSize: 12 }}>/ {r.service_total ?? 0}</span>
                  </Tooltip>
                ) },
                { title: '待处理', dataIndex: 'alert_pending', width: 80, render: (v: number) => <b style={{ color: v > 0 ? '#ff4d4f' : '#52c41a' }}>{v}</b> },
              ]}
            />
          </Card>
        </Col>
      </Row>
    </div>
  )
}
