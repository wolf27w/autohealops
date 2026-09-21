// 用户管理页
import { useEffect, useState } from 'react'
import { Table, Button, Modal, Form, Input, Select, Space, Tag, Popconfirm, message } from 'antd'
import { PlusOutlined } from '@ant-design/icons'
import { getUsers, createUser, updateUser, deleteUser, regenerateAPIKey } from '../services/api'
import type { User } from '../types'

export default function UserManagement() {
  const [users, setUsers] = useState<User[]>([])
  const [modalOpen, setModalOpen] = useState(false)
  const [editing, setEditing] = useState<User | null>(null)
  const [form] = Form.useForm()

  const load = () => getUsers().then((d) => setUsers(d))
  useEffect(() => { load() }, [])

  const openModal = (u?: User) => {
    setEditing(u || null)
    setModalOpen(true)
    if (u) form.setFieldsValue(u)
  }

  const handleSave = async () => {
    const values = await form.validateFields()
    if (editing) await updateUser(editing.id, values)
    else await createUser(values)
    message.success('已保存')
    setModalOpen(false)
    form.resetFields()
    load()
  }

  const roleColor: Record<string, string> = { admin: 'red', operator: 'blue', viewer: 'default', developer: 'cyan' }

  return (
    <div>
      <Button type="primary" icon={<PlusOutlined />} style={{ marginBottom: 16 }} onClick={() => openModal()}>创建用户</Button>
      <Table rowKey="id" dataSource={users}
        columns={[
          { title: '用户名', dataIndex: 'username' },
          { title: '邮箱', dataIndex: 'email' },
          { title: '角色', dataIndex: 'role', render: (r) => <Tag color={roleColor[r]}>{r}</Tag> },
          { title: '状态', dataIndex: 'is_active', render: (v) => (v ? '启用' : '停用') },
          { title: '手机号', dataIndex: 'phone' },
          { title: '飞书 user_id', dataIndex: 'feishu_user_id' },
          { title: '飞书 open_id', dataIndex: 'feishu_open_id' },
          { title: '钉钉ID', dataIndex: 'dingtalk_user_id' },
          { title: '操作', render: (_, r) => (
            <Space>
              <Button size="small" onClick={() => openModal(r)}>编辑</Button>
              <Button size="small" onClick={async () => { const d = (await regenerateAPIKey(r.id)) as { api_key?: string }; message.success(`新 API Key: ${d.api_key}`) }}>重置API Key</Button>
              <Popconfirm title="确认删除？" onConfirm={async () => { await deleteUser(r.id); load() }}>
                <Button size="small" danger>删除</Button>
              </Popconfirm>
            </Space>
          )},
        ]} />
      <Modal title={editing ? '编辑用户' : '创建用户'} open={modalOpen} onOk={handleSave} onCancel={() => setModalOpen(false)}>
        <Form form={form} layout="vertical">
          <Form.Item name="username" label="用户名" rules={[{ required: true }]}><Input disabled={!!editing} /></Form.Item>
          {!editing && <Form.Item name="password" label="密码" rules={[{ required: true }]}><Input.Password /></Form.Item>}
          <Form.Item name="email" label="邮箱"><Input /></Form.Item>
          <Form.Item name="role" label="角色" rules={[{ required: true }]}>
            <Select options={['admin', 'operator', 'viewer', 'developer'].map((r) => ({ value: r, label: r }))} />
          </Form.Item>
          <Form.Item name="dingtalk_user_id" label="钉钉 user ID"><Input /></Form.Item>
          <Form.Item name="wechat_userid" label="企微 userid"><Input /></Form.Item>
          <Form.Item name="phone" label="手机号"><Input /></Form.Item>
          <Form.Item name="feishu_user_id" label="飞书 user_id（通知到个人用）"><Input /></Form.Item>
          <Form.Item name="feishu_open_id" label="飞书 open_id（群内 @ 与审批回复身份映射用）"><Input /></Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
