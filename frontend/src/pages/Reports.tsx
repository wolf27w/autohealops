// 报表页（去重后）：报表导出（趋势/成本 CSV）+ 报表定时推送
// 说明：原「统计报表」页与仪表盘 / AI 运行看板重复的统计卡片与图表已移除，仅保留导出与推送能力
import { useEffect, useState } from 'react'
import { Card, Row, Col, Tag, Empty, Table, Button, Space, Modal, Form, Input, Switch, Popconfirm, Select, message } from 'antd'
import { DownloadOutlined, PlusOutlined, SendOutlined } from '@ant-design/icons'
import {
  getReportSchedules, createReportSchedule, updateReportSchedule, deleteReportSchedule, runReportSchedule, previewReport,
  getNotificationConfigs, getAIEffectiveness, getModelCost,
} from '../services/api'
import type { NotificationConfig } from '../types'

function downloadCsv(rows: Array<Record<string, unknown>>, headers: { key: string; title: string }[], filename: string) {
  const head = headers.map((h) => h.title).join(',')
  const body = rows.map((r) => headers.map((h) => `"${String(r[h.key] ?? '').replace(/"/g, '""')}"`).join(',')).join('\n')
  const blob = new Blob(['\ufeff' + head + '\n' + body], { type: 'text/csv;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a'); a.href = url; a.download = filename; a.click(); URL.revokeObjectURL(url)
}

interface TrendItem { date: string; heal_rate: number; ai_cost: number; alerts?: number; healed?: number; ai_calls?: number; [k: string]: unknown }
interface ModelCostData { total_cost?: number; models?: Array<Record<string, unknown>> }
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

export default function Reports() {
  const [range, setRange] = useState('7d')
  const [trend, setTrend] = useState<TrendItem[]>([])
  const [modelCost, setModelCost] = useState<ModelCostData>({ models: [], total_cost: 0 })
  const [schedules, setSchedules] = useState<ReportSchedule[]>([])
  const [configs, setConfigs] = useState<NotificationConfig[]>([])
  const [scOpen, setScOpen] = useState(false)
  const [editing, setEditing] = useState<ReportSchedule | null>(null)
  const [scForm] = Form.useForm()

  const loadSchedules = () => getReportSchedules().then((d) => setSchedules((d || []) as ReportSchedule[])).catch(() => {})
  const loadTrend = (r: string) => {
    getAIEffectiveness(r).then((d) => setTrend((d as { trend?: TrendItem[] }).trend || [])).catch(() => {})
    getModelCost(r).then((d) => setModelCost((d || { models: [], total_cost: 0 }) as ModelCostData)).catch(() => {})
  }

  useEffect(() => {
    loadSchedules()
    getNotificationConfigs().then((d) => setConfigs(d || [])).catch(() => {})
  }, [])
  useEffect(() => { loadTrend(range) }, [range])

  return (
    <div>
      <Card title="报表导出（CSV）" size="small"
        extra={<Select value={range} onChange={setRange} options={['7d', '30d', '90d'].map((r) => ({ value: r, label: `近 ${r}` }))} style={{ width: 110 }} />}>
        <Space wrap>
          <Button icon={<DownloadOutlined />} onClick={() => downloadCsv(trend, [
            { key: 'date', title: '日期' }, { key: 'alerts', title: '新增告警' },
            { key: 'healed', title: '已解决' }, { key: 'heal_rate', title: '自愈率' },
            { key: 'ai_calls', title: 'AI调用' }, { key: 'ai_cost', title: 'AI成本($)' },
          ], `ai-trend-${range}.csv`)}>导出 AI 有效性趋势</Button>
          <Button icon={<DownloadOutlined />} onClick={() => downloadCsv(modelCost.models || [], [
            { key: 'model_name', title: '模型' }, { key: 'calls', title: '调用次数' },
            { key: 'tokens', title: 'Token' }, { key: 'cost', title: '成本($)' },
          ], 'model-cost.csv')}>导出模型成本（合计 ${modelCost.total_cost ?? 0}）</Button>
        </Space>
        <div style={{ marginTop: 8, color: '#8c8c8c', fontSize: 12 }}>
          实时统计看板见「仪表盘」；AI 调用/成本明细见「策略配置 → AI配置 → 运行/成本看板」。
        </div>
      </Card>

      <Card title="报表定时推送" size="small" style={{ marginTop: 12 }}
        extra={
          <Space>
            <Button size="small" onClick={async () => { const d = (await previewReport(7)) as { text?: string }; Modal.info({ title: '报表预览（近 7 天）', content: <pre style={{ whiteSpace: 'pre-wrap' }}>{d?.text || ''}</pre>, width: 520 }) }}>预览</Button>
            <Button size="small" type="primary" icon={<PlusOutlined />} onClick={() => { setEditing(null); scForm.resetFields(); scForm.setFieldsValue({ range_days: 7, enabled: true, cron_expression: '0 9 * * *' }); setScOpen(true) }}>新建推送</Button>
          </Space>
        }>
        <Table rowKey="id" dataSource={schedules} pagination={false} size="small"
          locale={{ emptyText: <Empty description="暂无定时推送（可新建，如每天 09:00 推送到钉钉群）" /> }}
          columns={[
            { title: '名称', dataIndex: 'name' },
            { title: 'Cron', dataIndex: 'cron_expression', width: 140 },
            { title: '窗口(天)', dataIndex: 'range_days', width: 90 },
            { title: '渠道', dataIndex: 'notify_config_id', width: 160, render: (v: number) => configs.find((c) => c.id === v)?.name || (v ? `#${v}` : '-') },
            { title: '状态', dataIndex: 'enabled', width: 80, render: (v: boolean) => v ? <Tag color="green">启用</Tag> : <Tag>停用</Tag> },
            { title: '上次推送', dataIndex: 'last_run_at', width: 170, render: (v: string) => v ? String(v).slice(0, 19) : '-' },
            { title: '操作', width: 220, render: (_: unknown, r: ReportSchedule) => (
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
