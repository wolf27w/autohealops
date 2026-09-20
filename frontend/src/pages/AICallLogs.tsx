// AI 运行 / 成本看板 + 调用日志（C5）
import { useEffect, useState } from 'react'
import type { AICallLog } from '../types'
import { Card, Row, Col, Statistic, Table, Select, Empty, Tag, Space } from 'antd'
import { getAIDashboard, getAICallLogs } from '../services/api'

const REQ_LABEL: Record<string, string> = {
  diagnosis: '告警诊断', multi_diagnose: '多模型诊断', repair_plan: '修复方案', verification: '修复验证',
  agent_decide: '应用诊断决策', agent_decide_once: '应用诊断(单轮)', db_diagnose: '数据库诊断', unknown: '其他',
}

interface AISummary {
  calls?: number
  cost?: number
  success_rate?: number
  avg_latency_ms?: number
}

interface AIDashboard {
  summary?: AISummary
  today?: AISummary
  by_type?: Array<Record<string, unknown>>
  by_model?: Array<Record<string, unknown>>
}

export default function AICallLogs() {
  const [range, setRange] = useState(7)
  const [dash, setDash] = useState<AIDashboard>({})
  const [logs, setLogs] = useState<AICallLog[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [reqType, setReqType] = useState<string>()

  const loadDash = () => getAIDashboard(range).then((d) => setDash((d || {}) as AIDashboard)).catch(() => {})
  const loadLogs = () => getAICallLogs({ page, page_size: 20, request_type: reqType || '' })
    .then((d) => { const r = d as { items?: AICallLog[]; total?: number }; setLogs(r.items || []); setTotal(r.total || 0) }).catch(() => {})

  useEffect(() => { loadDash() }, [range])
  useEffect(() => { loadLogs() }, [page, reqType])

  const s: AISummary = dash.summary || {}
  const t: AISummary = dash.today || {}

  return (
    <div>
      <Space style={{ marginBottom: 12 }}>
        <span>统计窗口：</span>
        <Select value={range} onChange={setRange} style={{ width: 110 }}
          options={[{ value: 1, label: '近 1 天' }, { value: 7, label: '近 7 天' }, { value: 30, label: '近 30 天' }]} />
      </Space>

      <Row gutter={[12, 12]}>
        <Col span={4}><Card size="small"><Statistic title="今日调用" value={t.calls || 0} /></Card></Col>
        <Col span={4}><Card size="small"><Statistic title="今日成本" value={t.cost || 0} precision={4} prefix="$" /></Card></Col>
        <Col span={4}><Card size="small"><Statistic title={`近${range}天调用`} value={s.calls || 0} /></Card></Col>
        <Col span={4}><Card size="small"><Statistic title="成功率" value={((s.success_rate || 0) * 100).toFixed(1)} suffix="%" valueStyle={{ color: '#3f8600' }} /></Card></Col>
        <Col span={4}><Card size="small"><Statistic title="总成本" value={s.cost || 0} precision={4} prefix="$" /></Card></Col>
        <Col span={4}><Card size="small"><Statistic title="平均耗时" value={s.avg_latency_ms || 0} suffix="ms" /></Card></Col>
      </Row>

      <Row gutter={[12, 12]} style={{ marginTop: 12 }}>
        <Col span={12}>
          <Card size="small" title="按调用类型">
            <Table rowKey="request_type" size="small" pagination={false} dataSource={dash.by_type || []}
              locale={{ emptyText: <Empty description="暂无数据" /> }}
              columns={[
                { title: '类型', dataIndex: 'request_type', render: (v) => REQ_LABEL[v] || v },
                { title: '次数', dataIndex: 'calls', width: 80 },
                { title: '成功', dataIndex: 'success', width: 80 },
                { title: '耗时(ms)', dataIndex: 'avg_latency_ms', width: 100 },
                { title: '成本($)', dataIndex: 'cost', width: 100 },
              ]} />
          </Card>
        </Col>
        <Col span={12}>
          <Card size="small" title="按模型">
            <Table rowKey="model_name" size="small" pagination={false} dataSource={dash.by_model || []}
              locale={{ emptyText: <Empty description="暂无数据" /> }}
              columns={[
                { title: '模型', dataIndex: 'model_name' },
                { title: '次数', dataIndex: 'calls', width: 80 },
                { title: 'Token', dataIndex: 'tokens', width: 110, render: (v) => Number(v).toLocaleString() },
                { title: '耗时(ms)', dataIndex: 'avg_latency_ms', width: 100 },
                { title: '成本($)', dataIndex: 'cost', width: 100 },
              ]} />
          </Card>
        </Col>
      </Row>

      <Card size="small" title="调用日志" style={{ marginTop: 12 }}
        extra={
          <Select allowClear placeholder="按类型筛选" style={{ width: 160 }} value={reqType} onChange={(v) => { setPage(1); setReqType(v) }}
            options={Object.entries(REQ_LABEL).map(([k, v]) => ({ value: k, label: v }))} />
        }>
        <Table rowKey="id" size="small" dataSource={logs}
          pagination={{ current: page, pageSize: 20, total, onChange: setPage, showTotal: (n) => `共 ${n} 条` }}
          locale={{ emptyText: <Empty description="暂无调用日志" /> }}
          columns={[
            { title: 'ID', dataIndex: 'id', width: 70 },
            { title: '类型', dataIndex: 'request_type', width: 150, render: (v) => REQ_LABEL[v] || v },
            { title: '模型ID', dataIndex: 'model_config_id', width: 90 },
            { title: 'Token', dataIndex: 'token_usage', width: 100 },
            { title: '耗时(ms)', dataIndex: 'latency_ms', width: 100 },
            { title: '成本($)', dataIndex: 'cost', width: 100, render: (v) => `$${v}` },
            { title: '状态', dataIndex: 'status', width: 90, render: (v) => <Tag color={v === 'success' ? 'green' : 'red'}>{v}</Tag> },
            { title: '时间', dataIndex: 'created_at', width: 170, render: (v) => String(v).slice(0, 19) },
          ]} />
      </Card>
    </div>
  )
}
