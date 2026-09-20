// AI 模型配置页：模型列表（增删改）+ AI 调用日志（分页/统计分类/筛选）
import { useEffect, useState } from 'react'
import type { AICallLog } from '../types'
import { Card, Button, Modal, Form, Input, InputNumber, Select, Switch, Space, Table, Tag, message, Popconfirm, Row, Col, Empty } from 'antd'
import { PlusOutlined, EditOutlined, DeleteOutlined } from '@ant-design/icons'
import { getAIModels, createAIModel, updateAIModel, deleteAIModel, getAICallLogs, getAICallLogStats } from '../services/api'

const MODEL_FIELDS = {
  provider: { label: 'Provider' },
  model_name: { label: '模型名' },
  api_key_encrypted: { label: 'API Key' },
  api_base: { label: 'API Base' },
  max_tokens: { label: 'Max Tokens' },
  temperature: { label: 'Temperature' },
  priority: { label: '优先级' },
  min_severity: { label: '最低severity' },
  cost_per_1k_tokens: { label: '每千token成本' },
}

interface AIModel {
  id: number
  provider?: string
  model_name?: string
  priority?: number
  min_severity?: string
  cost_per_1k_tokens?: number
  is_active?: boolean
  api_key_encrypted?: string
  [key: string]: unknown
}

interface CallStat {
  request_type: string
  count: number
}

export default function AIModelConfig() {
  const [models, setModels] = useState<AIModel[]>([])
  const [logs, setLogs] = useState<AICallLog[]>([])
  const [stats, setStats] = useState<CallStat[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [reqType, setReqType] = useState('')
  const [modalOpen, setModalOpen] = useState(false)
  const [editing, setEditing] = useState<AIModel | null>(null)
  const [form] = Form.useForm()

  const loadModels = () => getAIModels().then((d) => setModels((d || []) as AIModel[]))
  const loadLogs = (p = page, rt = reqType) => {
    getAICallLogs({ page: p, page_size: 20, request_type: rt || undefined }).then((d) => {
      const r = d as { items?: AICallLog[]; total?: number }
      setLogs(r.items || [])
      setTotal(r.total || 0)
    })
  }
  const loadStats = () => getAICallLogStats().then((d) => setStats((d || []) as CallStat[]))
  useEffect(() => { loadModels(); loadLogs(); loadStats() }, [])

  const openCreate = () => {
    setEditing(null); form.resetFields()
    form.setFieldsValue({ temperature: 0.7, max_tokens: 4096, priority: 100, min_severity: 'low', cost_per_1k_tokens: 0, is_active: true })
    setModalOpen(true)
  }
  const openEdit = (m: AIModel) => {
    setEditing(m); form.setFieldsValue({ ...m, api_key_encrypted: '' }); setModalOpen(true)
  }

  const handleSave = async () => {
    const values = await form.validateFields()
    if (editing) {
      const payload = { ...values }
      if (!payload.api_key_encrypted) delete payload.api_key_encrypted
      await updateAIModel(editing.id, payload)
      message.success('模型已更新')
    } else {
      await createAIModel(values)
      message.success('模型已添加')
    }
    setModalOpen(false); loadModels()
  }

  const handleDelete = async (id: number) => { await deleteAIModel(id); message.success('已删除'); loadModels() }

  const clickType = (rt: string) => { setReqType(rt); setPage(1); loadLogs(1, rt) }

  return (
    <div>
      <Card title="模型配置" extra={<Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>添加模型</Button>}>
        <Space wrap>
          {models.map((m) => (
            <Card key={m.id} size="small" style={{ width: 300 }}>
              <div style={{ fontWeight: 'bold' }}>{m.provider} / {m.model_name}</div>
              <div style={{ color: '#888' }}>优先级：{m.priority} ｜ 最低severity：{m.min_severity}</div>
              <div style={{ color: '#888' }}>成本：{m.cost_per_1k_tokens} /1k tokens</div>
              <Space style={{ marginTop: 8 }}>
                <Tag color={m.is_active ? 'green' : 'gray'}>{m.is_active ? '启用' : '停用'}</Tag>
                <Button size="small" icon={<EditOutlined />} onClick={() => openEdit(m)}>编辑</Button>
                <Popconfirm title="确认删除该模型？" onConfirm={() => handleDelete(m.id)}>
                  <Button size="small" danger icon={<DeleteOutlined />} />
                </Popconfirm>
              </Space>
            </Card>
          ))}
          {models.length === 0 && <Empty description="暂无模型，点击右上角添加" style={{ width: '100%' }} />}
        </Space>
      </Card>

      <Card title="AI 调用日志" style={{ marginTop: 16 }}>
        <Row gutter={12} style={{ marginBottom: 16 }}>
          <Col span={24}>
            <Space wrap>
              <Tag style={{ cursor: 'pointer', padding: '4px 10px' }} color={reqType === '' ? 'blue' : 'default'} onClick={() => clickType('')}>全部（{stats.reduce((s, x) => s + x.count, 0)}）</Tag>
              {stats.map((s) => (
                <Tag key={s.request_type} style={{ cursor: 'pointer', padding: '4px 10px' }} color={reqType === s.request_type ? 'blue' : 'default'} onClick={() => clickType(s.request_type)}>
                  {s.request_type}（{s.count}）
                </Tag>
              ))}
            </Space>
          </Col>
        </Row>
        <Table
          rowKey="id"
          dataSource={logs}
          pagination={{ current: page, pageSize: 20, total, showSizeChanger: false, onChange: (p) => { setPage(p); loadLogs(p) } }}
          columns={[
            { title: '类型', dataIndex: 'request_type', render: (t) => <Tag>{t || '-'}</Tag> },
            { title: '模型ID', dataIndex: 'model_config_id', width: 80 },
            { title: 'Token', dataIndex: 'token_usage', width: 90 },
            { title: '耗时(ms)', dataIndex: 'latency_ms', width: 100 },
            { title: '成本', dataIndex: 'cost', width: 90 },
            { title: '状态', dataIndex: 'status', width: 90, render: (s) => <Tag color={s === 'success' ? 'green' : 'red'}>{s}</Tag> },
            { title: '时间', dataIndex: 'created_at', render: (v) => (v ? v.slice(0, 19).replace('T', ' ') : '-') },
          ]}
        />
      </Card>

      <Modal title={editing ? '编辑 AI 模型' : '添加 AI 模型'} open={modalOpen} onOk={handleSave} onCancel={() => setModalOpen(false)}>
        <Form form={form} layout="vertical">
          <Form.Item name="provider" label="Provider" rules={[{ required: true }]}>
            <Select options={['openai', 'deepseek', 'claude'].map((p) => ({ value: p, label: p }))} />
          </Form.Item>
          <Form.Item name="model_name" label="模型名" rules={[{ required: true }]}>
            <Input placeholder="如 deepseek-v4-flash / deepseek-chat" />
          </Form.Item>
          <Form.Item name="api_key_encrypted" label={editing ? 'API Key（留空=不修改）' : 'API Key'} rules={editing ? [] : [{ required: true }]}>
            <Input.Password placeholder="AI 模型 API Key" />
          </Form.Item>
          <Form.Item name="api_base" label="API Base（可选）">
            <Input placeholder="如 https://api.deepseek.com" />
          </Form.Item>
          <Space>
            <Form.Item name="max_tokens" label="Max Tokens"><InputNumber min={1} /></Form.Item>
            <Form.Item name="temperature" label="Temperature"><InputNumber min={0} max={2} step={0.1} /></Form.Item>
          </Space>
          <Space>
            <Form.Item name="priority" label="优先级（小优先）"><InputNumber min={1} /></Form.Item>
            <Form.Item name="cost_per_1k_tokens" label="每千token成本"><InputNumber min={0} step={0.001} /></Form.Item>
          </Space>
          <Form.Item name="min_severity" label="最低severity">
            <Select options={['critical', 'high', 'medium', 'low'].map((s) => ({ value: s, label: s }))} />
          </Form.Item>
          <Form.Item name="is_active" label="启用" valuePropName="checked"><Switch /></Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
