// 主机/虚拟机纳管（N11 + R25）：SSH 采集进程监控；分组 + 分组权限
import { useEffect, useMemo, useState } from 'react'
import {
  Alert, Button, Card, Drawer, Empty, Form, Input, InputNumber, List, Menu, Modal, Popconfirm,
  Radio, Segmented, Select, Space, Steps, Switch, Table, Tag, Tooltip, message,
} from 'antd'
import { DeleteOutlined, EditOutlined, PlusOutlined, ReloadOutlined, SyncOutlined, TeamOutlined } from '@ant-design/icons'
import {
  getHosts, createHost, updateHost, deleteHost, checkHost,
  getHostGroups, createHostGroup, updateHostGroup, deleteHostGroup,
  getHostGroupMembers, addHostGroupMember, removeHostGroupMember, getUsers,
} from '../services/api'
import type { HostItem, HostCheckResult, HostGroup, HostGroupMember } from '../services/api'
import type { User } from '../types'

const STATUS: Record<string, { color: string; label: string }> = {
  online: { color: 'green', label: '在线' }, offline: { color: 'red', label: '离线' }, unknown: { color: 'default', label: '未知' },
}
const KIND: Record<string, { color: string; label: string }> = {
  host: { color: 'blue', label: '主机' }, vm: { color: 'purple', label: '虚拟机' },
}
const ROLE_LABEL: Record<string, string> = { admin: '组管理', operator: '操作', viewer: '只读' }

export default function HostManagement() {
  const [hosts, setHosts] = useState<HostItem[]>([])
  const [groups, setGroups] = useState<HostGroup[]>([])
  const [isAdmin, setIsAdmin] = useState(false)
  const [loading, setLoading] = useState(false)
  const [groupFilter, setGroupFilter] = useState<string>('all')
  const [kindFilter, setKindFilter] = useState<string>('')
  const [keyword, setKeyword] = useState('')

  const [modal, setModal] = useState(false)
  const [editing, setEditing] = useState<HostItem | null>(null)
  const [step, setStep] = useState(0)
  const [wizardKind, setWizardKind] = useState<'host' | 'vm'>('host')
  const [checking, setChecking] = useState<number | null>(null)
  const [checkResult, setCheckResult] = useState<HostCheckResult | null>(null)
  const [form] = Form.useForm()

  const [groupDrawer, setGroupDrawer] = useState(false)
  const [groupModalOpen, setGroupModalOpen] = useState(false)
  const [groupEdit, setGroupEdit] = useState<HostGroup | null>(null)
  const [groupForm] = Form.useForm()
  const [memberModal, setMemberModal] = useState<HostGroup | null>(null)

  // 当前用户可创建主机/虚拟机的组（operator|admin）
  const createableGroups = useMemo(
    () => groups.filter((g) => g.my_role === 'operator' || g.my_role === 'admin'),
    [groups],
  )

  const loadGroups = () => {
    getHostGroups().then((d) => { setGroups(d?.groups || []); setIsAdmin(!!d?.is_admin) }).catch(() => {})
  }

  const load = () => {
    setLoading(true)
    const params: Record<string, unknown> = {}
    if (groupFilter === 'ungrouped') params.group_id = 0
    else if (groupFilter !== 'all') params.group_id = Number(groupFilter)
    if (kindFilter) params.kind = kindFilter
    if (keyword.trim()) params.keyword = keyword.trim()
    getHosts(params).then((d) => setHosts(d || [])).catch(() => {}).finally(() => setLoading(false))
  }

  useEffect(() => { loadGroups() }, [])
  useEffect(() => { load() }, [groupFilter, kindFilter, keyword])

  const openCreate = () => {
    setEditing(null); setStep(0); setWizardKind('host')
    form.resetFields()
    form.setFieldsValue({ ssh_port: 22, ssh_user: 'root', enabled: true, process_paths: [] })
    setModal(true)
  }
  const openEdit = (h: HostItem) => {
    setEditing(h); setStep(1)
    form.resetFields()
    form.setFieldsValue({ ...h, process_paths: h.process_paths || [], group_id: h.group_id ?? undefined })
    setModal(true)
  }

  const save = async () => {
    const v = await form.validateFields()
    const payload: Record<string, unknown> = {
      ...v,
      process_paths: (v.process_paths || []).map((p: { name?: string; path?: string }) => ({ name: (p.name || p.path || '').trim(), path: (p.path || '').trim() })).filter((p: { path: string }) => p.path),
    }
    if (!editing) payload.kind = wizardKind
    if (editing) { await updateHost(editing.id, payload); message.success('已更新') }
    else { await createHost(payload); message.success('已添加') }
    setModal(false); load(); loadGroups()
  }

  const doCheck = async (id: number) => {
    setChecking(id)
    try { const r = await checkHost(id); setCheckResult(r.check) } catch { /* */ } finally { setChecking(null) }
  }

  const del = async (id: number) => { await deleteHost(id); message.success('已删除'); load(); loadGroups() }

  // ===== 分组管理 =====
  const saveGroup = async () => {
    const v = await groupForm.validateFields()
    if (groupEdit) { await updateHostGroup(groupEdit.id, v); message.success('已更新分组') }
    else { await createHostGroup(v); message.success('已新建分组') }
    setGroupEdit(null); setGroupModalOpen(false); loadGroups()
  }

  const delGroup = async (id: number) => { await deleteHostGroup(id); message.success('已删除分组'); loadGroups() }

  const menuItems = [
    { key: 'all', label: '全部主机' },
    { key: 'ungrouped', label: '未分组' },
    ...groups.map((g) => ({ key: String(g.id), label: `${g.name}（${g.host_count}）` })),
  ]

  const groupOptions = createableGroups.map((g) => ({ value: g.id, label: g.name }))

  const columns = [
    { title: '名称', dataIndex: 'name' },
    { title: '分组', width: 110, render: (_: unknown, r: HostItem) => r.group_name
      ? <Tag>{r.group_name}</Tag> : <Tag color="warning">未分组</Tag> },
    { title: '类型', width: 90, render: (_: unknown, r: HostItem) => <Tag color={KIND[r.kind]?.color}>{KIND[r.kind]?.label || r.kind}</Tag> },
    { title: '地址', render: (_: unknown, r: HostItem) => `${r.address}:${r.ssh_port}` },
    { title: '状态', dataIndex: 'status', width: 80, render: (s: string, r: HostItem) => {
      const tag = <Tag color={STATUS[s]?.color}>{STATUS[s]?.label || s}</Tag>
      return s === 'offline' && r.last_error ? <Tooltip title={r.last_error}>{tag}</Tooltip> : tag
    } },
    { title: '监控进程', width: 220, render: (_: unknown, r: HostItem) => (r.process_paths || []).map((p) => p.name || p.path).join('、') || '-' },
    { title: '启用', dataIndex: 'enabled', width: 70, render: (v: boolean) => v ? <Tag color="green">是</Tag> : <Tag>否</Tag> },
    { title: '操作', width: 170, render: (_: unknown, r: HostItem) => (
      <Space size="small">
        {r.can.operate && <Button size="small" type="link" loading={checking === r.id} icon={<SyncOutlined />} onClick={() => doCheck(r.id)}>检测</Button>}
        {r.can.operate && <Button size="small" type="link" onClick={() => openEdit(r)}>编辑</Button>}
        {r.can.delete && <Popconfirm title="删除该主机？" onConfirm={() => del(r.id)}><a style={{ color: '#cf1322' }}>删除</a></Popconfirm>}
      </Space>
    ) },
  ]

  const noPerm = !isAdmin && groups.length === 0

  return (
    <Card size="small" title="主机 / 虚拟机（非集群节点）"
      extra={<Space>
        <Button icon={<ReloadOutlined />} onClick={() => { load(); loadGroups() }}>刷新</Button>
        {isAdmin && <Button icon={<TeamOutlined />} onClick={() => setGroupDrawer(true)}>分组管理</Button>}
        {(isAdmin || createableGroups.length > 0) && <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>添加主机</Button>}
      </Space>}>
      <div style={{ display: 'flex', gap: 16, alignItems: 'flex-start' }}>
        {/* 左侧：分组树 + 类型 + 搜索 */}
        <div style={{ width: 200, flex: '0 0 200px' }}>
          <Menu
            mode="inline" style={{ border: 'none' }}
            selectedKeys={[groupFilter]}
            items={menuItems}
            onClick={({ key }) => setGroupFilter(key)}
          />
          <div style={{ padding: '8px 4px' }}>
            <Segmented block options={[{ label: '全部', value: '' }, { label: '主机', value: 'host' }, { label: '虚拟机', value: 'vm' }]} value={kindFilter} onChange={(v) => setKindFilter(v as string)} />
          </div>
          <Input.Search placeholder="搜索名称/地址" allowClear onSearch={(v) => setKeyword(v)} style={{ marginBottom: 8 }} />
        </div>

        {/* 右侧：列表 */}
        <div style={{ flex: 1, minWidth: 0 }}>
          {noPerm ? (
            <Alert type="info" showIcon message="暂无可用主机" description="请联系管理员为你分配主机组权限（组角色：只读 / 操作 / 组管理）。" />
          ) : (
            <Table rowKey="id" size="small" loading={loading} dataSource={hosts} pagination={false} columns={columns} />
          )}
        </div>
      </div>

      {checkResult && (
        <Card size="small" style={{ marginTop: 12 }} title={`检测结果${checkResult.online ? '' : '（SSH 失败）'}`}>
          {checkResult.error && <div style={{ color: '#cf1322' }}>{checkResult.error}</div>}
          <Table rowKey="path" size="small" pagination={false} dataSource={checkResult.paths}
            columns={[
              { title: '进程', dataIndex: 'name' },
              { title: '路径', dataIndex: 'path' },
              { title: '状态', dataIndex: 'running', render: (v: boolean) => <Tag color={v ? 'green' : 'red'}>{v ? '运行中' : '已停止'}</Tag> },
              { title: 'PID 数', dataIndex: 'pid_count', width: 80 },
              { title: 'CPU%', dataIndex: 'cpu_pct', width: 80 },
              { title: '内存(MB)', dataIndex: 'mem_mb', width: 100 },
            ]} />
        </Card>
      )}

      {/* 新建（两步向导）/ 编辑 */}
      <Modal title={editing ? '编辑主机' : '添加主机'} open={modal} onCancel={() => setModal(false)} width={640} destroyOnClose
        footer={editing ? undefined : step === 0
          ? [<Button key="c" onClick={() => setModal(false)}>取消</Button>, <Button key="n" type="primary" onClick={() => setStep(1)}>下一步</Button>]
          : [<Button key="b" onClick={() => setStep(0)}>上一步</Button>, <Button key="c" onClick={() => setModal(false)}>取消</Button>, <Button key="s" type="primary" onClick={save}>保存</Button>]}>
        {!editing && <Steps size="small" current={step} style={{ marginBottom: 16 }} items={[{ title: '选类型' }, { title: '填信息' }]} />}

        {!editing && step === 0 && (
          <div style={{ padding: '12px 0' }}>
            <div style={{ marginBottom: 8, color: '#666' }}>请选择要纳管的设备类型：</div>
            <Radio.Group value={wizardKind} onChange={(e) => setWizardKind(e.target.value)}>
              <Radio.Button value="host">主机（物理机）</Radio.Button>
              <Radio.Button value="vm">虚拟机</Radio.Button>
            </Radio.Group>
            <div style={{ marginTop: 12, color: '#888', fontSize: 12 }}>虚拟机创建后必须先归属某个分组；物理机可暂不分组。</div>
          </div>
        )}

        {(editing || step === 1) && (
          <Form form={form} layout="vertical">
            {!editing && wizardKind === 'vm' && createableGroups.length === 0 && (
              <Alert type="warning" showIcon style={{ marginBottom: 12 }} message="无可用分组" description="你还没有可管理的分组，请联系管理员建组并分配权限后再添加虚拟机。" />
            )}
            <Space.Compact block style={{ width: '100%' }}>
              <Form.Item name="name" label="名称" style={{ width: '34%' }} rules={[{ required: true }]}><Input placeholder="如 db-192.168.6.99" /></Form.Item>
              <Form.Item name="address" label="IP / 主机名" style={{ width: '34%' }} rules={[{ required: true }]}><Input placeholder="192.168.6.99" /></Form.Item>
              <Form.Item name="ssh_port" label="端口" style={{ width: '12%' }}><InputNumber min={1} max={65535} style={{ width: '100%' }} /></Form.Item>
              <Form.Item name="group_id" label="分组" style={{ width: '20%' }}
                rules={!editing && wizardKind === 'vm' ? [{ required: true, message: '虚拟机必须选分组' }] : []}>
                <Select allowClear={editing ? false : wizardKind === 'host'} placeholder="选择分组" options={groupOptions} disabled={editing ? (!isAdmin && !editing.can.manage) : false} />
              </Form.Item>
            </Space.Compact>
            <Space.Compact block style={{ width: '100%' }}>
              <Form.Item name="ssh_user" label="SSH 用户" style={{ width: '40%' }}><Input /></Form.Item>
              <Form.Item name="ssh_password" label="SSH 口令（可选，留空=用密钥）" style={{ width: '40%' }}><Input.Password placeholder={editing?.has_password ? '已设置（留空不变）' : ''} /></Form.Item>
              <Form.Item name="env_tag" label="环境标签" style={{ width: '20%' }}><Input placeholder="如 prod" /></Form.Item>
            </Space.Compact>
            <Form.Item name="ssh_key_path" label="私钥路径（可选，留空=默认 ~/.ssh/id_rsa）"><Input /></Form.Item>
            <Form.Item name="enabled" label="启用监控" valuePropName="checked"><Switch /></Form.Item>
            <Form.Item label="监控进程路径（一行一个服务：名称 + 命令行匹配路径）">
              <Form.List name="process_paths">
                {(fields, { add, remove }) => (
                  <>
                    {fields.map(({ key, name: idx }) => (
                      <Space key={key} style={{ display: 'flex', marginBottom: 4 }} align="baseline">
                        <Form.Item name={[idx, 'name']} style={{ marginBottom: 0 }}><Input placeholder="服务名" style={{ width: 140 }} /></Form.Item>
                        <Form.Item name={[idx, 'path']} rules={[{ required: true, message: '填路径' }]} style={{ marginBottom: 0 }}><Input placeholder="/usr/local/app/app.jar 或 mysqld" style={{ width: 300 }} /></Form.Item>
                        <a onClick={() => remove(idx)} style={{ color: '#cf1322' }}>删除</a>
                      </Space>
                    ))}
                    <Button size="small" type="dashed" onClick={() => add()} style={{ marginTop: 4 }}>+ 加一个进程</Button>
                  </>
                )}
              </Form.List>
            </Form.Item>
          </Form>
        )}
      </Modal>

      {/* 分组管理 */}
      <Drawer title="主机分组管理" open={groupDrawer} onClose={() => setGroupDrawer(false)} width={480}>
        {isAdmin && <Button type="primary" icon={<PlusOutlined />} block style={{ marginBottom: 12 }} onClick={() => { setGroupEdit(null); groupForm.resetFields(); setGroupModalOpen(true) }}>新建分组</Button>}
        <List
          dataSource={groups}
          renderItem={(g) => (
            <List.Item
              actions={[
                (g.my_role === 'admin' || isAdmin) && <a key="m" onClick={() => setMemberModal(g)}>成员</a>,
                (g.my_role === 'admin' || isAdmin) && <a key="e" onClick={() => { setGroupEdit(g); groupForm.setFieldsValue({ name: g.name, description: g.description, sort: g.sort }); setGroupModalOpen(true) }}>编辑</a>,
                isAdmin && !g.is_default && <Popconfirm key="d" title="删除分组？组内主机会转为未分组" onConfirm={() => delGroup(g.id)}><a style={{ color: '#cf1322' }}>删除</a></Popconfirm>,
              ].filter(Boolean)}
            >
              <List.Item.Meta
                title={<span>{g.name}{g.is_default && <Tag style={{ marginLeft: 6 }}>默认</Tag>}</span>}
                description={<span>{g.description || '—'} · 主机 {g.host_count} · 成员 {g.member_count}{g.my_role ? ` · 我的角色 ${ROLE_LABEL[g.my_role] || g.my_role}` : ''}</span>}
              />
            </List.Item>
          )}
        />
        <Modal title={groupEdit ? '编辑分组' : '新建分组'} open={groupModalOpen} onCancel={() => setGroupModalOpen(false)} onOk={saveGroup} destroyOnClose>
          <Form form={groupForm} layout="vertical">
            <Form.Item name="name" label="分组名" rules={[{ required: true }]}><Input /></Form.Item>
            <Form.Item name="description" label="描述"><Input /></Form.Item>
            <Form.Item name="sort" label="排序"><InputNumber style={{ width: '100%' }} /></Form.Item>
          </Form>
        </Modal>
      </Drawer>

      {/* 成员管理 */}
      <MemberModal group={memberModal} onClose={() => { setMemberModal(null); loadGroups() }} />
    </Card>
  )
}

function MemberModal({ group, onClose }: { group: HostGroup | null; onClose: () => void }) {
  const [members, setMembers] = useState<HostGroupMember[]>([])
  const [users, setUsers] = useState<User[]>([])
  const [userId, setUserId] = useState<number | null>(null)
  const [role, setRole] = useState('viewer')

  useEffect(() => {
    if (!group) return
    getHostGroupMembers(group.id).then(setMembers).catch(() => {})
    getUsers().then(setUsers).catch(() => {})
  }, [group])

  const add = async () => {
    if (!group || !userId) return
    await addHostGroupMember(group.id, { user_id: userId, role })
    message.success('已添加'); setUserId(null); setRole('viewer')
    getHostGroupMembers(group.id).then(setMembers).catch(() => {})
  }

  const remove = async (uid: number) => {
    if (!group) return
    await removeHostGroupMember(group.id, uid); message.success('已移除')
    getHostGroupMembers(group.id).then(setMembers).catch(() => {})
  }

  return (
    <Modal title={`「${group?.name}」成员`} open={!!group} onCancel={onClose} footer={null} destroyOnClose>
      <Space style={{ marginBottom: 12, width: '100%' }}>
        <Select style={{ width: 180 }} placeholder="选择用户" value={userId} onChange={setUserId}
          options={users.map((u) => ({ value: u.id, label: u.username }))} showSearch optionFilterProp="label" />
        <Select style={{ width: 110 }} value={role} onChange={setRole}
          options={[{ value: 'viewer', label: '只读' }, { value: 'operator', label: '操作' }, { value: 'admin', label: '组管理' }]} />
        <Button type="primary" disabled={!userId} onClick={add}>添加</Button>
      </Space>
      <List size="small" dataSource={members} renderItem={(m) => (
        <List.Item actions={[<a key="r" style={{ color: '#cf1322' }} onClick={() => remove(m.user_id)}>移除</a>]}>
          <span>{m.username}</span>
          <Tag style={{ marginLeft: 8 }} color={m.role === 'admin' ? 'blue' : m.role === 'operator' ? 'green' : 'default'}>{ROLE_LABEL[m.role] || m.role}</Tag>
        </List.Item>
      )} />
    </Modal>
  )
}
