// 消息群管理页：审批应用群（chat_id，走 IM 应用 API 可 @人/收消息）+ 告警 webhook 群
import { useEffect, useState } from 'react'
import { Card, Table, Button, Modal, Form, Input, Select, Switch, Space, Tag, Popconfirm, message } from 'antd'
import { PlusOutlined } from '@ant-design/icons'
import { getIMGroups, createIMGroup, updateIMGroup, deleteIMGroup } from '../services/api'

const CHANNEL_OPTIONS = [
  { value: 'feishu', label: '飞书' },
  { value: 'dingtalk', label: '钉钉' },
  { value: 'wechat', label: '企业微信' },
]

interface IMGroup {
  id: number
  name: string
  channel_type: string
  chat_id?: string
  webhook_url?: string
  is_active: boolean
}

export default function IMGroups() {
  const [groups, setGroups] = useState<IMGroup[]>([])
  const [open, setOpen] = useState(false)
  const [editing, setEditing] = useState<IMGroup | null>(null)
  const [form] = Form.useForm()

  const load = () => getIMGroups().then((d) => setGroups((d || []) as IMGroup[]))
  useEffect(() => { load() }, [])

  const save = async () => {
    const v = await form.validateFields()
    if (editing) await updateIMGroup(editing.id, v)
    else await createIMGroup(v)
    message.success('已保存'); setOpen(false); load()
  }

  return (
    <div>
      <Card
        title="消息群"
        extra={<Button type="primary" icon={<PlusOutlined />} onClick={() => { setEditing(null); form.resetFields(); form.setFieldsValue({ channel_type: 'feishu', is_active: true }); setOpen(true) }}>新建群</Button>}
      >
        <div style={{ marginBottom: 12, color: '#666' }}>
          <b>审批群</b>（应用群）：填飞书/钉钉群的 chat_id，消息走 IM 应用 API 发出，支持 @值班人、接收群内审批回复（长连接闭环）。
          <br />
          <b>告警群</b>（webhook 群）：填群机器人 webhook URL，单向通知。两种至少填一种。
        </div>
        <Table rowKey="id" dataSource={groups} pagination={false}
          columns={[
            { title: '群名称', dataIndex: 'name', width: 180, render: (v, r) => (
              <Space>
                {v}
                {r.is_active ? <Tag color="green">启用</Tag> : <Tag>停用</Tag>}
              </Space>
            ) },
            { title: '渠道', dataIndex: 'channel_type', width: 100,
              render: (t) => <Tag color={t === 'feishu' ? 'blue' : t === 'dingtalk' ? 'orange' : 'green'}>{CHANNEL_OPTIONS.find((o) => o.value === t)?.label || t}</Tag> },
            { title: 'chat_id（应用群）', dataIndex: 'chat_id', render: (v) => v || <span style={{ color: '#999' }}>-</span> },
            { title: 'webhook（告警群）', dataIndex: 'webhook_url', ellipsis: true, render: (v) => v || <span style={{ color: '#999' }}>-</span> },
            { title: '操作', width: 150, render: (_, r) => (
              <Space>
                <Button size="small" onClick={() => { setEditing(r); form.setFieldsValue(r); setOpen(true) }}>编辑</Button>
                <Popconfirm title="确认删除该群？" onConfirm={async () => { await deleteIMGroup(r.id); message.success('已删除'); load() }}>
                  <Button size="small" danger>删除</Button>
                </Popconfirm>
              </Space>
            ) },
          ]} />
      </Card>

      <Modal title={editing ? '编辑群' : '新建群'} open={open} onOk={save} onCancel={() => setOpen(false)}>
        <Form form={form} layout="vertical">
          <Form.Item name="name" label="群名称" rules={[{ required: true }]}><Input placeholder="如 值班群 / 运维告警群" /></Form.Item>
          <Form.Item name="channel_type" label="渠道" rules={[{ required: true }]}>
            <Select options={CHANNEL_OPTIONS} disabled={!!editing} />
          </Form.Item>
          <Form.Item name="chat_id" label="chat_id（应用群）"
            tooltip="飞书群 chat_id（oc_xxx）或钉钉 conversationId。填了消息走应用 API（可 @人、可收审批回复）；不填则该群只能用 webhook">
            <Input placeholder="如 oc_xxxxx（审批应用群必填）" />
          </Form.Item>
          <Form.Item name="webhook_url" label="Webhook URL（告警群）">
            <Input placeholder="如 https://open.feishu.cn/open-apis/bot/v2/hook/xxx（告警群）" />
          </Form.Item>
          <Form.Item name="is_active" label="启用" valuePropName="checked"><Switch /></Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
