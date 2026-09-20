// 决策策略页
import { useEffect, useState } from 'react'
import { Table, Button, Modal, Form, Input, Select, Switch, Space, message } from 'antd'
import { getDecisionPolicies, createDecisionPolicy, updateDecisionPolicy } from '../services/api'

const RULE_OPTIONS = ['auto', 'auto_with_log', 'auto_with_confirm', 'approval_1', 'approval_1_with_escalation', 'approval_2']
const SCHEDULE_OPTIONS = ['working_hours', 'off_hours', 'weekend', 'holiday']

interface DecisionPolicy {
  id: number
  name: string
  schedule_type?: string
  start_time?: string
  end_time?: string
  read_rule?: string
  create_rule?: string
  update_rule?: string
  delete_rule?: string
  auto_escalation?: boolean
  [key: string]: unknown
}

export default function DecisionPolicies() {
  const [policies, setPolicies] = useState<DecisionPolicy[]>([])
  const [modalOpen, setModalOpen] = useState(false)
  const [editing, setEditing] = useState<DecisionPolicy | null>(null)
  const [form] = Form.useForm()

  const load = () => getDecisionPolicies().then((d) => setPolicies(d as DecisionPolicy[]))
  useEffect(() => { load() }, [])

  const openModal = (p?: DecisionPolicy) => {
    setEditing(p || null)
    setModalOpen(true)
    if (p) form.setFieldsValue(p)
  }

  const handleSave = async () => {
    const values = await form.validateFields()
    if (editing) await updateDecisionPolicy(editing.id, values)
    else await createDecisionPolicy(values)
    message.success('已保存')
    setModalOpen(false)
    form.resetFields()
    load()
  }

  const ruleSelect = (name: string, label: string) => (
    <Form.Item name={name} label={label} rules={[{ required: true }]}>
      <Select options={RULE_OPTIONS.map((r) => ({ value: r, label: r }))} />
    </Form.Item>
  )

  return (
    <div>
      <Button type="primary" style={{ marginBottom: 16 }} onClick={() => openModal()}>添加策略</Button>
      <Table rowKey="id" dataSource={policies}
        columns={[
          { title: '名称', dataIndex: 'name' },
          { title: '时段', dataIndex: 'schedule_type' },
          { title: '时间段', render: (_, r) => `${r.start_time}-${r.end_time}` },
          { title: 'READ', dataIndex: 'read_rule' },
          { title: 'CREATE', dataIndex: 'create_rule' },
          { title: 'UPDATE', dataIndex: 'update_rule' },
          { title: 'DELETE', dataIndex: 'delete_rule' },
          { title: '自动升级', dataIndex: 'auto_escalation', render: (v) => (v ? '是' : '否') },
          { title: '操作', render: (_, r) => <Button size="small" onClick={() => openModal(r)}>编辑</Button> },
        ]} />
      <Modal title={editing ? '编辑策略' : '添加策略'} open={modalOpen} onOk={handleSave} onCancel={() => setModalOpen(false)} width={700}>
        <Form form={form} layout="vertical">
          <Form.Item name="name" label="名称" rules={[{ required: true }]}><Input /></Form.Item>
          <Form.Item name="schedule_type" label="时段类型" rules={[{ required: true }]}>
            <Select options={SCHEDULE_OPTIONS.map((s) => ({ value: s, label: s }))} />
          </Form.Item>
          <Space>
            <Form.Item name="start_time" label="开始时间"><Input placeholder="09:00" /></Form.Item>
            <Form.Item name="end_time" label="结束时间"><Input placeholder="18:00" /></Form.Item>
          </Space>
          {ruleSelect('read_rule', 'READ 规则')}
          {ruleSelect('create_rule', 'CREATE 规则')}
          {ruleSelect('update_rule', 'UPDATE 规则')}
          {ruleSelect('delete_rule', 'DELETE 规则')}
          <Form.Item name="auto_escalation" label="自动升级" valuePropName="checked"><Switch /></Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
