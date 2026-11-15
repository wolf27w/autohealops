// 数据库资源列表页（资源中心 → 数据库）：纳管实例的列表 + 筛选 + 增删改 + 采集/巡检状态
import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import type { DBConnection } from '../types'
import {
  Card, Button, Table, Modal, Form, Input, Select, AutoComplete, InputNumber,
  Space, Tag, message, Popconfirm, Dropdown, Empty, Switch, Tooltip, Spin, Segmented,
} from 'antd'
import { PlusOutlined, ReloadOutlined, SearchOutlined, MoreOutlined, CodeOutlined } from '@ant-design/icons'
import DbInstanceTree, { type DbTreeConn } from '../components/db/DbInstanceTree'
import DbInstanceTabs from '../components/db/DbInstanceTabs'
import {
  getDBConnections, createDBConnection, updateDBConnection,
  deleteDBConnection, testDBConnection, collectSlowSQL, listDbGroups,
} from '../services/api'
import { isAdmin } from '../utils/auth'

// 本地行类型：后端返回字段（types.ts 里的 DBConnection 未覆盖这些，这里补全）
interface DbRow extends DBConnection {
  source?: string            // direct | agent
  cluster_id?: number | null
  agent_db_key?: string
  last_error?: string
  last_seen_at?: string
  env_tag?: string
  collect_interval?: number
  username?: string
}

const DB_TYPE: Record<string, { color: string; label: string }> = {
  mysql: { color: 'blue', label: 'MySQL' },
  pg: { color: 'green', label: 'PostgreSQL' },
  oracle: { color: 'orange', label: 'Oracle' },
}
const STATUS: Record<string, { color: string; label: string }> = {
  connected: { color: 'green', label: '已连接' },
  failed: { color: 'orange', label: '采集失败' },
  unknown: { color: 'default', label: '未知' },
  removed: { color: 'red', label: '已下线' },
}

/** 时间戳转本地可读（无时区/非法值原样返回） */
function fmtTime(s?: string): string {
  if (!s) return '-'
  const d = new Date(s)
  if (Number.isNaN(d.getTime())) return s
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

export default function DbResourceList() {
  const navigate = useNavigate()
  const admin = isAdmin()
  const [conns, setConns] = useState<DbRow[]>([])
  const [groups, setGroups] = useState<{ id: number; name: string }[]>([])
  const [loading, setLoading] = useState(false)

  // 视图模式（列表 / 树），记住到 localStorage（默认列表）
  const [viewMode, setViewMode] = useState<'list' | 'tree'>(() => (
    localStorage.getItem('db_view_mode') === 'tree' ? 'tree' : 'list'
  ))
  // 树视图：已打开实例页签 + 当前激活页签
  const [openIds, setOpenIds] = useState<number[]>([])
  const [activeId, setActiveId] = useState<number | null>(null)

  const changeViewMode = (m: 'list' | 'tree') => {
    setViewMode(m)
    localStorage.setItem('db_view_mode', m)
  }
  // 树：点击实例 → 打开页签并激活
  const openInstance = (id: number) => {
    setOpenIds((prev) => (prev.includes(id) ? prev : [...prev, id]))
    setActiveId(id)
  }
  // 树：关闭页签（关闭当前页签时切到相邻页签）
  const closeInstance = (id: number) => {
    const idx = openIds.indexOf(id)
    const next = openIds.filter((x) => x !== id)
    setOpenIds(next)
    if (activeId === id) {
      setActiveId(next.length ? next[Math.min(idx, next.length - 1)] : null)
    }
  }

  // 筛选状态
  const [kwInput, setKwInput] = useState('')   // 输入框即时值，300ms 防抖写 keyword
  const [keyword, setKeyword] = useState('')
  const [typeFilter, setTypeFilter] = useState('all')
  const [groupFilter, setGroupFilter] = useState('all')
  const [envFilter, setEnvFilter] = useState<string | undefined>(undefined)
  const [statusFilter, setStatusFilter] = useState('all')
  const [collectFilter, setCollectFilter] = useState('all')

  // 弹窗表单
  const [modalOpen, setModalOpen] = useState(false)
  const [editing, setEditing] = useState<DbRow | null>(null)
  const [form] = Form.useForm()
  const dbType: string = Form.useWatch('db_type', form) || 'mysql'
  const oracleMode: string = Form.useWatch('connect_mode', form) || 'service_name'

  const load = () => {
    setLoading(true)
    getDBConnections()
      .then((d) => setConns((d || []) as DbRow[]))
      .catch(() => {})
      .finally(() => setLoading(false))
  }
  const loadGroups = () => listDbGroups().then((d) => setGroups((d || []) as { id: number; name: string }[])).catch(() => setGroups([]))

  useEffect(() => { load() }, [])
  useEffect(() => { loadGroups() }, [])
  // 搜索防抖
  useEffect(() => {
    const t = setTimeout(() => setKeyword(kwInput.trim()), 300)
    return () => clearTimeout(t)
  }, [kwInput])

  // ===== 表单逻辑（从 DiagnosticCenter 的 DBConnections 搬，不改原文件） =====
  const onTypeChange = (t: string) => {
    form.setFieldsValue({
      db_type: t,
      port: t === 'oracle' ? 1521 : t === 'pg' ? 5432 : 3306,
      database: undefined,
      connect_mode: t === 'oracle' ? 'service_name' : undefined,
      service_name: undefined,
      sid: undefined,
      tns_descriptor: undefined,
      oracle_role: undefined,
    })
  }
  const onOracleModeChange = (m: string) => {
    form.setFieldsValue({
      connect_mode: m,
      service_name: m === 'service_name' ? form.getFieldValue('service_name') : undefined,
      sid: m === 'sid' ? form.getFieldValue('sid') : undefined,
      tns_descriptor: m === 'tns' ? form.getFieldValue('tns_descriptor') : undefined,
      port: form.getFieldValue('port') || 1521,
    })
  }

  const openModal = (c?: DbRow) => {
    loadGroups()
    setEditing(c || null)
    setModalOpen(true)
    if (c) {
      form.setFieldsValue({
        ...c,
        password: '',
        connect_mode: c.db_type === 'oracle' ? ((c.connect_mode as string) || 'service_name') : undefined,
      })
    } else {
      form.resetFields()
      form.setFieldsValue({ db_type: 'mysql', port: 3306, slow_sql_threshold: 1.0, collect_interval: 5, collect_enabled: true, inspect_enabled: true })
    }
  }

  const handleSave = async () => {
    const values = await form.validateFields()
    if (editing) {
      const payload: Record<string, unknown> = { ...values }
      if (!payload.password) delete payload.password
      await updateDBConnection(editing.id, payload)
    } else {
      await createDBConnection(values)
    }
    message.success('已保存')
    setModalOpen(false)
    load()
  }

  const testConn = async (r: DbRow) => {
    try {
      await testDBConnection(r.id)
      message.success('连接成功')
    } catch { /* 拦截器已提示失败原因 */ }
  }
  const doCollect = async (r: DbRow) => {
    try { await collectSlowSQL(r.id); message.success('已触发采集') } catch { /* */ }
  }
  const del = async (r: DbRow) => {
    await deleteDBConnection(r.id)
    message.success('已删除')
    load()
  }

  // ===== 筛选后的数据 =====
  const filtered = useMemo(() => {
    const kw = keyword.toLowerCase()
    return conns.filter((c) => {
      if (kw) {
        const hay = `${c.name || ''} ${c.host || ''} ${c.database || ''}`.toLowerCase()
        if (!hay.includes(kw)) return false
      }
      if (typeFilter !== 'all' && c.db_type !== typeFilter) return false
      if (groupFilter !== 'all') {
        if (groupFilter === 'ungrouped') { if (c.group_name) return false }
        else if (c.group_name !== groupFilter) return false
      }
      if (envFilter) {
        if (envFilter === 'ungrouped') { if (c.env_tag) return false }
        else if (c.env_tag !== envFilter) return false
      }
      if (statusFilter !== 'all' && (c.status || 'unknown') !== statusFilter) return false
      if (collectFilter !== 'all') {
        const on = !!c.collect_enabled
        if (collectFilter === 'on' && !on) return false
        if (collectFilter === 'off' && on) return false
      }
      return true
    })
  }, [conns, keyword, typeFilter, groupFilter, envFilter, statusFilter, collectFilter])

  const envOptions = useMemo(() => {
    const set = new Set<string>()
    conns.forEach((c) => { if (c.env_tag) set.add(c.env_tag) })
    return Array.from(set).map((v) => ({ value: v, label: v }))
  }, [conns])

  const groupOptions = [
    { value: 'all', label: '全部分组' },
    ...groups.map((g) => ({ value: g.name, label: g.name })),
    { value: 'ungrouped', label: '未分组' },
  ]

  const columns = [
    {
      title: '名称', render: (_: unknown, r: DbRow) => (
        <a onClick={(e) => { e.stopPropagation(); navigate(`/resources/databases/${r.id}`) }}>{r.name}</a>
      ),
    },
    { title: '类型', width: 110, render: (_: unknown, r: DbRow) => <Tag color={DB_TYPE[r.db_type || '']?.color}>{DB_TYPE[r.db_type || '']?.label || r.db_type || '-'}</Tag> },
    {
      title: '来源', width: 110, render: (_: unknown, r: DbRow) => r.source === 'agent'
        ? <Tag color="purple">集群Agent</Tag>
        : <Tag>手动纳管</Tag>,
    },
    { title: '分组', width: 110, render: (_: unknown, r: DbRow) => r.group_name ? <Tag>{r.group_name}</Tag> : <Tag color="warning">未分组</Tag> },
    { title: '环境', width: 90, render: (_: unknown, r: DbRow) => r.env_tag ? <Tag color="cyan">{r.env_tag}</Tag> : '-' },
    {
      title: '状态', width: 90, render: (_: unknown, r: DbRow) => {
        const s = STATUS[r.status || 'unknown'] || STATUS.unknown
        const tag = <Tag color={s.color}>{s.label}</Tag>
        return (r.status === 'failed' && r.last_error) ? <Tooltip title={r.last_error}>{tag}</Tooltip> : tag
      },
    },
    {
      title: '采集', width: 110, render: (_: unknown, r: DbRow) => r.collect_enabled
        ? <span style={{ color: '#389e0d' }}>开{r.collect_interval != null ? ` · ${r.collect_interval} 分钟` : ''}</span>
        : <span style={{ color: '#8c8c8c' }}>关</span>,
    },
    {
      title: '巡检', width: 150, render: (_: unknown, r: DbRow) => r.inspect_enabled
        ? <span style={{ color: '#389e0d' }}>开{r.inspect_cron ? ` · ${r.inspect_cron}` : ''}</span>
        : <span style={{ color: '#8c8c8c' }}>关</span>,
    },
    { title: '最近采集', width: 150, render: (_: unknown, r: DbRow) => fmtTime(r.last_seen_at) },
    {
      title: '操作', width: 60, render: (_: unknown, r: DbRow) => (
        <div onClick={(e) => e.stopPropagation()}>
          <Dropdown
            trigger={['click']}
            dropdownRender={() => (
              <div style={{ background: '#fff', borderRadius: 8, boxShadow: '0 3px 6px -4px rgba(0,0,0,.12),0 6px 16px 0 rgba(0,0,0,.08)', padding: '4px 0', minWidth: 120 }}>
                <MenuItem onClick={() => navigate(`/resources/databases/${r.id}`)}>查看详情</MenuItem>
                <MenuItem onClick={() => testConn(r)}>测试连接</MenuItem>
                {admin && <MenuItem onClick={() => openModal(r)}>编辑</MenuItem>}
                {admin && <MenuItem onClick={() => doCollect(r)}>立即采集</MenuItem>}
                {admin && (
                  <Popconfirm title="删除该数据库？" okText="删除" okButtonProps={{ danger: true }} onConfirm={() => del(r)}>
                    <MenuItem danger>删除</MenuItem>
                  </Popconfirm>
                )}
              </div>
            )}
          >
            <Button size="small" type="text" icon={<MoreOutlined />} />
          </Dropdown>
        </div>
      ),
    },
  ]

  return (
    <Card size="small" title="数据库管理"
      extra={<Space>
        <Button icon={<CodeOutlined />} onClick={() => window.open('/sql-terminal', '_blank', 'noopener')}>SQL 终端</Button>
        {admin && <Button type="primary" icon={<PlusOutlined />} onClick={() => openModal()}>添加数据库</Button>}
        <Button icon={<ReloadOutlined />} onClick={() => { load(); loadGroups() }}>刷新</Button>
      </Space>}>
      {/* 视图切换（列表 / 树），记住到 localStorage */}
      <Segmented
        value={viewMode}
        onChange={(v) => changeViewMode(v as 'list' | 'tree')}
        options={[{ label: '列表', value: 'list' }, { label: '树', value: 'tree' }]}
        style={{ marginBottom: 12 }}
      />

      {viewMode === 'tree' ? (
        // 树视图：左实例树 + 右多页签
        <div style={{ display: 'flex', gap: 12, alignItems: 'stretch', height: 'calc(100vh - 250px)', minHeight: 420 }}>
          <div style={{ width: 320, flexShrink: 0 }}>
            <DbInstanceTree
              conns={conns as DbTreeConn[]}
              activeId={activeId}
              onOpen={openInstance}
              keyword={keyword}
              onRefresh={() => load()}
            />
          </div>
          <div style={{ flex: 1, minWidth: 0 }}>
            <DbInstanceTabs
              conns={conns as DbTreeConn[]}
              openIds={openIds}
              activeId={activeId}
              onActive={(id) => setActiveId(id)}
              onClose={closeInstance}
            />
          </div>
        </div>
      ) : (
        <>
          {/* 筛选条 */}
          <Space wrap style={{ marginBottom: 12 }}>
            <Input
              placeholder="搜索名称 / 主机 / 库名" allowClear prefix={<SearchOutlined />}
              style={{ width: 220 }} value={kwInput} onChange={(e) => setKwInput(e.target.value)}
            />
            <Select value={typeFilter} style={{ width: 130 }} onChange={setTypeFilter}
              options={[{ value: 'all', label: '全部类型' }, { value: 'mysql', label: 'MySQL' }, { value: 'pg', label: 'PostgreSQL' }, { value: 'oracle', label: 'Oracle' }]} />
            <Select value={groupFilter} style={{ width: 140 }} onChange={setGroupFilter} options={groupOptions} />
            <Select value={envFilter} style={{ width: 140 }} allowClear placeholder="环境标签"
              onChange={(v) => setEnvFilter(v || undefined)}
              options={[{ value: 'ungrouped', label: '未分组' }, ...envOptions]} />
            <Select value={statusFilter} style={{ width: 120 }} onChange={setStatusFilter}
              options={[{ value: 'all', label: '全部状态' }, { value: 'connected', label: '已连接' }, { value: 'failed', label: '采集失败' }, { value: 'unknown', label: '未知' }, { value: 'removed', label: '已下线' }]} />
            <Select value={collectFilter} style={{ width: 130 }} onChange={setCollectFilter}
              options={[{ value: 'all', label: '采集：全部' }, { value: 'on', label: '采集：已开启' }, { value: 'off', label: '采集：已关闭' }]} />
          </Space>

          {loading ? <Spin /> : conns.length === 0 ? (
            // 空态：无数据时引导接入
            <Empty style={{ padding: '40px 0' }} image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无数据库资源">
              <div style={{ maxWidth: 520, margin: '0 auto', textAlign: 'left' }}>
                <div style={{ fontWeight: 600, marginBottom: 8 }}>两种接入方式：</div>
                <div style={{ marginBottom: 6 }}>① <b>手动纳管</b>：填写连接信息（主机 / 端口 / 账号密码）后，平台定时采集指标与慢 SQL、生成巡检报告。</div>
                <div style={{ marginBottom: 12 }}>② <b>集群 Agent 自动上报</b>：部署集群 Agent 后，平台自动发现并纳管集群内的数据库实例。</div>
                {admin && <Button type="primary" icon={<PlusOutlined />} onClick={() => openModal()}>添加数据库</Button>}
              </div>
            </Empty>
          ) : (
            <Table
              rowKey="id" size="small" loading={loading} dataSource={filtered}
              pagination={{ pageSize: 20, showSizeChanger: false, showTotal: (t) => `共 ${t} 条` }}
              columns={columns}
              onRow={(r) => ({ onClick: () => navigate(`/resources/databases/${r.id}`), style: { cursor: 'pointer' } })}
            />
          )}
        </>
      )}

      {/* 添加/编辑表单（字段照搬 DiagnosticCenter.DBConnections） */}
      <Modal title={editing ? '编辑数据库连接' : '添加数据库连接'} open={modalOpen} onOk={handleSave} onCancel={() => setModalOpen(false)} width={560}>
        <Form form={form} layout="vertical">
          <Form.Item name="name" label="名称" rules={[{ required: true }]}><Input /></Form.Item>
          <Form.Item name="env_tag" label="环境" tooltip="生产/UAT/测试…；告警与工单按环境聚合（留空=未分组）">
            <Input placeholder="如 生产 / UAT / 测试" />
          </Form.Item>
          <Form.Item name="group_name" label="分组" tooltip="可直接输入新分组名；留空=未分组">
            <AutoComplete
              allowClear
              placeholder="选择或输入分组（留空=未分组）"
              options={groups.map((g) => ({ value: g.name }))}
              filterOption={(input: string, option?: { value?: string }) => String(option?.value || '').toLowerCase().includes(input.toLowerCase())}
            />
          </Form.Item>
          <Form.Item name="db_type" label="数据库类型" rules={[{ required: true }]}>
            <Select onChange={onTypeChange} options={[
              { value: 'mysql', label: 'MySQL' },
              { value: 'pg', label: 'PostgreSQL' },
              { value: 'oracle', label: 'Oracle' },
            ]} />
          </Form.Item>
          {dbType === 'oracle' && (
            <Form.Item name="connect_mode" label="连接方式" rules={[{ required: true }]}>
              <Select onChange={onOracleModeChange} options={[
                { value: 'service_name', label: 'Service Name' },
                { value: 'sid', label: 'SID' },
                { value: 'tns', label: 'TNS 描述符' },
              ]} />
            </Form.Item>
          )}
          {!(dbType === 'oracle' && oracleMode === 'tns') && (
            <Space>
              <Form.Item name="host" label="主机" rules={[{ required: true, message: '请填主机' }]}><Input /></Form.Item>
              <Form.Item name="port" label="端口" rules={[{ required: true }]}><InputNumber min={1} max={65535} /></Form.Item>
            </Space>
          )}
          {dbType !== 'oracle' && (
            <Form.Item
              name="database"
              label={dbType === 'pg' ? '数据库（必填）' : '数据库（可选）'}
              rules={dbType === 'pg' ? [{ required: true, message: 'PostgreSQL 必须填数据库名' }] : []}
              extra={dbType === 'mysql' ? '不填=连接实例但不选默认库（SQL 里需写全名 db.table）' : undefined}
            ><Input /></Form.Item>
          )}
          {dbType === 'oracle' && oracleMode === 'service_name' && (
            <Form.Item name="service_name" label="Service Name" rules={[{ required: true, message: '请填 Service Name' }]}>
              <Input placeholder="如 ORCLPDB1" />
            </Form.Item>
          )}
          {dbType === 'oracle' && oracleMode === 'sid' && (
            <Form.Item name="sid" label="SID" rules={[{ required: true, message: '请填 SID' }]}>
              <Input placeholder="如 ORCL" />
            </Form.Item>
          )}
          {dbType === 'oracle' && oracleMode === 'tns' && (
            <Form.Item
              name="tns_descriptor"
              label="TNS 描述符"
              rules={[{ required: true, message: '请粘贴 TNS 描述符' }, {
                validator: (_, v: string) => {
                  const t = (v || '').trim()
                  if (!t.toUpperCase().startsWith('(DESCRIPTION=')) return Promise.reject(new Error('必须以 (DESCRIPTION= 开头'))
                  let depth = 0
                  for (const ch of t) {
                    if (ch === '(') depth += 1
                    else if (ch === ')') { depth -= 1; if (depth < 0) return Promise.reject(new Error('括号不配对')) }
                  }
                  return depth === 0 ? Promise.resolve() : Promise.reject(new Error('括号不配对'))
                },
              }]}
            >
              <Input.TextArea rows={4} placeholder="(DESCRIPTION=(ADDRESS=(PROTOCOL=TCP)(HOST=...)(PORT=1521))(CONNECT_DATA=(SERVICE_NAME=...)))" />
            </Form.Item>
          )}
          {dbType === 'oracle' && (
            <Form.Item name="oracle_role" label="角色" tooltip="SYSDBA/SYSOPER 需要相应权限；默认=普通用户">
              <Select allowClear placeholder="默认" options={[
                { value: 'sysdba', label: 'SYSDBA' },
                { value: 'sysoper', label: 'SYSOPER' },
              ]} />
            </Form.Item>
          )}
          <Form.Item name="username" label="用户名" rules={[{ required: true, message: '请填用户名' }]} extra="建议用只读权限账号（诊断采集只需查系统视图 / performance_schema）"><Input /></Form.Item>
          <Form.Item name="password" label={editing ? '密码（留空=不修改）' : '密码'} rules={editing ? [] : [{ required: true }]}>
            <Input.Password />
          </Form.Item>
          <Space>
            <Form.Item name="slow_sql_threshold" label="慢SQL阈值(s)"><InputNumber min={0.001} step={0.1} /></Form.Item>
            <Form.Item name="collect_interval" label="采集间隔(分钟)"><InputNumber min={1} /></Form.Item>
          </Space>
          <Form.Item name="collect_enabled" label="采集开关" valuePropName="checked"><Switch /></Form.Item>
          <Form.Item name="inspect_enabled" label="巡检开关" valuePropName="checked" tooltip="关闭后该库不参与巡检/诊断报告生成"><Switch /></Form.Item>
          <Form.Item name="inspect_cron" label="定时巡检 Cron" tooltip="分 时 日 月 周；如 0 9 * * * = 每天 09:00；留空=不定时，仅手动点「诊断」">
            <Input placeholder="留空=不定时，如 0 9 * * *" />
          </Form.Item>
        </Form>
      </Modal>
    </Card>
  )
}

/** 下拉菜单项（操作列表里的小菜单项） */
function MenuItem({ children, onClick, danger }: { children: ReactNode; onClick?: () => void; danger?: boolean }) {
  return (
    <div
      onClick={(e) => { e.stopPropagation(); onClick?.() }}
      style={{
        padding: '6px 12px', cursor: 'pointer', fontSize: 13,
        color: danger ? '#cf1322' : 'rgba(0,0,0,.88)',
      }}
      onMouseEnter={(e) => { (e.currentTarget as HTMLElement).style.background = '#f5f5f5' }}
      onMouseLeave={(e) => { (e.currentTarget as HTMLElement).style.background = 'transparent' }}
    >
      {children}
    </div>
  )
}
