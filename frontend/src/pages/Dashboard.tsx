// 仪表盘 v2：6 段布局（项目/集群/时间筛选 + 全局健康分 + 平台健康 + KPI + 趋势/分布 + 实时告警/排行 + 异常 Pod/DB 健康 + 动作统计/时间线 + 集群水位）
// 数据源：GET /api/v1/stats/dashboard?range&cluster_id&project_id（5s 轮询）+ /stats/platform-health
import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  Card, Row, Col, Statistic, Select, Segmented, Button, Table, Tag, Space, Progress, Empty, Badge, Tooltip,
} from 'antd'
import { ReloadOutlined, FullscreenOutlined, FullscreenExitOutlined, ThunderboltOutlined } from '@ant-design/icons'
import { Line, Pie, Bar } from '@ant-design/charts'
import { useNavigate } from 'react-router-dom'
import { getDashboard, getPlatformHealth, getProjects } from '../services/api'
import SeverityTag from '../components/SeverityTag'
import StatusTag from '../components/StatusTag'
import { ALERT_STATUS_LABEL } from '../constants'
import type {
  DashboardData, HealthScore, RealtimeAlert, AbnormalPod, DbHealthItem,
  ActionTimelineItem, ClusterWaterline,
} from '../types'

// 告警状态/级别：走公共常量 + SeverityTag/StatusTag 组件（去重）
const AI_RESULT_META: Record<string, { label: string; color: string }> = {
  self_heal: { label: '可自愈', color: '#52c41a' },
  approval: { label: '需审批', color: '#1677ff' },
  manual: { label: '需人工', color: '#faad14' },
  failed: { label: '诊断失败', color: '#ff4d4f' },
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

interface ProjectOption {
  id: number
  name: string
}

export default function Dashboard({ screen = false }: { screen?: boolean }) {
  const navigate = useNavigate()
  const [range, setRange] = useState('24h')
  const [clusterId, setClusterId] = useState<number | undefined>(undefined)
  const [projectId, setProjectId] = useState<number | undefined>(undefined)
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

  // 项目列表（下拉筛选）
  const { data: projects = [] } = useQuery<ProjectOption[]>({
    queryKey: ['projects'],
    queryFn: () => getProjects() as Promise<ProjectOption[]>,
  })

  // 平台健康（右上角小标，独立接口）
  const { data: platformHealth } = useQuery({
    queryKey: ['platform-health'],
    queryFn: () => getPlatformHealth(),
    refetchInterval: 15000,
  })

  // 仪表盘主数据（5s 轮询；筛选变化自动重查）
  const { data, isFetching, refetch } = useQuery<DashboardData>({
    queryKey: ['dashboard-v2', range, clusterId, projectId],
    queryFn: () => getDashboard({ range, cluster_id: clusterId, project_id: projectId }),
    refetchInterval: 5000,
  })

  const hs: HealthScore = data?.health_score ?? { total: 0, cluster_score: 0, alert_score: 0, db_score: 0 }
  const kpi = data?.kpi
  const clusters = data?.clusters ?? []

  // ---- 派生数据 ----
  const trendData = (data?.alert_trend ?? []).flatMap((t) => [
    { bucket: t.bucket, type: '新增', value: t.new },
    { bucket: t.bucket, type: '已解决', value: t.resolved },
    { bucket: t.bucket, type: '自愈', value: t.self_healed },
  ])
  const aiDistData = (data?.ai_result_dist ?? []).map((d) => ({
    rawKey: d.key,
    key: AI_RESULT_META[d.key]?.label ?? d.key,
    value: d.count ?? d.value ?? 0,
  }))
  // 百分比展示（老板要求：AI 诊断结果分布用百分比）
  const aiTotal = aiDistData.reduce((s, d) => s + (d.value || 0), 0)
  const aiDistPct = aiDistData.map((d) => ({
    ...d,
    pct: aiTotal ? Math.round((d.value / aiTotal) * 1000) / 10 : 0,
  }))
  const faultTopData = (data?.fault_top ?? []).map((f) => ({ fault_type: f.fault_type, count: f.count }))
  const realtimeAlerts: RealtimeAlert[] = data?.realtime_alerts ?? []
  const abnormalPods: AbnormalPod[] = data?.abnormal_pods ?? []
  const dbHealth: DbHealthItem[] = data?.db_health ?? []
  const actionStat = data?.action_stat ?? { self_heal: 0, restart: 0, scale: 0, rollback: 0 }
  const timeline: ActionTimelineItem[] = data?.action_timeline ?? []
  const waterline: ClusterWaterline[] = data?.cluster_waterline ?? []

  const platformOk = (ok: boolean | undefined) => (ok === undefined ? '#d9d9d9' : ok ? '#52c41a' : '#ff4d4f')

  const kpiItems = [
    { title: '集群数', value: kpi?.cluster_total ?? 0, color: '#1677ff' },
    { title: '活跃告警', value: kpi?.alert_pending ?? 0, color: (kpi?.alert_pending ?? 0) > 0 ? '#ff4d4f' : '#1677ff' },
    { title: '自动处理（近一年）', value: kpi?.auto_processed_1y ?? 0, color: '#13c2c2' },
    { title: '待人工', value: kpi?.manual_pending ?? 0, color: (kpi?.manual_pending ?? 0) > 0 ? '#faad14' : '#1677ff' },
    { title: 'DB 异常', value: kpi?.db_abnormal ?? 0, color: (kpi?.db_abnormal ?? 0) > 0 ? '#ff4d4f' : '#1677ff' },
    { title: '成功率', value: `${(((kpi?.heal_rate ?? 0) * 100).toFixed(1))}%`, color: '#52c41a' },
    { title: 'MTTR（分钟）', value: kpi?.mttr_minutes ?? 0, color: '#722ed1' },
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
      ...(screen ? { padding: 12, minHeight: '100vh', background: '#f5f6fa', boxSizing: 'border-box' as const } : {}),
    }}>
      {/* ===== 第 1 段 · 顶部栏 ===== */}
      <Card bodyStyle={{ padding: '10px 16px' }} style={{ marginBottom: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 12 }}>
          <span style={{ fontSize: 16, fontWeight: 600, marginRight: 4 }}>仪表盘</span>
          <Space size={8} wrap>
            <Select
              allowClear placeholder="全部项目" style={{ width: 160 }}
              value={projectId}
              onChange={(v) => setProjectId(v)}
              options={projects.map((p) => ({ value: p.id, label: p.name }))}
            />
            <Select
              allowClear placeholder="全部集群" style={{ width: 160 }}
              value={clusterId}
              onChange={(v) => setClusterId(v)}
              options={clusters.map((c) => ({ value: c.id, label: c.name }))}
            />
            <Segmented
              value={range}
              onChange={(v) => setRange(v as string)}
              options={['24h', '7d', '30d', '90d', '1y']}
            />
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
              <span style={{ fontSize: 12, color: '#888', lineHeight: 1.2 }}>全局<br />健康分</span>
            </div>
          </Tooltip>

          {/* 平台健康（右上角） */}
          <Space size={6} style={{ padding: '0 8px', borderLeft: '1px solid #f0f0f0' }}>
            <span style={{ fontSize: 12, color: '#888' }}>平台</span>
            <Badge color={platformOk(platformHealth?.api_server)} />
            <span style={{ fontSize: 12, color: '#888' }}>DB</span>
            <Badge color={platformOk(platformHealth?.database)} />
            <span style={{ fontSize: 12, color: '#888' }}>Redis</span>
            <Badge color={platformOk(platformHealth?.redis)} />
          </Space>

          <Button icon={<ReloadOutlined spin={isFetching} />} onClick={() => refetch()}>刷新</Button>
          <Button
            type="text"
            icon={fullscreen ? <FullscreenExitOutlined /> : <FullscreenOutlined />}
            onClick={toggleFullscreen}
          />
        </div>
      </Card>

      {/* ===== 第 2 段 · KPI 行（7 项） ===== */}
      <Row gutter={[12, 12]}>
        {kpiItems.map((k) => (
          <Col key={k.title} flex="1 1 150px">
            <Card bodyStyle={{ padding: '12px 16px' }}>
              <Statistic
                title={k.title} value={k.value}
                valueStyle={{ color: k.color, fontSize: 24, fontWeight: 700 }}
              />
            </Card>
          </Col>
        ))}
      </Row>

      {/* ===== 第 3 段 · 告警趋势（2/3） + AI 诊断结果分布（1/3） ===== */}
      <Row gutter={[12, 12]}>
        <Col xs={24} lg={16}>
          <Card title="告警趋势（新增 / 已解决 / 自愈）" size="small">
            {trendData.length ? (
              <Line
                data={trendData} xField="bucket" yField="value" colorField="type"
                height={240} smooth
                legend={{ color: { position: 'top' } }}
                scale={{ color: { range: ['#1677ff', '#52c41a', '#13c2c2'] } }}
              />
            ) : <Empty description="暂无数据" />}
          </Card>
        </Col>
        <Col xs={24} lg={8}>
          <Card title="AI 诊断结果分布" size="small">
            {aiDistPct.length ? (
              <>
                <div style={{ position: 'relative' }}>
                  <Pie
                    data={aiDistPct} angleField="value" colorField="key"
                    height={220} radius={0.78} innerRadius={0.6}
                    legend={false}
                    label={{
                      text: (d: { key: string; pct: number }) => `${d.key} ${d.pct}%`,
                      position: 'outside',
                      style: { fontSize: 14, fontWeight: 600, fill: '#1f2d3d' },
                    }}
                    tooltip={{ items: [{ channel: 'y', name: '告警数', valueFormatter: (v: number) => `${v} 条（${aiTotal ? Math.round((v / aiTotal) * 1000) / 10 : 0}%）` }] }}
                    scale={{ color: { range: ['#52c41a', '#1677ff', '#faad14', '#ff4d4f'] } }}
                  />
                  {/* 环形中心总量（HTML 叠层，字号可控） */}
                  <div style={{
                    position: 'absolute', top: '50%', left: '50%', transform: 'translate(-50%, -50%)',
                    textAlign: 'center', pointerEvents: 'none', lineHeight: 1.25,
                  }}>
                    <div style={{ fontSize: 13, color: '#8c8c8c' }}>总计</div>
                    <div style={{ fontSize: 22, fontWeight: 700, color: '#1f2d3d' }}>{aiTotal}</div>
                  </div>
                </div>
                {/* 自定义图例（中文更大 + 数量 + 百分比） */}
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px 18px', marginTop: 4 }}>
                  {aiDistPct.map((d) => {
                    const color = AI_RESULT_META[Object.keys(AI_RESULT_META).find((k) => AI_RESULT_META[k].label === d.key) ?? '']?.color ?? '#8c8c8c'
                    return (
                      <span key={d.key} style={{ display: 'inline-flex', alignItems: 'center', fontSize: 14, color: '#1f2d3d' }}>
                        <span style={{ width: 10, height: 10, borderRadius: 2, background: color, marginRight: 6, display: 'inline-block' }} />
                        {d.key}
                        <span style={{ marginLeft: 6, fontWeight: 700 }}>{d.pct}%</span>
                        <span style={{ marginLeft: 4, color: '#8c8c8c' }}>（{d.value}）</span>
                      </span>
                    )
                  })}
                </div>
              </>
            ) : <Empty description="暂无数据" />}
          </Card>
        </Col>
      </Row>

      {/* ===== 第 4 段 · 实时告警流（2/3） + Top 告警类型（1/3） ===== */}
      <Row gutter={[12, 12]}>
        <Col xs={24} lg={16}>
          <Card title="实时告警流（未处理）" size="small">
            <Table
              rowKey="id" size="small" pagination={false}
              dataSource={realtimeAlerts}
              scroll={{ y: 240 }}
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
          <Card title="Top 告警类型" size="small">
            {faultTopData.length ? (
              <Bar
                data={faultTopData} xField="count" yField="fault_type"
                height={240} colorField="fault_type"
                legend={false}
              />
            ) : <Empty description="暂无数据" />}
          </Card>
        </Col>
      </Row>

      {/* ===== 第 5 段 · 异常 Pod Top N（1/2） + 数据库健康概览（1/2） ===== */}
      <Row gutter={[12, 12]}>
        <Col xs={24} lg={12}>
          <Card title="异常 Pod Top 20" size="small">
            <Table
              rowKey={(r) => `${r.cluster_name}-${r.namespace}-${r.pod_name}-${r.fault_type}`}
              size="small" pagination={false}
              dataSource={abnormalPods}
              scroll={{ y: 240 }}
              locale={{ emptyText: <Empty description="暂无异常 Pod" /> }}
              columns={[
                { title: '集群', dataIndex: 'cluster_name', width: 110, ellipsis: true },
                { title: '命名空间', dataIndex: 'namespace', width: 100, ellipsis: true },
                { title: 'Pod', dataIndex: 'pod_name', ellipsis: true },
                { title: '故障', dataIndex: 'fault_type', width: 140, ellipsis: true },
                { title: '级别', dataIndex: 'severity', width: 72, render: (s: string) => <SeverityTag severity={s} /> },
                { title: '状态', dataIndex: 'status', width: 86, render: (s: string) => <StatusTag status={s} label={ALERT_STATUS_LABEL[s] || s} /> },
                { title: '时间', dataIndex: 'detected_at', width: 120, render: fmtTime },
              ]}
            />
          </Card>
        </Col>
        <Col xs={24} lg={12}>
          <Card title="数据库健康概览" size="small">
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
      <Row gutter={[12, 12]}>
        <Col xs={24} lg={12}>
          <Card title="自动处理（近一年）" size="small">
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
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 6, color: '#888', fontSize: 12 }}>
              <ThunderboltOutlined /> 最近动作时间线（倒序）
            </div>
            {timeline.length ? (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 6, maxHeight: 220, overflow: 'auto' }}>
                {timeline.map((t, i) => (
                  <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12 }}>
                    <Tag color={ACTION_META[t.action]?.color} style={{ marginInlineEnd: 0 }}>{ACTION_META[t.action]?.label ?? t.action}</Tag>
                    <span style={{ color: '#999', width: 120, flexShrink: 0 }}>{fmtTime(t.time)}</span>
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
          <Card title="集群资源水位 Top（点击进集群详情）" size="small">
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
                    <span style={{ color: '#8c8c8c', marginLeft: 4, fontSize: 12 }}>/ {r.service_total ?? 0}</span>
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
