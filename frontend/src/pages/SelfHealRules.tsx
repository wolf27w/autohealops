// 自愈场景规则页：按集群/namespace/故障类型/资源类型配置强制人工/强制自动（含影子模式）
import { useEffect, useState } from 'react'
import { Card, Table, Button, Modal, Form, Input, InputNumber, Select, Switch, Space, Tag, Popconfirm, message } from 'antd'
import { PlusOutlined } from '@ant-design/icons'
import { getSelfHealRules, createSelfHealRule, updateSelfHealRule, deleteSelfHealRule, getClusters } from '../services/api'
import type { Cluster } from '../types'

interface SelfHealRule {
  id: number
  name: string
  cluster_id?: number | null
  cluster_name?: string
  namespace_match?: string
  fault_type_match?: string
  resource_type_match?: string
  action: string
  priority?: number
  shadow?: boolean
  enabled?: boolean
  [key: string]: unknown
}

export default function SelfHealRules() {
  const [data, setData] = useState<SelfHealRule[]>([])
  const [clusters, setClusters] = useState<Cluster[]>([])
  const [open, setOpen] = useState(false)
  const [editing, setEditing] = useState<SelfHealRule | null>(null)
  const [form] = Form.useForm()

  const load = () => getSelfHealRules().then((d) => setData((d || []) as SelfHealRule[]))
  useEffect(() => { load(); getClusters().then((d) => setClusters(d || [])) }, [])

  const clusterOptions = [
    { value: 0, label: '全部集群' },
    ...clusters.map((c) => ({ value: c.id, label: `${c.name} (#${c.id})` })),
  ]

  const save = async () => {
    const v = await form.validateFields()
    const payload = { ...v, cluster_id: v.cluster_id || null }
    if (editing) await updateSelfHealRule(editing.id, payload)
    else await createSelfHealRule(payload)
    message.success('已保存'); setOpen(false); form.resetFields(); load()
  }

  const openEdit = (r: SelfHealRule) => { setEditing(r); form.setFieldsValue({ ...r, cluster_id: r.cluster_id || 0 }); setOpen(true) }

  return (
    <Card title="自愈场景规则">
      <div style={{ marginBottom: 12, color: '#666' }}>
        覆盖默认的自动/人工决策：命中规则后，<b>强制人工</b>（即使风险评分是自动也走审批）或 <b>强制自动</b>（仅覆盖单人审批）。
        新规则默认开启<b>影子模式</b>——只记录「如果生效会怎样」、不实际改变决策，观察确认无误后再关闭影子模式正式生效。
        匹配条件留空 = 不限制该维度；namespace 支持前缀通配（如 <Tag>prod-*</Tag>）。优先级数字越小越优先。
      </div>
      <Button type="primary" icon={<PlusOutlined />} style={{ marginBottom: 16 }}
        onClick={() => { setEditing(null); form.resetFields(); form.setFieldsValue({ cluster_id: 0, action: 'force_manual', priority: 100, shadow: true, enabled: true }); setOpen(true) }}>
        新增规则
      </Button>
      <Table rowKey="id" dataSource={data} columns={[
        { title: '规则名', dataIndex: 'name', width: 160 },
        { title: '集群', dataIndex: 'cluster_id', width: 110, render: (v, r) => v ? (r.cluster_name || `#${v}`) : '全部' },
        { title: 'namespace', dataIndex: 'namespace_match', width: 110, render: (v) => v || '全部' },
        { title: '故障类型', dataIndex: 'fault_type_match', width: 130, render: (v) => v || '全部' },
        { title: '资源类型', dataIndex: 'resource_type_match', width: 90, render: (v) => v || '全部' },
        { title: '动作', dataIndex: 'action', width: 100, render: (a) => <Tag color={a === 'force_manual' ? 'orange' : 'green'}>{a === 'force_manual' ? '强制人工' : '强制自动'}</Tag> },
        { title: '优先级', dataIndex: 'priority', width: 80 },
        { title: '影子', dataIndex: 'shadow', width: 80, render: (v) => v ? <Tag color="blue">影子</Tag> : <Tag color="green">生效</Tag> },
        { title: '启用', dataIndex: 'enabled', width: 70, render: (v) => (v ? '是' : '否') },
        { title: '操作', render: (_, r) => (
          <Space>
            <Button size="small" onClick={() => openEdit(r)}>编辑</Button>
            <Popconfirm title="确认删除？" onConfirm={async () => { await deleteSelfHealRule(r.id); load() }}>
              <Button size="small" danger>删除</Button>
            </Popconfirm>
          </Space>
        ) },
      ]} />
      <Modal title={editing ? '编辑规则' : '新增规则'} open={open} onOk={save} onCancel={() => setOpen(false)} width={560}>
        <Form form={form} layout="vertical">
          <Form.Item name="name" label="规则名" rules={[{ required: true }]}><Input placeholder="如 生产环境 OOM 强制人工" /></Form.Item>
          <Form.Item name="cluster_id" label="集群（空=全部）">
            <Select options={clusterOptions} allowClear placeholder="选择集群" />
          </Form.Item>
          <Form.Item name="namespace_match" label="namespace 匹配" tooltip="空=全部；prod-* 表示所有 prod 开头 namespace">
            <Input placeholder="prod-*（支持 * 前缀通配）" />
          </Form.Item>
          <Form.Item name="fault_type_match" label="故障类型（空=全部）"><Input placeholder="如 OOMKilled / CrashLoopBackOff / db_ 前缀" /></Form.Item>
          <Form.Item name="resource_type_match" label="资源类型（空=全部）">
            <Select allowClear options={[{ value: 'pod', label: 'Pod' }, { value: 'node', label: '节点' }]} />
          </Form.Item>
          <Form.Item name="action" label="动作" rules={[{ required: true }]}>
            <Select options={[
              { value: 'force_manual', label: '强制人工（覆盖自动→审批）' },
              { value: 'force_auto', label: '强制自动（覆盖单人审批→自动）' },
            ]} />
          </Form.Item>
          <Form.Item name="priority" label="优先级（数字小=高）"><InputNumber min={1} style={{ width: '100%' }} /></Form.Item>
          <Form.Item name="shadow" label="影子模式（只记录不生效）" valuePropName="checked" tooltip="新规则建议先开影子模式观察，确认无误后关闭"><Switch /></Form.Item>
          <Form.Item name="enabled" label="启用" valuePropName="checked"><Switch /></Form.Item>
        </Form>
      </Modal>
    </Card>
  )
}
