// 权限管理页：用户集群授权 + 数据库授权（admin）+ 我的告警订阅
import { useEffect, useState } from 'react'
import {
  Tabs, Table, Button, Modal, Form, Input, Select, Space, Tag, message, Popconfirm, Switch, Empty,
} from 'antd'
import { PlusOutlined, BellOutlined, DeleteOutlined } from '@ant-design/icons'
import {
  getUsers, getClusters, getDBConnections,
  getUserClusterPerms, addUserClusterPerm, deleteUserClusterPerm,
  getUserDBPerms, addUserDBPerm, deleteUserDBPerm,
  getSubscriptions, addSubscription, deleteSubscription,
} from '../services/api'
import type { Cluster, User } from '../types'

type ClusterWithEnv = Cluster & { env_tag?: string }

interface ClusterPerm {
  id: number
  cluster_id?: number
  namespace?: string
  permission?: string
}

interface DBConn {
  id: number
  name: string
}

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
  const me = JSON.parse(localStorage.getItem('user') || '{}')
  const items = me.role === 'admin'
    ? [
        { key: 'cluster', label: '集群授权', children: <ClusterPerms /> },
        { key: 'db', label: '数据库授权', children: <DBPerms /> },
        { key: 'subs', label: '我的告警订阅', children: <MySubscriptions /> },
      ]
    : [{ key: 'subs', label: '我的告警订阅', children: <MySubscriptions /> }]
  return <Tabs defaultActiveKey={me.role === 'admin' ? 'cluster' : 'subs'} items={items} />
}

// ============ 集群授权（admin）============
function ClusterPerms() {
  const [users, setUsers] = useState<User[]>([])
  const [clusters, setClusters] = useState<ClusterWithEnv[]>([])
  const [selectedUser, setSelectedUser] = useState<number | null>(null)
  const [perms, setPerms] = useState<ClusterPerm[]>([])
  const [modalOpen, setModalOpen] = useState(false)
  const [form] = Form.useForm()

  useEffect(() => {
    getUsers().then((d) => setUsers(d || []))
    getClusters().then((d) => setClusters(d || []))
  }, [])

  const loadPerms = (uid: number) => getUserClusterPerms(uid).then((d) => setPerms((d || []) as ClusterPerm[]))

  const handleSelectUser = (uid: number) => {
    setSelectedUser(uid)
    loadPerms(uid)
  }

  const openModal = () => {
    form.resetFields()
    form.setFieldsValue({ permission: 'read', namespace: '' })
    setModalOpen(true)
  }

  const handleAdd = async () => {
    const values = await form.validateFields()
    await addUserClusterPerm(selectedUser!, values)
    message.success('已授权')
    setModalOpen(false)
    loadPerms(selectedUser!)
  }

  return (
    <div>
      <Space style={{ marginBottom: 16 }}>
        <span>选择用户：</span>
        <Select
          style={{ width: 220 }} placeholder="选择用户" onChange={handleSelectUser}
          options={users.map((u) => ({ label: `${u.username}（${u.role}）`, value: u.id }))}
        />
        {selectedUser && <Button type="primary" icon={<PlusOutlined />} onClick={openModal}>添加授权</Button>}
      </Space>
      {!selectedUser ? (
        <Empty description="请先选择一个用户查看其集群授权" />
      ) : (
        <Table rowKey="id" dataSource={perms} pagination={false}
          columns={[
            { title: '集群', dataIndex: 'cluster_id', render: (id) => clusters.find((c) => c.id === id)?.name || id },
            { title: '命名空间', dataIndex: 'namespace', render: (n) => n || '全部' },
            { title: '权限', dataIndex: 'permission', render: (p) => <Tag color={p === 'operate' ? 'green' : 'blue'}>{p}</Tag> },
            { title: '操作', render: (_, r) => (
              <Popconfirm title="撤销授权？" onConfirm={async () => { await deleteUserClusterPerm(r.id); loadPerms(selectedUser!) }}>
                <Button size="small" danger icon={<DeleteOutlined />}>撤销</Button>
              </Popconfirm>
            ) },
          ]} />
      )}

      <Modal title="添加集群授权" open={modalOpen} onOk={handleAdd} onCancel={() => setModalOpen(false)}>
        <Form form={form} layout="vertical">
          <Form.Item name="cluster_id" label="集群" rules={[{ required: true }]}>
            <Select options={clusters.map((c) => ({ label: `${c.name}（${c.env_tag}）`, value: c.id }))} />
          </Form.Item>
          <Form.Item name="namespace" label="命名空间（留空=全集群）"><Input /></Form.Item>
          <Form.Item name="permission" label="权限" rules={[{ required: true }]}>
            <Select options={[{ value: 'read', label: 'read（只读）' }, { value: 'operate', label: 'operate（可操作）' }]} />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}

// ============ 数据库授权（admin）============
function DBPerms() {
  const [users, setUsers] = useState<User[]>([])
  const [conns, setConns] = useState<DBConn[]>([])
  const [selectedUser, setSelectedUser] = useState<number | null>(null)
  const [perms, setPerms] = useState<DBPerm[]>([])
  const [modalOpen, setModalOpen] = useState(false)
  const [form] = Form.useForm()

  useEffect(() => {
    getUsers().then((d) => setUsers(d || []))
    getDBConnections().then((d) => setConns((d || []) as DBConn[]))
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
