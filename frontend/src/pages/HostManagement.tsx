// 主机/虚拟机纳管（N11 + R25）：SSH 采集进程监控；分组 + 分组权限
import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Alert, Button, Card, Drawer, Form, Input, InputNumber, List, Menu, Modal, Popconfirm,
  Radio, Select, Space, Steps, Switch, Table, Tabs, Tag, Tooltip, Upload, message,
} from 'antd'
import { CodeOutlined, DownloadOutlined, ImportOutlined, PlusOutlined, ReloadOutlined, SearchOutlined, SyncOutlined, TeamOutlined, UploadOutlined } from '@ant-design/icons'
import {
  getHosts, createHost, updateHost, deleteHost, checkHost,
  getHostGroups, createHostGroup, updateHostGroup, deleteHostGroup,
  getHostGroupMembers, addHostGroupMember, removeHostGroupMember, getUsers,
  importHosts, getHostImportTemplate,
} from '../services/api'
import type { HostItem, HostCheckResult, HostGroup, HostGroupMember, HostImportResult } from '../services/api'
import type { User } from '../types'
import { HostCommandAuditPanel } from '../components/HostCommandAuditDrawer'

const STATUS: Record<string, { color: string; label: string }> = {
  online: { color: 'green', label: '在线' }, offline: { color: 'red', label: '离线' }, unknown: { color: 'default', label: '未知' },
}
const KIND: Record<string, { color: string; label: string }> = {
  host: { color: 'blue', label: '主机' }, vm: { color: 'purple', label: '虚拟机' },
}
const ROLE_LABEL: Record<string, string> = { admin: '组管理', operator: '操作', viewer: '只读' }
const IMPORT_STATUS: Record<string, { color: string; label: string }> = {
  created: { color: 'green', label: '新建' }, updated: { color: 'blue', label: '更新' },
  skipped: { color: 'orange', label: '跳过' }, failed: { color: 'red', label: '失败' },
}

export default function HostManagement() {
  const navigate = useNavigate()
  const [hosts, setHosts] = useState<HostItem[]>([])
  const [groups, setGroups] = useState<HostGroup[]>([])
  const [isAdmin, setIsAdmin] = useState(false)
  const [loading, setLoading] = useState(false)
  const [groupFilter, setGroupFilter] = useState<string>('all')
  const [sourceFilter, setSourceFilter] = useState<string>('')
  const [keyword, setKeyword] = useState('')
  const [kwInput, setKwInput] = useState('')  // 输入框即时值；300ms 防抖后写 keyword 触发查询
  // 页面两个 Tab：主机列表 / 命令审计（R36 §八）
  const [tab, setTab] = useState<'list' | 'audit'>('list')

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

  // 批量导入
  const [importOpen, setImportOpen] = useState(false)
  const [importTab, setImportTab] = useState<'paste' | 'file'>('paste')
  const [pasteContent, setPasteContent] = useState('')
  const [fileContent, setFileContent] = useState('')
  const [fileName, setFileName] = useState('')
  const [onDup, setOnDup] = useState<'skip' | 'update'>('skip')
  const [defaultGroupId, setDefaultGroupId] = useState<number | null>(null)
  const [importPreview, setImportPreview] = useState<HostImportResult | null>(null)
  const [importing, setImporting] = useState(false)

  const authType = Form.useWatch('auth_type', form) as 'password' | 'key' | undefined

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
    if (sourceFilter) params.source = sourceFilter
    if (keyword.trim()) params.keyword = keyword.trim()
    getHosts(params).then((d) => setHosts(d || [])).catch(() => {}).finally(() => setLoading(false))
  }

  useEffect(() => { loadGroups() }, [])
  // 搜索防抖：停止输入 300ms 才查询
  useEffect(() => {
    const t = setTimeout(() => setKeyword(kwInput.trim()), 300)
    return () => clearTimeout(t)
  }, [kwInput])

  useEffect(() => { load() }, [groupFilter, sourceFilter, keyword])

  const openCreate = () => {
    setEditing(null); setStep(0); setWizardKind('host')
    form.resetFields()
    form.setFieldsValue({ ssh_port: 22, ssh_user: 'root', enabled: true, auth_type: 'password', process_paths: [] })
    setModal(true)
  }
  const openEdit = (h: HostItem) => {
    setEditing(h); setStep(1)
    form.resetFields()
    form.setFieldsValue({ ...h, auth_type: h.auth_type || 'password', process_paths: h.process_paths || [], group_id: h.group_id ?? undefined })
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

  // ===== 批量导入 =====
  const importContent = importTab === 'paste' ? pasteContent : fileContent
  const openImport = () => {
    setImportTab('paste'); setPasteContent(''); setFileContent(''); setFileName(''); setOnDup('skip')
    setDefaultGroupId(null); setImportPreview(null); setImportOpen(true)
  }
  const downloadTemplate = async () => {
    try { await getHostImportTemplate() } catch { message.error('模板下载失败') }
  }
  const doPreview = async () => {
    if (!importContent.trim()) { message.warning('请先粘贴内容或上传文件'); return }
    setImporting(true)
    try {
      const r = await importHosts({ content: importContent, format: importTab === 'paste' ? 'table' : 'csv', on_duplicate: onDup, default_group_id: defaultGroupId, dry_run: true })
      setImportPreview(r)
    } catch { /* */ } finally { setImporting(false) }
  }
  const doImport = async () => {
    if (!importContent.trim()) { message.warning('请先粘贴内容或上传文件'); return }
    setImporting(true)
    try {
      const r = await importHosts({ content: importContent, format: importTab === 'paste' ? 'table' : 'csv', on_duplicate: onDup, default_group_id: defaultGroupId, dry_run: false })
      setImportPreview(r)
      message.success(`导入完成：成功 ${r.success}（含新建/更新），跳过 ${r.skipped}，失败 ${r.failed}${r.groups_created.length ? `，新建分组 ${r.groups_created.length} 个` : ''}`)
      load(); loadGroups()
    } catch { /* */ } finally { setImporting(false) }
  }
  const exportFailedCsv = () => {
    const failed = (importPreview?.rows || []).filter((r) => r.status === 'failed')
    if (!failed.length) return
    const esc = (c: unknown) => `"${String(c ?? '').replace(/"/g, '""')}"`
    const lines = [['行号', '名称', '地址', '状态', '原因'].join(',')]
      .concat(failed.map((r) => [r.line, r.name, r.address, r.status, r.reason].map(esc).join(',')))
    const blob = new Blob(['\ufeff' + lines.join('\n')], { type: 'text/csv;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url; a.download = 'hosts-import-failed.csv'
    document.body.appendChild(a); a.click(); a.remove()
    URL.revokeObjectURL(url)
  }

  const menuItems = [
    { key: 'all', label: '全部主机' },
    { key: 'ungrouped', label: '未分组' },
    ...groups.map((g) => ({ key: String(g.id), label: `${g.name}（${g.host_count}）` })),
  ]

  const groupOptions = createableGroups.map((g) => ({ value: g.id, label: g.name }))

  const columns = [
    { title: '名称', render: (_: unknown, r: HostItem) => r.description ? <Tooltip title={r.description}>{r.name}</Tooltip> : <>{r.name}</> },
    { title: '分组', width: 110, render: (_: unknown, r: HostItem) => r.group_name
      ? <Tag>{r.group_name}</Tag> : <Tag color="warning">未分组</Tag> },
    { title: '类型', width: 90, render: (_: unknown, r: HostItem) => <Tag color={KIND[r.kind]?.color}>{KIND[r.kind]?.label || r.kind}</Tag> },
    { title: '来源', width: 90, render: (_: unknown, r: HostItem) => r.source === 'agent' ? <Tag color="cyan">agent</Tag> : <Tag>直连</Tag> },
    { title: '地址', render: (_: unknown, r: HostItem) => `${r.address}:${r.ssh_port}` },
    { title: '凭据', width: 100, render: (_: unknown, r: HostItem) => (
      <Space size={4}>
        <Tag color={r.has_password ? 'green' : 'default'}>密码</Tag>
        <Tag color={r.has_key ? 'cyan' : 'default'}>密钥</Tag>
      </Space>
    ) },
    { title: '状态', dataIndex: 'status', width: 80, render: (s: string, r: HostItem) => {
      const tag = <Tag color={STATUS[s]?.color}>{STATUS[s]?.label || s}</Tag>
      return s === 'offline' && r.last_error ? <Tooltip title={r.last_error}>{tag}</Tooltip> : tag
    } },
    { title: '监控进程', width: 220, render: (_: unknown, r: HostItem) => (r.process_paths || []).map((p) => p.name || p.path).join('、') || '-' },
    { title: '启用', dataIndex: 'enabled', width: 70, render: (v: boolean) => v ? <Tag color="green">是</Tag> : <Tag>否</Tag> },
    { title: '操作', width: 170, render: (_: unknown, r: HostItem) => (
      <Space size="small">
        {r.can.operate && r.source === 'agent' && <Button size="small" type="link" onClick={() => setCheckResult({ online: r.last_metrics?.online ?? false, error: '', paths: r.last_metrics?.paths || [] })}>指标</Button>}
        {r.can.operate && r.source !== 'agent' && <Button size="small" type="link" onClick={() => window.open(`/terminal?host=${r.id}`, '_blank', 'noopener')}>终端</Button>}
        {r.can.operate && r.source !== 'agent' && <Button size="small" type="link" loading={checking === r.id} icon={<SyncOutlined />} onClick={() => doCheck(r.id)}>检测</Button>}
        {r.can.operate && <Button size="small" type="link" onClick={() => openEdit(r)}>编辑</Button>}
        {r.can.delete && <Popconfirm title="删除该主机？" onConfirm={() => del(r.id)}><a style={{ color: '#cf1322' }}>删除</a></Popconfirm>}
      </Space>
    ) },
  ]

  const noPerm = !isAdmin && groups.length === 0

  return (
    <Card size="small" title="节点管理"
      extra={<Space>
        {!noPerm && <Button icon={<CodeOutlined />} onClick={() => window.open('/terminal', '_blank', 'noopener')}>Web 终端</Button>}
        <Button icon={<ReloadOutlined />} onClick={() => { load(); loadGroups() }}>刷新</Button>
        {isAdmin && <Button icon={<TeamOutlined />} onClick={() => setGroupDrawer(true)}>分组管理</Button>}
        {(isAdmin || createableGroups.length > 0) && <Button icon={<ImportOutlined />} onClick={openImport}>批量导入</Button>}
        {(isAdmin || createableGroups.length > 0) && <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>添加主机</Button>}
      </Space>}>
      <Tabs
        activeKey={tab}
        onChange={(k) => setTab(k as 'list' | 'audit')}
        items={[
          {
            key: 'list',
            label: '主机列表',
            children: (
              <>
      <div style={{ display: 'flex', gap: 16, alignItems: 'flex-start' }}>
        {/* 左侧：分组树 + 类型 + 搜索 */}
        <div style={{ width: 200, flex: '0 0 200px' }}>
          <Input
            placeholder="搜索名称 / 地址 / 分组" allowClear prefix={<SearchOutlined />}
            value={kwInput}
            onChange={(e) => setKwInput(e.target.value)}
            style={{ marginBottom: 8 }}
          />
          {isAdmin && (
            <Button size="small" block icon={<PlusOutlined />} style={{ marginBottom: 8 }}
              onClick={() => { setGroupEdit(null); groupForm.resetFields(); setGroupModalOpen(true) }}>新建分组</Button>
          )}
          <Menu
            mode="inline" style={{ border: 'none' }}
            selectedKeys={[groupFilter]}
            items={menuItems}
            onClick={({ key }) => setGroupFilter(key)}
          />
          <Select placeholder="来源" allowClear style={{ width: '100%', marginTop: 8, marginBottom: 0 }} value={sourceFilter || undefined} onChange={(v) => setSourceFilter(v || '')} options={[{ value: 'direct', label: '直连' }, { value: 'agent', label: 'agent 代采' }]} />
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

      </>
            ),
          },
          {
            key: 'audit',
            label: '命令审计',
            children: <HostCommandAuditPanel active={tab === 'audit'} />,
          },
        ]}
      />

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
              <Form.Item name="ssh_user" label="SSH 用户" style={{ width: '34%' }}><Input /></Form.Item>
              <Form.Item name="auth_type" label="认证方式" style={{ width: '34%' }} rules={[{ required: true }]}>
                <Select options={[{ value: 'password', label: '密码' }, { value: 'key', label: '私钥' }]} />
              </Form.Item>
              <Form.Item name="env_tag" label="环境标签" style={{ width: '32%' }}><Input placeholder="如 prod" /></Form.Item>
            </Space.Compact>
            {authType === 'password' && (
              <Form.Item name="ssh_password" label="SSH 密码" preserve={false}>
                <Input.Password placeholder={editing ? '留空则不修改' : '留空则使用私钥认证'} autoComplete="new-password" />
              </Form.Item>
            )}
            {authType === 'key' && (
              <>
                <Form.Item name="ssh_private_key" label="私钥内容" preserve={false} extra="支持 OpenSSH / PEM 格式，留空则不修改">
                  <Input.TextArea rows={5} placeholder="-----BEGIN OPENSSH PRIVATE KEY-----" style={{ fontFamily: 'monospace' }} />
                </Form.Item>
                <Form.Item name="ssh_key_passphrase" label="私钥口令（可选）" preserve={false}>
                  <Input.Password placeholder="私钥加密时填写，留空则不修改" autoComplete="new-password" />
                </Form.Item>
              </>
            )}
            <Form.Item name="description" label="说明"><Input placeholder="可选，最多 300 字" maxLength={300} /></Form.Item>
            <Form.Item name="ssh_key_path" label="本地私钥路径（兼容旧配置，本地文件方式，留空=默认 ~/.ssh/id_rsa）"><Input /></Form.Item>
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

      {/* 批量导入 */}
      <Modal title="批量导入主机" open={importOpen} onCancel={() => setImportOpen(false)} width={760} destroyOnClose
        footer={[
          <Button key="tpl" icon={<DownloadOutlined />} onClick={downloadTemplate}>下载模板</Button>,
          <Button key="c" onClick={() => setImportOpen(false)}>关闭</Button>,
          <Button key="p" loading={importing} onClick={doPreview}>校验预览</Button>,
          <Button key="s" type="primary" loading={importing} onClick={doImport}>确认导入</Button>,
        ]}>
        <Tabs activeKey={importTab} onChange={(k) => { setImportTab(k as 'paste' | 'file'); setImportPreview(null) }}
          items={[
            {
              key: 'paste', label: '粘贴表格',
              children: <Input.TextArea rows={8} value={pasteContent} onChange={(e) => { setPasteContent(e.target.value); setImportPreview(null) }}
                placeholder={'支持逗号 / 制表符分隔，带表头（中文列名）。示例：\n名称,地址,端口,用户名,认证方式,密码,私钥,分组,进程路径,说明\nbiz-app-01,192.168.6.41,22,root,password,******,,生产,/opt/app/app.jar,业务应用 A'} />,
            },
            {
              key: 'file', label: '上传 CSV',
              children: (
                <>
                  <Upload.Dragger accept=".csv,.txt" maxCount={1} showUploadList={false}
                    beforeUpload={(file) => {
                      const reader = new FileReader()
                      reader.onload = () => { setFileContent(String(reader.result || '')); setFileName(file.name); setImportPreview(null) }
                      reader.readAsText(file)
                      return false
                    }}>
                    <p className="ant-upload-drag-icon"><UploadOutlined /></p>
                    <p className="ant-upload-text">点击或拖拽 CSV 文件到此处</p>
                    <p className="ant-upload-hint">文件在本机读取，不会真正上传；支持中文/英文表头</p>
                  </Upload.Dragger>
                  {fileName && <div style={{ marginTop: 8, color: '#888' }}>已读取：{fileName}</div>}
                </>
              ),
            },
          ]}
        />
        <Space style={{ marginTop: 12, width: '100%' }} wrap>
          <span>重复处理：</span>
          <Select style={{ width: 120 }} value={onDup} onChange={setOnDup} options={[{ value: 'skip', label: '跳过' }, { value: 'update', label: '覆盖' }]} />
          <span style={{ marginLeft: 12 }}>默认分组（可选）：</span>
          <Select style={{ width: 200 }} allowClear placeholder="不指定（按表内分组列）" value={defaultGroupId ?? undefined}
            onChange={(v) => setDefaultGroupId(v ?? null)} options={groups.map((g) => ({ value: g.id, label: g.name }))} />
        </Space>

        {importPreview && (
          <>
            <Alert style={{ marginTop: 12 }} type={importPreview.failed > 0 ? 'warning' : 'success'} showIcon
              message={importPreview.dry_run
                ? `校验预览（未导入）：共 ${importPreview.total} 行，可成功 ${importPreview.success}，跳过 ${importPreview.skipped}，失败 ${importPreview.failed}`
                : `导入完成：共 ${importPreview.total} 行，成功 ${importPreview.success}，跳过 ${importPreview.skipped}，失败 ${importPreview.failed}`}
              description={importPreview.groups_created.length ? `自动新建分组：${importPreview.groups_created.join('、')}` : undefined} />
            <Table rowKey={(r: HostImportResult['rows'][number], i?: number) => `${r.line}-${i ?? 0}`} size="small" style={{ marginTop: 12 }} pagination={false}
              dataSource={importPreview.rows}
              columns={[
                { title: '行', dataIndex: 'line', width: 60 },
                { title: '名称', dataIndex: 'name', ellipsis: true },
                { title: '地址', dataIndex: 'address', ellipsis: true },
                { title: '状态', dataIndex: 'status', width: 90, render: (s: string) => <Tag color={IMPORT_STATUS[s]?.color}>{IMPORT_STATUS[s]?.label || s}</Tag> },
                { title: '原因', dataIndex: 'reason', ellipsis: true },
              ]} />
            {importPreview.failed > 0 && (
              <Button size="small" style={{ marginTop: 8 }} icon={<DownloadOutlined />} onClick={exportFailedCsv}>导出失败明细 CSV</Button>
            )}
          </>
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
      </Drawer>

      {/* 新建/编辑分组（左侧与分组管理抽屉共用） */}
      <Modal title={groupEdit ? '编辑分组' : '新建分组'} open={groupModalOpen} onCancel={() => setGroupModalOpen(false)} onOk={saveGroup} destroyOnClose>
        <Form form={groupForm} layout="vertical">
          <Form.Item name="name" label="分组名" rules={[{ required: true }]}><Input /></Form.Item>
          <Form.Item name="description" label="描述"><Input /></Form.Item>
          <Form.Item name="sort" label="排序"><InputNumber style={{ width: '100%' }} /></Form.Item>
        </Form>
      </Modal>

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
