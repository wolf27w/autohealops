// 自动化策略：AI 处置授权矩阵（2026-11-26）
// 后端：GET/PUT /api/v1/settings/automation-policy
// 口径：动作库 risk 是底线（forbidden 永不放开），这里按「环境 × 动作」做显式授权覆盖。
import { useEffect, useState } from 'react'
import {
  Alert, Button, Card, Col, InputNumber, Radio, Row, Select, Space, Table, Tag, Typography, message,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { getAutomationPolicy, updateAutomationPolicy } from '../services/api'

const { Text, Paragraph } = Typography

type Decision = 'auto' | 'approve' | 'manual' | 'forbidden' | ''

interface ActionRow {
  key: string
  name: string
  risk: string
  description?: string
  rollback_note?: string
  prod: Decision
  test: Decision
}

interface PolicyData {
  mode: string
  thresholds: { prod: number; test: number }
  defaults: { mode: string; thresholds: { prod: number; test: number } }
  actions: ActionRow[]
}

const DECISION_OPTIONS: { value: Decision; label: string }[] = [
  { value: 'auto', label: '自动执行' },
  { value: 'approve', label: '人工审批' },
  { value: 'manual', label: '仅人工处理' },
  { value: 'forbidden', label: '禁止执行' },
]

const RISK_COLOR: Record<string, string> = { green: 'green', yellow: 'gold', red: 'red', forbidden: 'magenta' }

export default function AutomationPolicy() {
  const [mode, setMode] = useState('by_env')
  const [prodThreshold, setProdThreshold] = useState(0.8)
  const [testThreshold, setTestThreshold] = useState(0.6)
  const [rows, setRows] = useState<ActionRow[]>([])
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)

  const load = async () => {
    setLoading(true)
    try {
      const d = (await getAutomationPolicy()) as unknown as PolicyData
      setMode(d.mode || 'by_env')
      setProdThreshold(d.thresholds?.prod ?? d.defaults?.thresholds?.prod ?? 0.8)
      setTestThreshold(d.thresholds?.test ?? d.defaults?.thresholds?.test ?? 0.6)
      setRows(d.actions || [])
    } catch (e) {
      message.error(`加载失败：${(e as Error).message}`)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  const patchRow = (key: string, bucket: 'prod' | 'test', value: Decision) => {
    setRows((prev) => prev.map((r) => (r.key === key ? { ...r, [bucket]: value } as ActionRow : r)))
  }

  const save = async () => {
    setSaving(true)
    try {
      const actions: Record<string, { prod?: string; test?: string }> = {}
      rows.forEach((r) => {
        const item: { prod?: string; test?: string } = {}
        if (r.prod) item.prod = r.prod
        if (r.test) item.test = r.test
        if (Object.keys(item).length) actions[r.key] = item
      })
      await updateAutomationPolicy({
        mode,
        thresholds: { prod: Number(prodThreshold), test: Number(testThreshold) },
        actions,
      })
      message.success('已保存，下一次处置立即生效')
      await load()
    } catch (e) {
      message.error(`保存失败：${(e as Error).message}`)
    } finally {
      setSaving(false)
    }
  }

  const columns: ColumnsType<ActionRow> = [
    {
      title: '动作', dataIndex: 'name', width: 200,
      render: (v: string, r) => <Space direction="vertical" size={0}>
        <Text strong>{v}</Text>
        <Text type="secondary" style={{ fontSize: 12 }}>{r.key}</Text>
      </Space>,
    },
    {
      title: '动作库风险', dataIndex: 'risk', width: 100,
      render: (v: string) => <Tag color={RISK_COLOR[v] || 'default'}>{v || '-'}</Tag>,
    },
    {
      title: '生产环境', width: 150,
      render: (_, r) => (
        <Select
          size="small" style={{ width: 130 }} allowClear placeholder="按风险默认"
          value={r.prod || undefined}
          options={DECISION_OPTIONS}
          onChange={(v) => patchRow(r.key, 'prod', (v || '') as Decision)}
        />
      ),
    },
    {
      title: '测试 / UAT', width: 150,
      render: (_, r) => (
        <Select
          size="small" style={{ width: 130 }} allowClear placeholder="按风险默认"
          value={r.test || undefined}
          options={DECISION_OPTIONS}
          onChange={(v) => patchRow(r.key, 'test', (v || '') as Decision)}
        />
      ),
    },
    {
      title: '说明', dataIndex: 'rollback_note',
      render: (v: string, r) => <Text type="secondary" style={{ fontSize: 12 }}>{v || r.description || '-'}</Text>,
    },
  ]

  return (
    <div>
      <Card loading={loading} title="自动化策略" extra={<Button type="primary" loading={saving} onClick={save}>保存</Button>}>
        <Alert
          type="info" showIcon style={{ marginBottom: 16 }}
          message="AI 处置授权：风险等级是底线，下面的配置只会在动作库允许的范围内收紧或放开"
          description="动作库标为 forbidden 的动作永远不能执行；未配置的行按动作风险默认（green 自动 / yellow 有条件自动 / red 必须审批）。生产环境配置 auto 时仍要求置信度达标。"
        />
        <Row gutter={24}>
          <Col span={10}>
            <div style={{ marginBottom: 8 }}><Text strong>处置模式</Text></div>
            <Radio.Group value={mode} onChange={(e) => setMode(e.target.value)}>
              <Space direction="vertical">
                <Radio value="by_env">按环境（推荐）：生产审批、测试全自动</Radio>
                <Radio value="assist">全部环境都走审批红线</Radio>
                <Radio value="full">全部环境直接执行（仅 forbidden 拦截）</Radio>
              </Space>
            </Radio.Group>
          </Col>
          <Col span={14}>
            <div style={{ marginBottom: 8 }}><Text strong>自动执行所需的最低置信度</Text></div>
            <Space size="large">
              <Space>
                <Text>生产</Text>
                <InputNumber min={0} max={1} step={0.05} value={prodThreshold}
                  onChange={(v) => setProdThreshold(Number(v ?? 0.8))} />
              </Space>
              <Space>
                <Text>测试 / UAT</Text>
                <InputNumber min={0} max={1} step={0.05} value={testThreshold}
                  onChange={(v) => setTestThreshold(Number(v ?? 0.6))} />
              </Space>
            </Space>
            <Paragraph type="secondary" style={{ marginTop: 8, fontSize: 12 }}>
              低于阈值的诊断结论不会自动执行，转审批或人工。当前默认 0.8 / 0.6。
            </Paragraph>
          </Col>
        </Row>
        <Table
          style={{ marginTop: 16 }}
          rowKey="key"
          size="small"
          pagination={false}
          columns={columns}
          dataSource={rows}
        />
      </Card>
    </div>
  )
}
