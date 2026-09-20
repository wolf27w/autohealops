// 统计报表页：概览 + AI 有效性趋势 + 修复成功率 + 告警状态分布 + 导出/定时推送
import { useEffect, useState } from 'react'
import { Card, Row, Col, Statistic, Select, Tag, Empty, Table, Button, Space, Modal, Form, Input, Switch, Popconfirm, message } from 'antd'
import { DownloadOutlined, PlusOutlined, SendOutlined } from '@ant-design/icons'
import { Line, Pie } from '@ant-design/charts'
import {
  getStatsOverview, getAIEffectiveness, getRepairSuccessRate, getAlertStatusDistribution, getModelCost,
  getReportSchedules, createReportSchedule, updateReportSchedule, deleteReportSchedule, runReportSchedule, previewReport,
  getNotificationConfigs,
} from '../services/api'
import type { NotificationConfig } from '../types'

// 导出 CSV 并下载
function downloadCsv(rows: Array<Record<string, unknown>>, headers: { key: string; title: string }[], filename: string) {
  const head = headers.map((h) => h.title).join(',')
  const body = rows.map((r) => headers.map((h) => `"${String(r[h.key] ?? '').replace(/"/g, '""')}"`).join(',')).join('\n')
  const blob = new Blob(['\ufeff' + head + '\n' + body], { type: 'text/csv;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a'); a.href = url; a.download = filename; a.click(); URL.revokeObjectURL(url)
}

const STATUS_META: Record<string, { label: string; color: string }> = {
  pending: { label: '待处理', color: 'orange' },
  self_healed: { label: '自愈', color: 'green' },
  resolved: { label: '已解决', color: 'blue' },
  awaiting_approval: { label: '待审批', color: 'purple' },
}

interface StatsOverview {
  cluster_total?: number
  cluster_healthy?: number
  today_ai_cost?: number
  alert_pending?: number
}

interface TrendItem {
  date: string
  heal_rate: number
  ai_cost: number
  alerts?: number
  healed?: number
  ai_calls?: number
  [key: string]: unknown
}

interface RepairStats {
  alert_heal_rate?: number
  alert_healed?: number
  plan_success_rate?: number
}

interface StatusDistItem {
  status: string
  count: number
}

interface ModelCostData {
  total_cost?: number
  models?: Array<Record<string, unknown>>
}

interface ReportSchedule {
  id: number
  name?: string
  cron_expression?: string
  range_days?: number
  notify_config_id?: number
  enabled?: boolean
  last_run_at?: string
  [key: string]: unknown
}

export default function StatsDashboard() {
  const [overview, setOverview] = useState<StatsOverview>({})
  const [range, setRange] = useState('7d')
  const [trend, setTrend] = useState<TrendItem[]>([])
  const [repair, setRepair] = useState<RepairStats>({})
  const [dist, setDist] = useState<StatusDistItem[]>([])
  const [modelCost, setModelCost] = useState<ModelCostData>({ models: [], total_cost: 0 })
  // 定时推送
  const [schedules, setSchedules] = useState<ReportSchedule[]>([])
  const [configs, setConfigs] = useState<NotificationConfig[]>([])
  const [scOpen, setScOpen] = useState(false)
  const [editing, setEditing] = useState<ReportSchedule | null>(null)
  const [scForm] = Form.useForm()
  const [previewText, setPreviewText] = useState('')

  const loadSchedules = () => getReportSchedules().then((d) => setSchedules((d || []) as ReportSchedule[])).catch(() => {})

  const loadTrend = (r: string) => {
    getAIEffectiveness(r).then((d) => setTrend((d as { trend?: TrendItem[] }).trend || [])).catch(() => {})
    getModelCost(r).then((d) => setModelCost((d || { models: [], total_cost: 0 }) as ModelCostData)).catch(() => {})
  }

  useEffect(() => {
    getStatsOverview().then((d) => setOverview(d as StatsOverview)).catch(() => {})
    getRepairSuccessRate().then((d) => setRepair(d as RepairStats)).catch(() => {})
    getAlertStatusDistribution().then((d) => setDist((d as { distribution?: StatusDistItem[] }).distribution || [])).catch(() => {})
    loadSchedules()
    getNotificationConfigs().then((d) => setConfigs(d || [])).catch(() => {})
  }, [])

  useEffect(() => { loadTrend(range) }, [range])

  const healRateData = trend.map((t) => ({ date: t.date.slice(5), rate: (t.heal_rate * 100).toFixed(1) }))
  const costData = trend.map((t) => ({ date: t.date.slice(5), cost: t.ai_cost }))
  const pieData = dist.filter((d) => d.count > 0).map((d) => ({
    type: STATUS_META[d.status]?.label || d.status,
    value: d.count,
  }))

  return (
    <div>
      <Row gutter={16}>
        <Col span={6}><Card><Statistic title="集群总数" value={overview.cluster_total || 0} /></Card></Col>
        <Col span={6}><Card><Statistic title="健康集群" value={overview.cluster_healthy || 0} valueStyle={{ color: '#3f8600' }} /></Card></Col>
        <Col span={6}><Card><Statistic title="今日 AI 成本" value={overview.today_ai_cost || 0} precision={4} prefix="$" /></Card></Col>
        <Col span={6}><Card><Statistic title="待处理告警" value={overview.alert_pending || 0} valueStyle={{ color: '#cf1322' }} /></Card></Col>
      </Row>

      <Row gutter={16} style={{ marginTop: 16 }}>
        <Col span={12}>
          <Card title="AI 有效性趋势（告警自愈率）"
            extra={
              <Space>
                <Button size="small" icon={<DownloadOutlined />} onClick={() => downloadCsv(trend, [
                  { key: 'date', title: '日期' }, { key: 'alerts', title: '新增告警' },
                  { key: 'healed', title: '已解决' }, { key: 'heal_rate', title: '自愈率' },
                  { key: 'ai_calls', title: 'AI调用' }, { key: 'ai_cost', title: 'AI成本($)' },
                ], `ai-trend-${range}.csv`)}>导出</Button>
                <Select value={range} onChange={setRange} options={['7d', '30d', '90d'].map((r) => ({ value: r, label: r }))} style={{ width: 100 }} />
              </Space>
            }>
            {healRateData.length === 0 ? <Empty description="暂无数据" /> : (
              <Line data={healRateData} xField="date" yField="rate" height={240} smooth
                yAxis={{ title: { text: '自愈率 %' } }} />
            )}
          </Card>
        </Col>
        <Col span={12}>
          <Card title="AI 调用成本趋势">
            {costData.length === 0 ? <Empty description="暂无数据" /> : (
              <Line data={costData} xField="date" yField="cost" height={240} smooth
                yAxis={{ title: { text: '成本 $' } }} />
            )}
          </Card>
        </Col>
      </Row>

      <Row gutter={16} style={{ marginTop: 16 }}>
        <Col span={24}>
          <Card title={`各模型成本（合计 $${modelCost.total_cost ?? 0}）`}
            extra={<Button size="small" icon={<DownloadOutlined />} onClick={() => downloadCsv(modelCost.models || [], [
              { key: 'model_name', title: '模型' }, { key: 'calls', title: '调用次数' },
              { key: 'tokens', title: 'Token' }, { key: 'cost', title: '成本($)' },
            ], 'model-cost.csv')}>导出</Button>}>
            <Table rowKey="model_config_id" dataSource={modelCost.models || []} pagination={false} size="small"
              locale={{ emptyText: <Empty description="暂无数据" /> }}
              columns={[
                { title: '模型', dataIndex: 'model_name' },
                { title: '调用次数', dataIndex: 'calls', width: 120 },
                { title: 'Token 消耗', dataIndex: 'tokens', width: 140, render: (v) => Number(v).toLocaleString() },
                { title: '成本 ($)', dataIndex: 'cost', width: 140, render: (v) => `$${v}` },
              ]} />
          </Card>
        </Col>
      </Row>

      <Row gutter={16} style={{ marginTop: 16 }}>
        <Col span={12}>
          <Card title="修复成功率">
            <Row gutter={16}>
              <Col span={8}><Statistic title="告警自愈率" value={(Number(repair.alert_heal_rate) * 100).toFixed(1)} suffix="%" /></Col>
              <Col span={8}><Statistic title="已解决告警" value={repair.alert_healed || 0} valueStyle={{ color: '#3f8600' }} /></Col>
              <Col span={8}><Statistic title="修复计划成功率" value={(Number(repair.plan_success_rate) * 100).toFixed(1)} suffix="%" /></Col>
            </Row>
          </Card>
        </Col>
        <Col span={12}>
          <Card title="告警状态分布">
            <div style={{ display: 'flex', alignItems: 'center', gap: 24 }}>
              {pieData.length === 0 ? <Empty description="暂无数据" /> : (
                <Pie data={pieData} angleField="value" colorField="type" height={200} radius={0.8}
                  label={{ text: 'type' }} legend={{ color: { position: 'right' } }} />
              )}
              <div>
                {dist.map((d) => (
                  <div key={d.status} style={{ marginBottom: 8 }}>
                    <Tag color={STATUS_META[d.status]?.color}>{STATUS_META[d.status]?.label || d.status}</Tag>
                    {d.count}
                  </div>
                ))}
              </div>
            </div>
          </Card>
        </Col>
      </Row>

      {/* 报表定时推送（C4） */}
      <Row gutter={16} style={{ marginTop: 16 }}>
        <Col span={24}>
          <Card title="报表定时推送"
            extra={
              <Space>
                <Button size="small" onClick={async () => { const d = (await previewReport(7)) as { text?: string }; setPreviewText(d?.text || ''); Modal.info({ title: '报表预览（近 7 天）', content: <pre style={{ whiteSpace: 'pre-wrap' }}>{d?.text || ''}</pre>, width: 520 }) }}>预览</Button>
                <Button size="small" type="primary" icon={<PlusOutlined />} onClick={() => { setEditing(null); scForm.resetFields(); scForm.setFieldsValue({ range_days: 7, enabled: true, cron_expression: '0 9 * * *' }); setScOpen(true) }}>新建推送</Button>
              </Space>
            }>
            <Table rowKey="id" dataSource={schedules} pagination={false} size="small"
              locale={{ emptyText: <Empty description="暂无定时推送（可新建，如每天 09:00 推送到钉钉群）" /> }}
              columns={[
                { title: '名称', dataIndex: 'name' },
                { title: 'Cron', dataIndex: 'cron_expression', width: 140 },
                { title: '窗口(天)', dataIndex: 'range_days', width: 90 },
                { title: '渠道', dataIndex: 'notify_config_id', width: 160, render: (v) => configs.find((c) => c.id === v)?.name || (v ? `#${v}` : '-') },
                { title: '状态', dataIndex: 'enabled', width: 80, render: (v) => v ? <Tag color="green">启用</Tag> : <Tag>停用</Tag> },
                { title: '上次推送', dataIndex: 'last_run_at', width: 170, render: (v) => v ? String(v).slice(0, 19) : '-' },
                { title: '操作', width: 220, render: (_, r: ReportSchedule) => (
                  <Space>
                    <Button size="small" icon={<SendOutlined />} onClick={async () => { const res = (await runReportSchedule(r.id)) as { ok?: boolean; reason?: string }; if (res?.ok) message.success('已推送'); else message.warning(res?.reason || '推送失败') }}>立即推送</Button>
                    <Button size="small" onClick={() => { setEditing(r); scForm.setFieldsValue(r); setScOpen(true) }}>编辑</Button>
                    <Popconfirm title="删除该推送任务？" onConfirm={async () => { await deleteReportSchedule(r.id); message.success('已删除'); loadSchedules() }}>
                      <Button size="small" danger>删除</Button>
                    </Popconfirm>
                  </Space>
                ) },
              ]} />
          </Card>
        </Col>
      </Row>

      <Modal title={editing ? '编辑推送任务' : '新建推送任务'} open={scOpen} onCancel={() => setScOpen(false)}
        onOk={async () => {
          const v = await scForm.validateFields()
          if (editing) await updateReportSchedule(editing.id, v)
          else await createReportSchedule(v)
          message.success('已保存')
          setScOpen(false); loadSchedules()
        }}>
        <Form form={scForm} layout="vertical">
          <Form.Item name="name" label="名称" rules={[{ required: true }]}><Input placeholder="如：每日晨报" /></Form.Item>
          <Form.Item name="cron_expression" label="Cron（分 时 日 月 周）" rules={[{ required: true }]} tooltip="示例：0 9 * * * = 每天 09:00；0 9 * * 1 = 每周一 09:00"><Input placeholder="0 9 * * *" /></Form.Item>
          <Form.Item name="notify_config_id" label="推送渠道" tooltip="取「通知配置」里的渠道（钉钉/企微/飞书）">
            <Select allowClear placeholder="选择通知渠道" options={configs.map((c) => ({ value: c.id, label: `${c.name}（${c.channel_type}）` }))} />
          </Form.Item>
          <Form.Item name="range_days" label="统计窗口（天）"><Input type="number" /></Form.Item>
          <Form.Item name="enabled" label="启用" valuePropName="checked"><Switch /></Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
