// 项目管理页：项目（应用）作为授权单元，聚合多环境集群 namespace + 数据库实例，授权给用户
import { useEffect, useState } from 'react'
import { Card, Table, Button, Modal, Form, Input, Tabs, Drawer, Select, Space, Tag, Popconfirm, message } from 'antd'
import { PlusOutlined, DeleteOutlined, ClusterOutlined, DatabaseOutlined, TeamOutlined } from '@ant-design/icons'
import {
  getProjects, createProject, updateProject, deleteProject,
  getProjectClusterNS, addProjectClusterNS, deleteProjectClusterNS,
  getProjectDBs, addProjectDB, deleteProjectDB,
  getProjectUsers, addProjectUsers, deleteProjectUser,
  getClusters, getClusterNamespaces, getDBConnections, getUsers,
  getNotificationConfigs,
} from '../services/api'
import type { Cluster, DBConnection, NotificationConfig, User } from '../types'

interface Project {
  id: number
  name: string
  description?: string
  cluster_ns_count?: number
  db_count?: number
  user_count?: number
  notify_config_id?: number | null
  [key: string]: unknown
}

interface ProjectClusterNS {
  id: number
  cluster_name?: string
  namespace?: string
}

interface ProjectDB {
  id: number
  db_name?: string
}

interface ProjectUser {
  id: number
  user_id?: number
  username?: string
  permission?: string
}

export default function ProjectManagement() {
  const [projects, setProjects] = useState<Project[]>([])
  const [alertConfigs, setAlertConfigs] = useState<NotificationConfig[]>([])  // 告警机器人配置（项目组群候选）
  const [open, setOpen] = useState(false)
  const [editing, setEditing] = useState<Project | null>(null)
  const [detail, setDetail] = useState<Project | null>(null)
  const [form] = Form.useForm()

  const load = () => {
    getProjects().then((d) => setProjects((d || []) as Project[]))
    getNotificationConfigs().then((d) =>
      setAlertConfigs((d || []).filter((c) => c.scene === 'alert' || c.scene === 'both')))
  }
  useEffect(() => { load() }, [])

  const save = async () => {
    const v = await form.validateFields()
    if (editing) await updateProject(editing.id, v)
    else await createProject(v)
    message.success('已保存'); setOpen(false); load()
  }

  const handleDelete = async (id: number) => {
    await deleteProject(id); message.success('已删除')
    if (detail?.id === id) setDetail(null)
    load()
  }

  return (
    <div>
      <Card
        title="项目（应用）管理"
        extra={<Button type="primary" icon={<PlusOutlined />} onClick={() => { setEditing(null); form.resetFields(); setOpen(true) }}>新建项目</Button>}
      >
        <div style={{ marginBottom: 12, color: '#666' }}>
          一个项目聚合一个业务应用在多环境（不同集群）的指定 namespace 和数据库实例。授权给用户后，用户自动获得项目内全部资源的访问与诊断权限。
        </div>
        <Table rowKey="id" dataSource={projects} pagination={false}
          columns={[
            { title: '项目名称', dataIndex: 'name', width: 180 },
            { title: '描述', dataIndex: 'description', ellipsis: true },
            { title: '集群/namespace', dataIndex: 'cluster_ns_count', width: 130 },
            { title: '数据库实例', dataIndex: 'db_count', width: 100 },
            { title: '授权用户', dataIndex: 'user_count', width: 90 },
            { title: '项目组群', dataIndex: 'notify_config_id', width: 150,
              render: (v) => {
                const cfg = alertConfigs.find((c) => c.id === v)
                return v ? <Tag color="blue">{cfg?.name || `配置#${v}`}</Tag> : <span style={{ color: '#999' }}>未配置</span>
              } },
            { title: '操作', width: 250, render: (_, r) => (
              <Space>
                <Button size="small" type="primary" icon={<ClusterOutlined />} onClick={() => setDetail(r)}>资源与授权</Button>
                <Button size="small" onClick={() => { setEditing(r); form.setFieldsValue(r); setOpen(true) }}>编辑</Button>
                <Popconfirm title="确认删除项目？" onConfirm={() => handleDelete(r.id)}>
                  <Button size="small" danger>删除</Button>
                </Popconfirm>
              </Space>
            ) },
          ]} />
      </Card>

      <Modal title={editing ? '编辑项目' : '新建项目'} open={open} onOk={save} onCancel={() => setOpen(false)}>
        <Form form={form} layout="vertical">
          <Form.Item name="name" label="项目名称" rules={[{ required: true }]}><Input placeholder="如 电商系统" /></Form.Item>
          <Form.Item name="description" label="描述"><Input.TextArea rows={2} placeholder="业务应用描述" /></Form.Item>
          <Form.Item name="notify_config_id" label="项目组群（告警机器人）"
            tooltip="该项目 namespace 下的告警发送到此群。需先在「通知配置」添加用途为「告警机器人」的渠道。">
            <Select allowClear placeholder="选择项目组群（不选=告警走默认群）"
              options={alertConfigs.map((c) => ({ value: c.id, label: `${c.name}（${c.channel_type}）` }))} />
          </Form.Item>
        </Form>
      </Modal>

      {detail && (
        <Drawer title={`项目：${detail.name}`} open onClose={() => setDetail(null)} width={900}>
          <Tabs
            items={[
              { key: 'cluster', label: '集群/namespace（多环境）', children: <ClusterNSTab projectId={detail.id} /> },
              { key: 'db', label: '数据库实例', children: <DBTab projectId={detail.id} /> },
              { key: 'users', label: '成员（只读/可操作/管理员）', children: <UsersTab projectId={detail.id} /> },
            ]}
          />
        </Drawer>
      )}
    </div>
  )
}

// ============ 集群/namespace Tab ============
export function ClusterNSTab({ projectId }: { projectId: number }) {
  const [items, setItems] = useState<ProjectClusterNS[]>([])
  const [clusters, setClusters] = useState<Cluster[]>([])
  const [namespaces, setNamespaces] = useState<string[]>([])
  const [clusterId, setClusterId] = useState<number>()
  const [namespace, setNamespace] = useState<string>()

  const load = () => getProjectClusterNS(projectId).then((d) => setItems((d || []) as ProjectClusterNS[]))
  useEffect(() => { load(); getClusters().then((d) => setClusters(d || [])) }, [])

  const handleCluster = (cid: number) => {
    setClusterId(cid); setNamespace(undefined)
    getClusterNamespaces(cid).then((d) => setNamespaces(d || []))
  }

  const add = async () => {
    if (!clusterId || !namespace) { message.warning('请选择集群和 namespace'); return }
    await addProjectClusterNS(projectId, { cluster_id: clusterId, namespace })
    message.success('已关联'); setNamespace(undefined); load()
  }

  return (
    <div>
      <Space style={{ marginBottom: 16 }}>
        <Select placeholder="选择集群" style={{ width: 180 }} value={clusterId} onChange={handleCluster}
          options={clusters.map((c) => ({ value: c.id, label: c.name }))} />
        <Select placeholder="选择 namespace" style={{ width: 220 }} value={namespace} onChange={setNamespace} disabled={!namespaces.length}
          options={namespaces.map((n) => ({ value: n, label: n }))} />
        <Button type="primary" icon={<PlusOutlined />} onClick={add}>关联</Button>
      </Space>
      <Table rowKey="id" dataSource={items} size="small" pagination={false}
        columns={[
          { title: '集群', dataIndex: 'cluster_name' },
          { title: 'namespace', dataIndex: 'namespace' },
          { title: '操作', width: 80, render: (_, r) => (
            <Popconfirm title="解除关联？" onConfirm={async () => { await deleteProjectClusterNS(projectId, r.id); load() }}>
              <Button size="small" danger icon={<DeleteOutlined />} />
            </Popconfirm>
          ) },
        ]} />
    </div>
  )
}

// ============ 数据库实例 Tab ============
function DBTab({ projectId }: { projectId: number }) {
  const [items, setItems] = useState<ProjectDB[]>([])
  const [conns, setConns] = useState<DBConnection[]>([])
  const [connId, setConnId] = useState<number>()

  const load = () => getProjectDBs(projectId).then((d) => setItems((d || []) as ProjectDB[]))
  useEffect(() => { load(); getDBConnections().then((d) => setConns((d || []) as DBConnection[])) }, [])

  const add = async () => {
    if (!connId) { message.warning('请选择数据库实例'); return }
    await addProjectDB(projectId, { db_connection_id: connId })
    message.success('已关联'); setConnId(undefined); load()
  }

  return (
    <div>
      <Space style={{ marginBottom: 16 }}>
        <Select placeholder="选择数据库实例" style={{ width: 260 }} value={connId} onChange={setConnId} showSearch optionFilterProp="label"
          options={conns.map((c) => ({ value: c.id, label: `${c.name}（${c.db_type}@${c.host}）` }))} />
        <Button type="primary" icon={<PlusOutlined />} onClick={add}>关联</Button>
      </Space>
      <Table rowKey="id" dataSource={items} size="small" pagination={false}
        columns={[
          { title: '数据库实例', dataIndex: 'db_name' },
          { title: '操作', width: 80, render: (_, r) => (
            <Popconfirm title="解除关联？" onConfirm={async () => { await deleteProjectDB(projectId, r.id); load() }}>
              <Button size="small" danger icon={<DeleteOutlined />} />
            </Popconfirm>
          ) },
        ]} />
    </div>
  )
}

// ============ 授权用户 Tab ============
function UsersTab({ projectId }: { projectId: number }) {
  const [items, setItems] = useState<ProjectUser[]>([])
  const [users, setUsers] = useState<User[]>([])
  const [userIds, setUserIds] = useState<number[]>([])
  const [permission, setPermission] = useState('read')

  const load = () => getProjectUsers(projectId).then((d) => setItems((d || []) as ProjectUser[]))
  useEffect(() => { load(); getUsers().then((d) => setUsers(d || [])) }, [])

  // 待选用户 = 未授权的用户（已授权的不再出现在下拉里）
  const unassigned = users.filter((u) => !items.some((i) => i.user_id === u.id))

  const add = async () => {
    if (!userIds.length) { message.warning('请选择用户'); return }
    await addProjectUsers(projectId, { user_ids: userIds, permission })
    message.success(`已批量授权 ${userIds.length} 位用户`); setUserIds([]); load()
  }

  return (
    <div>
      <Space style={{ marginBottom: 16 }}>
        <Select mode="multiple" placeholder="选择用户（可多选）" style={{ minWidth: 280 }} value={userIds} onChange={setUserIds}
          showSearch optionFilterProp="label" maxTagCount="responsive"
          options={unassigned.map((u) => ({ value: u.id, label: u.username }))} />
        <Select style={{ width: 150 }} value={permission} onChange={setPermission}
          options={[
            { value: 'read', label: '只读' },
            { value: 'operate', label: '可操作' },
            { value: 'manager', label: '管理员' },
          ]} />
        <Button type="primary" icon={<PlusOutlined />} onClick={add} disabled={!userIds.length}>批量授权</Button>
      </Space>
      <Table rowKey="id" dataSource={items} size="small" pagination={false}
        columns={[
          { title: '用户', dataIndex: 'username' },
          { title: '权限', dataIndex: 'permission', render: (p) => {
            const map: Record<string, [string, string]> = {
              read: ['default', '只读'], operate: ['blue', '可操作'], manager: ['green', '管理员'],
            }
            const [color, label] = map[p] || ['default', p]
            return <Tag color={color}>{label}</Tag>
          } },
          { title: '操作', width: 80, render: (_, r) => (
            <Popconfirm title="解除授权？" onConfirm={async () => { await deleteProjectUser(projectId, r.id); load() }}>
              <Button size="small" danger icon={<DeleteOutlined />} />
            </Popconfirm>
          ) },
        ]} />
    </div>
  )
}
