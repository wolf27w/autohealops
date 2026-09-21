// 审批流程管理页：自定义审批流程（节点序列：值班人/@群所有人/管理员/指定用户）
import { useEffect, useState } from 'react'
import {
  Card, Table, Button, Modal, Form, Input, Select, InputNumber, Switch,
  Space, Tag, Popconfirm, message, Empty,
} from 'antd'
import type { FormInstance, FormListFieldData } from 'antd'
import { PlusOutlined, ArrowUpOutlined, ArrowDownOutlined, DeleteOutlined, ReloadOutlined } from '@ant-design/icons'
import {
  getApprovalFlows, createApprovalFlow, updateApprovalFlow,
  deleteApprovalFlow, resetDefaultApprovalFlow, getNotificationConfigs, getUsers,
} from '../services/api'
import type { NotificationConfig } from '../types'

const NODE_TYPE_OPTIONS = [
  { value: 'duty', label: '值班人' },
  { value: 'group_all', label: '@群所有人' },
  { value: 'user', label: '指定用户' },
]

const NODE_TYPE_COLOR: Record<string, string> = {
  duty: 'blue', group_all: 'orange', admin: 'purple', user: 'cyan',
}

const DEFAULT_NODE = { type: 'duty', timeout: 40, approve_count: 0, on_empty: 'next', group_id: 0, notify_config_id: 0, at_duty: false, include_admin: true, users: [] }

interface ApprovalFlowNode {
  type: string
  timeout?: number
  approve_count?: number
  on_empty?: string
  group_id?: number
  notify_config_id?: number
  at_duty?: boolean
  include_admin?: boolean
  users?: string[]
}

interface ApprovalFlow {
  id: number
  name: string
  description?: string
  is_active?: boolean
  is_default?: boolean
  nodes?: ApprovalFlowNode[]
}

function NodeFields({ form, field, index, configs }: { form: FormInstance; field: FormListFieldData; index: number; configs: NotificationConfig[] }) {
  return (
    <Form.Item noStyle shouldUpdate={(prev, cur) =>
      prev.nodes?.[index]?.type !== cur.nodes?.[index]?.type
    }>
      {({ getFieldValue }) => {
        const type = getFieldValue(['nodes', index, 'type'])
        return (
          <Space direction="vertical" style={{ width: '100%' }} size={4}>
            <Space wrap>
              <Form.Item name={[field.name, 'notify_config_id']} label="发送群（通知配置）" style={{ marginBottom: 8 }}
                tooltip="审批消息发到哪个通知配置的群（显示通知配置名称）；不选=按用途自动路由到通知配置中的值班/管理员机器人">
                <Select allowClear style={{ width: 240 }} placeholder="选择通知配置"
                  options={configs.map((c) => ({ value: c.id, label: `${c.name}（${c.channel_type}）` }))} />
              </Form.Item>
              {type !== 'group_all' && (
                <Form.Item name={[field.name, 'at_duty']} label="@值班人" valuePropName="checked" style={{ marginBottom: 8 }}
                  tooltip="群消息中 @ 当天值班的审核人员（需用户在用户管理中配置飞书 open_id）">
                  <Switch />
                </Form.Item>
              )}
            </Space>
            {type === 'duty' && (
              <div style={{ fontSize: 12, color: '#999' }}>
                当天值班人审批；无值班人时自动落到下一节点（@群所有人 + 管理员）
              </div>
            )}
            {type === 'group_all' && (
              <>
                <Form.Item name={[field.name, 'include_admin']} label="同时通知管理员" valuePropName="checked"
                  style={{ marginBottom: 8 }}><Switch /></Form.Item>
                <div style={{ fontSize: 12, color: '#999' }}>@群所有人（钉钉 isAtAll / 企微 @all），审批在群里回复或点按钮</div>
              </>
            )}
            {type === 'user' && (
              <>
                <Form.Item name={[field.name, 'users']} label="指定用户（从平台用户选择）" style={{ marginBottom: 4 }}>
                  <Select mode="multiple" placeholder="选择用户"
                    options={users.map((u) => ({
                      value: u.username,
                      label: `${u.username}${u.feishu_open_id ? '' : '（未绑飞书 open_id，群里 @ 不到）'}`,
                    }))} />
                </Form.Item>
                <div style={{ fontSize: 12, color: '#999', marginBottom: 8 }}>
                  只能选「用户管理」里**存在**的用户；未填飞书 open_id 的用户，群消息 @ 不到（个人消息也发不出）。
                </div>
              </>
            )}
          </Space>
        )
      }}
    </Form.Item>
  )
}

export default function ApprovalFlows() {
  const [flows, setFlows] = useState<ApprovalFlow[]>([])
  const [configs, setConfigs] = useState<NotificationConfig[]>([])
  const [users, setUsers] = useState<Array<{ username: string; feishu_open_id?: string }>>([])
  const [open, setOpen] = useState(false)
  const [editing, setEditing] = useState<ApprovalFlow | null>(null)
  const [form] = Form.useForm()
  const [saving, setSaving] = useState(false)

  const load = () => {
    getApprovalFlows().then((d) => setFlows((d || []) as ApprovalFlow[]))
    getNotificationConfigs().then((d) => setConfigs(d || []))
    getUsers().then((d) => setUsers((d || []) as Array<{ username: string; feishu_open_id?: string }>)).catch(() => {})
  }
  useEffect(() => { load() }, [])

  const openCreate = () => {
    setEditing(null)
    form.resetFields()
    form.setFieldsValue({
      nodes: [
        { ...DEFAULT_NODE },
        { type: 'group_all', timeout: 0, approve_count: 0, group_id: 0, at_duty: false, include_admin: true },
      ],
      is_active: true,
    })
    setOpen(true)
  }

  const openEdit = (f: ApprovalFlow) => {
    setEditing(f)
    form.resetFields()
    form.setFieldsValue({
      name: f.name,
      description: f.description,
      is_active: f.is_active,
      nodes: (f.nodes || []).map((n) => ({ ...DEFAULT_NODE, ...n })),
    })
    setOpen(true)
  }

  const handleSubmit = async () => {
    const values = await form.validateFields()
    const nodes = (values.nodes || []).map((n: ApprovalFlowNode) => ({
      type: n.type,
      timeout: n.timeout ?? 0,
      approve_count: n.approve_count || 0,
      on_empty: n.type === 'duty' ? 'next' : undefined,
      group_id: n.group_id || 0,
      notify_config_id: n.notify_config_id || 0,
      at_duty: n.type !== 'group_all' ? !!n.at_duty : undefined,
      include_admin: n.type === 'group_all' ? !!n.include_admin : undefined,
      users: n.type === 'user' ? (n.users || []) : undefined,
    }))
    const payload = { name: values.name, description: values.description, nodes, is_active: values.is_active ?? true }
    setSaving(true)
    try {
      if (editing) await updateApprovalFlow(editing.id, payload)
      else await createApprovalFlow(payload)
      message.success('已保存')
      setOpen(false)
      load()
    } finally {
      setSaving(false)
    }
  }

  const handleDelete = async (f: ApprovalFlow) => {
    await deleteApprovalFlow(f.id)
    message.success('已删除')
    load()
  }

  const handleResetDefault = async () => {
    await resetDefaultApprovalFlow()
    message.success('默认流程已恢复')
    load()
  }

  const nodeSummary = (nodes: ApprovalFlowNode[]) =>
    (nodes || []).map((n) => NODE_TYPE_OPTIONS.find((o) => o.value === n.type)?.label || n.type).join(' → ')

  return (
    <div>
      <Card
        title="审批流程"
        extra={
          <Space>
            <Button icon={<ReloadOutlined />} onClick={handleResetDefault}>恢复默认流程</Button>
            <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>新建流程</Button>
          </Space>
        }
      >
        <div style={{ marginBottom: 12, color: '#666' }}>
          审批按流程节点逐级推进：当前节点到「提醒时间」未处理 → 自动通知下一节点（默认：值班人 40 分钟未处理 → @群所有人）。
          **审批没有「超时」一说**：到最后一个节点后只通知、不再推进，单子一直待审批；关联告警自愈/已解决时自动解除。
          默认流程不可删除。
        </div>
        <Table rowKey="id" dataSource={flows} pagination={false} locale={{ emptyText: <Empty description="暂无流程" /> }}
          columns={[
            { title: '流程名称', dataIndex: 'name', width: 220, render: (v, r) => (
              <Space>
                {v}
                {r.is_default && <Tag color="gold">默认</Tag>}
                {r.is_active ? <Tag color="green">启用</Tag> : <Tag>停用</Tag>}
              </Space>
            ) },
            { title: '节点序列', dataIndex: 'nodes', render: (nodes) => (
              <span style={{ fontSize: 12 }}>{nodeSummary(nodes)}</span>
            ) },
            { title: '描述', dataIndex: 'description', ellipsis: true },
            { title: '操作', width: 180, render: (_, r) => (
              <Space>
                <Button size="small" onClick={() => openEdit(r)}>编辑</Button>
                <Popconfirm title={r.is_default ? '默认流程不可删除' : '确认删除该流程？'} disabled={r.is_default}
                  onConfirm={() => handleDelete(r)}>
                  <Button size="small" danger disabled={r.is_default}>删除</Button>
                </Popconfirm>
              </Space>
            ) },
          ]} />
      </Card>

      <Modal title={editing ? '编辑审批流程' : '新建审批流程'} open={open} onOk={handleSubmit}
        confirmLoading={saving} onCancel={() => setOpen(false)} width={720}>
        <Form form={form} layout="vertical">
          <Form.Item name="name" label="流程名称" rules={[{ required: true }]}><Input placeholder="如 高危变更审批流程" /></Form.Item>
          <Form.Item name="description" label="描述"><Input.TextArea rows={2} /></Form.Item>

          <div style={{ marginBottom: 8, fontWeight: 'bold' }}>审批节点（按顺序逐级升级）</div>
          <Form.List name="nodes">
            {(fields, { add, remove, move }) => (
              <>
                {fields.map((field, index) => (
                  <Card key={field.key} size="small" style={{ marginBottom: 8 }}
                    title={
                      <Space>
                        <span>节点 {index + 1}</span>
                        <Tag color={NODE_TYPE_COLOR[form.getFieldValue(['nodes', index, 'type'])] || 'default'}>
                          {NODE_TYPE_OPTIONS.find((o) => o.value === form.getFieldValue(['nodes', index, 'type']))?.label}
                        </Tag>
                      </Space>
                    }
                    extra={
                      <Space>
                        <Button size="small" icon={<ArrowUpOutlined />} disabled={index === 0} onClick={() => move(index, index - 1)} />
                        <Button size="small" icon={<ArrowDownOutlined />} disabled={index === fields.length - 1} onClick={() => move(index, index + 1)} />
                        <Button size="small" danger icon={<DeleteOutlined />} onClick={() => remove(field.name)} />
                      </Space>
                    }>
                    <Space wrap style={{ width: '100%' }} align="start">
                      <Form.Item name={[field.name, 'type']} label="节点类型" rules={[{ required: true }]} style={{ marginBottom: 8 }}>
                        <Select style={{ width: 140 }} options={NODE_TYPE_OPTIONS} />
                      </Form.Item>
                      <Form.Item name={[field.name, 'timeout']} label="提醒时间(分钟)" tooltip="到达后推送到下一节点并重新通知（如值班人 40 分钟未处理 → @群所有人）。最后一个节点只通知、不再推进；审批无超时，关联告警恢复则自动解除。" style={{ marginBottom: 8 }}>
                        <InputNumber min={0} max={1440} style={{ width: 100 }} />
                      </Form.Item>
                      <Form.Item name={[field.name, 'approve_count']} label="需批准人数" tooltip="0=按决策级别（approval_1 单人 / approval_2 双人）" style={{ marginBottom: 8 }}>
                        <InputNumber min={0} max={5} style={{ width: 100 }} />
                      </Form.Item>
                    </Space>
                    <NodeFields form={form} field={field} index={index} configs={configs} />
                  </Card>
                ))}
                <Button type="dashed" block icon={<PlusOutlined />} onClick={() => add({ ...DEFAULT_NODE })}>
                  添加节点
                </Button>
              </>
            )}
          </Form.List>

          {!editing && (
            <Form.Item name="is_active" label="启用" valuePropName="checked" style={{ marginTop: 12 }}>
              <Switch />
            </Form.Item>
          )}
          {editing && !form.getFieldValue('is_default') && (
            <Form.Item name="is_active" label="启用" valuePropName="checked" style={{ marginTop: 12 }}>
              <Switch />
            </Form.Item>
          )}
        </Form>
      </Modal>
    </div>
  )
}
