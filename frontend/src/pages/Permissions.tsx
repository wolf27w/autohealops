// 权限管理页：按项目授权 / 按集群授权 / 按数据库授权（admin）+ 我的告警订阅
// 生效逻辑：直接授权（集群/数据库） ∪ 项目授权，取最高权限（见 user_permission_service）
import { useEffect, useState } from 'react'
import {
  Tabs, Table, Button, Modal, Form, Input, Select, Space, Tag, message, Popconfirm, Switch, Empty,
} from 'antd'
import { PlusOutlined, BellOutlined, DeleteOutlined } from '@ant-design/icons'
import {
  getUsers, getClusters, getDBConnections,
  getUserDBPerms, addUserDBPerm, deleteUserDBPerm,
  getProjects,
  getSubscriptions, addSubscription, deleteSubscription,
} from '../services/api'
import type { Cluster, DBConnection, User } from '../types'
import ProjectManagement, { ClusterNSTab } from './ProjectManagement'
import { getCurrentUser } from '../utils/auth'

interface DBPerm {
  id: number
  db_connection_id?: number
  permission?: string
}

interface Subscription {
  id: number
  cluster_id?: number
  namespace?: string
  subscribe_type?: string
  enabled?: boolean
}

export default function Permissions() {
  const me = getCurrentUser()
  const items = me.role === 'admin'
    ? [
        { key: 'project', label: '按项目授权', children: <ProjectManagement /> },
        { key: 'cluster', label: '按集群授权', children: <ClusterPerms /> },
        { key: 'db', label: '按数据库授权', children: <DBPerms /> },
        { key: 'subs', label: '我的告警订阅', children: <MySubscriptions /> },
      ]
    : [{ key: 'subs', label: '我的告警订阅', children: <MySubscriptions /> }]
  return <Tabs defaultActiveKey="project" items={items} />
}

// ============ 集群授权（admin）：按项目授权，项目内成员自动继承 ============
function ClusterPerms() {
  const [projects, setProjects] = useState<{ id: number; name: string }[]>([])
  const [projectId, setProjectId] = useState<number>()

  useEffect(() => {
    getProjects().then((d) => setProjects((d || []) as { id: number; name: string }[]))
  }, [])

  return (
    <div>
      <div style={{ marginBottom: 12, color: '#666' }}>
        按项目授权集群：选择一个项目，为其关联集群 / namespace；项目内成员（只读/可操作/管理员）自动获得对应 namespace 的访问与操作权限。
      </div>
      <Space style={{ marginBottom: 16 }}>
        <span>选择项目：</span>
        <Select style={{ width: 240 }} placeholder="选择项目" value={projectId} onChange={setProjectId} showSearch optionFilterProp="label"
          options={projects.map((p) => ({ label: p.name, value: p.id }))} />
      </Space>
      {!projectId ? (
        <Empty description="请先选择一个项目，为其授权集群 / namespace" />
      ) : (
        <ClusterNSTab projectId={projectId} />
      )}
    </div>
  )
}

// ============ 数据库授权（admin）============
function DBPerms() {
  const [users, setUsers] = useState<User[]>([])
  const [conns, setConns] = useState<DBConnection[]>([])
  const [selectedUser, setSelectedUser] = useState<number | null>(null)
  const [perms, setPerms] = useState<DBPerm[]>([])
  const [modalOpen, setModalOpen] = useState(false)
  const [form] = Form.useForm()

  useEffect(() => {
    getUsers().then((d) => setUsers(d || []))
    getDBConnections().then((d) => setConns((d || []) as DBConnection[]))
  }, [])

  const loadPerms = (uid: number) => getUserDBPerms(uid).then((d) => setPerms((d || []) as DBPerm[]))

  const handleSelectUser = (uid: number) => {
    setSelectedUser(uid)
    loadPerms(uid)
  }

  const handleAdd = async () => {
    const values = await form.validateFields()
    await addUserDBPerm(selectedUser!, values)
    message.success('已授权')
    setModalOpen(false)
    loadPerms(selectedUser!)
  }

  return (
    <div>
      <Space style={{ marginBottom: 16 }}>
        <span>选择用户：</span>
        <Select style={{ width: 220 }} placeholder="选择用户" onChange={handleSelectUser}
          options={users.map((u) => ({ label: `${u.username}（${u.role}）`, value: u.id }))} />
        {selectedUser && <Button type="primary" icon={<PlusOutlined />} onClick={() => { form.resetFields(); form.setFieldsValue({ permission: 'read' }); setModalOpen(true) }}>添加授权</Button>}
      </Space>
      {!selectedUser ? (
        <Empty description="请先选择一个用户查看其数据库授权" />
      ) : (
        <Table rowKey="id" dataSource={perms} pagination={false}
          columns={[
            { title: '数据库连接', dataIndex: 'db_connection_id', render: (id) => conns.find((c) => c.id === id)?.name || id },
            { title: '权限', dataIndex: 'permission', render: (p) => <Tag color={p === 'manage' ? 'green' : 'blue'}>{p}</Tag> },
            { title: '操作', render: (_, r) => (
              <Popconfirm title="撤销授权？" onConfirm={async () => { await deleteUserDBPerm(r.id); loadPerms(selectedUser!) }}>
                <Button size="small" danger icon={<DeleteOutlined />}>撤销</Button>
              </Popconfirm>
            ) },
          ]} />
      )}

      <Modal title="添加数据库授权" open={modalOpen} onOk={handleAdd} onCancel={() => setModalOpen(false)}>
        <Form form={form} layout="vertical">
          <Form.Item name="db_connection_id" label="数据库连接" rules={[{ required: true }]}>
            <Select options={conns.map((c) => ({ label: c.name, value: c.id }))} />
          </Form.Item>
          <Form.Item name="permission" label="权限" rules={[{ required: true }]}>
            <Select options={[{ value: 'read', label: 'read（只读）' }, { value: 'manage', label: 'manage（可管理）' }]} />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}

// ============ 我的告警订阅 ============
function MySubscriptions() {
  const [subs, setSubs] = useState<Subscription[]>([])
  const [clusters, setClusters] = useState<Cluster[]>([])
  const [modalOpen, setModalOpen] = useState(false)
  const [form] = Form.useForm()

  const load = () => getSubscriptions().then((d) => setSubs((d || []) as Subscription[]))
  useEffect(() => {
    load()
    getClusters().then((d) => setClusters(d || []))
  }, [])

  const handleAdd = async () => {
    const values = await form.validateFields()
    await addSubscription(values)
    message.success('已订阅')
    setModalOpen(false)
    load()
  }

  return (
    <div>
      <Button type="primary" icon={<BellOutlined />} style={{ marginBottom: 16 }} onClick={() => { form.resetFields(); form.setFieldsValue({ subscribe_type: 'both' }); setModalOpen(true) }}>
        添加订阅
      </Button>
      <Table rowKey="id" dataSource={subs} pagination={false}
        columns={[
          { title: '集群', dataIndex: 'cluster_id', render: (id) => clusters.find((c) => c.id === id)?.name || id },
          { title: '命名空间', dataIndex: 'namespace', render: (n) => n || '全部' },
          { title: '订阅类型', dataIndex: 'subscribe_type', render: (t) => <Tag>{t}</Tag> },
          { title: '状态', dataIndex: 'enabled', render: (v) => (v ? '启用' : '停用') },
          { title: '操作', render: (_, r) => (
            <Popconfirm title="取消订阅？" onConfirm={async () => { await deleteSubscription(r.id); load() }}>
              <Button size="small" danger>取消</Button>
            </Popconfirm>
          ) },
        ]} />

      <Modal title="添加告警订阅" open={modalOpen} onOk={handleAdd} onCancel={() => setModalOpen(false)}>
        <Form form={form} layout="vertical">
          <Form.Item name="cluster_id" label="集群" rules={[{ required: true }]}>
            <Select options={clusters.map((c) => ({ label: c.name, value: c.id }))} />
          </Form.Item>
          <Form.Item name="namespace" label="命名空间（留空=全集群）"><Input /></Form.Item>
          <Form.Item name="subscribe_type" label="订阅类型" rules={[{ required: true }]}>
            <Select options={[
              { value: 'env', label: '环境告警（故障）' },
              { value: 'log', label: '日志告警' },
              { value: 'both', label: '两者都订' },
            ]} />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
