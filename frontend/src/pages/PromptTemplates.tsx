// R10 提示词场景体系：场景模板 / 公共片段 / 路由规则 + 预览渲染
import { useEffect, useState } from 'react'
import {
  Alert, Button, Card, Descriptions, Drawer, Form, Input, InputNumber, Modal, Popconfirm, Select, Space,
  Table, Tabs, Tag, message,
} from 'antd'
import { PlusOutlined, EyeOutlined } from '@ant-design/icons'
import {
  getPromptTemplates, createPromptTemplate, updatePromptTemplate, previewPromptTemplate,
  getPromptFragments, createPromptFragment, updatePromptFragment, deletePromptFragment,
  getPromptRouting, createPromptRouting, updatePromptRouting, deletePromptRouting,
} from '../services/api'
import type { PromptFragment, PromptRouting } from '../services/api'

interface PromptTemplate {
  id: number
  name: string
  category?: string
  version?: number
  is_active?: boolean
  scene?: string
  sample_vars?: string
  system_prompt?: string
  user_prompt_template?: string
  variables?: string
  [key: string]: unknown
}

const SCENES = [
  { value: 'pod', label: 'Pod 故障' },
  { value: 'node', label: '节点故障' },
  { value: 'workload', label: '服务/工作负载' },
  { value: 'database', label: '数据库' },
  { value: 'cluster', label: '集群' },
  { value: 'platform', label: '平台自身' },
  { value: 'action', label: '动作类（方案/风险/验证）' },
]
const SCENE_LABEL: Record<string, string> = Object.fromEntries(SCENES.map((s) => [s.value, s.label]))

export default function PromptTemplates() {
  const [tab, setTab] = useState('templates')
  const [templates, setTemplates] = useState<PromptTemplate[]>([])
  const [loading, setLoading] = useState(false)
  const [sceneFilter, setSceneFilter] = useState<string>()
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [editing, setEditing] = useState(false)
  const [creating, setCreating] = useState(false)
  const [current, setCurrent] = useState<PromptTemplate | null>(null)
  const [form] = Form.useForm()

  // 预览
  const [previewOpen, setPreviewOpen] = useState(false)
  const [previewTpl, setPreviewTpl] = useState<PromptTemplate | null>(null)
  const [varsText, setVarsText] = useState('{}')
  const [previewOut, setPreviewOut] = useState<{ system_prompt: string; user_prompt: string; missing_vars: string[] } | null>(null)

  // 片段 / 路由
  const [frags, setFrags] = useState<PromptFragment[]>([])
  const [routes, setRoutes] = useState<PromptRouting[]>([])
  const [fragModal, setFragModal] = useState<PromptFragment | null | undefined>(undefined)
  const [routeModal, setRouteModal] = useState<PromptRouting | null | undefined>(undefined)
  const [fragForm] = Form.useForm()
  const [routeForm] = Form.useForm()

  const load = () => {
    setLoading(true)
    getPromptTemplates().then((d) => setTemplates(d as PromptTemplate[])).finally(() => setLoading(false))
  }
  const loadFrags = () => getPromptFragments().then((d) => setFrags(d || [])).catch(() => {})
  const loadRoutes = () => getPromptRouting().then((d) => setRoutes(d || [])).catch(() => {})
  useEffect(load, [])
  useEffect(() => { loadFrags(); loadRoutes() }, [])

  const openCreate = () => {
    setCreating(true); setEditing(true); setCurrent(null)
    form.resetFields()
    form.setFieldsValue({ category: 'diagnosis', scene: 'workload' })
    setDrawerOpen(true)
  }
  const openView = (t: PromptTemplate) => {
    setCurrent(t); setCreating(false); setEditing(false); form.setFieldsValue(t); setDrawerOpen(true)
  }
  const openEdit = (t: PromptTemplate) => {
    setCurrent(t); setCreating(false); setEditing(true); form.setFieldsValue(t); setDrawerOpen(true)
  }
  const handleSave = async () => {
    const values = await form.validateFields()
    if (creating) await createPromptTemplate(values)
    else await updatePromptTemplate(current!.id, values)
    message.success(creating ? '模板已创建' : '已保存（版本号已 +1）')
    setDrawerOpen(false)
    load()
  }

  const openPreview = (t: PromptTemplate) => {
    setPreviewTpl(t)
    setPreviewOut(null)
    let vars: Record<string, string> = {}
    try {
      const sv = t.sample_vars ? JSON.parse(t.sample_vars) : null
      const keys: string[] = (() => { try { return JSON.parse((t.variables as string) || '[]') } catch { return [] } })()
      if (sv && typeof sv === 'object') vars = sv as Record<string, string>
      else keys.forEach((k) => { vars[k] = `<${k}>` })
    } catch { /* ignore */ }
    setVarsText(JSON.stringify(vars, null, 2))
    setPreviewOpen(true)
  }
  const doPreview = async () => {
    if (!previewTpl) return
    let vars: Record<string, string>
    try {
      vars = JSON.parse(varsText)
      if (typeof vars !== 'object' || Array.isArray(vars)) throw new Error('必须是 JSON 对象')
    } catch (e) {
      message.error(`变量 JSON 不合法：${(e as Error).message}`)
      return
    }
    setPreviewOut(await previewPromptTemplate(previewTpl.id, vars))
  }

  const saveFrag = async () => {
    const v = await fragForm.validateFields()
    if (fragModal) await updatePromptFragment(fragModal.id, v)
    else await createPromptFragment(v)
    message.success('已保存'); setFragModal(undefined); loadFrags()
  }
  const saveRoute = async () => {
    const v = await routeForm.validateFields()
    if (routeModal) await updatePromptRouting(routeModal.id, v)
    else await createPromptRouting(v)
    message.success('已保存'); setRouteModal(undefined); loadRoutes()
  }

  const filteredTemplates = sceneFilter ? templates.filter((t) => (t.scene || '') === sceneFilter) : templates

  return (
    <div>
      <Alert
        type="info" showIcon style={{ marginBottom: 12 }}
        message="三层体系：公共片段（输出格式/红线/证据要求）→ 场景模板（用 {{fragment:key}} 引用）→ 路由规则（fault_type → 模板，exact/prefix/default）。改配置即时生效，无需改代码。"
      />
      <Tabs
        activeKey={tab}
        onChange={setTab}
        items={[
          {
            key: 'templates',
            label: `场景模板（${templates.length}）`,
            children: (
              <>
                <Space style={{ marginBottom: 12 }} wrap>
                  <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>新增模板</Button>
                  <Select
                    allowClear placeholder="按场景筛选" style={{ width: 200 }} value={sceneFilter}
                    onChange={setSceneFilter}
                    options={SCENES.map((s) => ({ value: s.value, label: s.label }))}
                  />
                </Space>
                <Table
                  rowKey="id" loading={loading} dataSource={filteredTemplates} size="small"
                  columns={[
                    { title: '模板名', dataIndex: 'name' },
                    {
                      title: '场景', dataIndex: 'scene', width: 140,
                      render: (s: string) => (s ? <Tag color="blue">{SCENE_LABEL[s] || s}</Tag> : <Tag>未分类</Tag>),
                    },
                    { title: '分类', dataIndex: 'category', width: 130, render: (c: string) => <Tag>{c}</Tag> },
                    { title: '版本', dataIndex: 'version', width: 80 },
                    { title: '状态', dataIndex: 'is_active', width: 80, render: (v: boolean) => (v ? '启用' : '停用') },
                    {
                      title: '操作', width: 220,
                      render: (_: unknown, r: PromptTemplate) => (
                        <>
                          <Button size="small" icon={<EyeOutlined />} style={{ marginRight: 8 }} onClick={() => openPreview(r)}>预览</Button>
                          <Button size="small" onClick={() => openView(r)} style={{ marginRight: 8 }}>查看</Button>
                          <Button size="small" type="primary" onClick={() => openEdit(r)}>编辑</Button>
                        </>
                      ),
                    },
                  ]}
                />
              </>
            ),
          },
          {
            key: 'fragments',
            label: `公共片段（${frags.length}）`,
            children: (
              <>
                <Space style={{ marginBottom: 12 }}>
                  <Button type="primary" icon={<PlusOutlined />} onClick={() => { fragForm.resetFields(); setFragModal(null) }}>新增片段</Button>
                </Space>
                <Table<PromptFragment>
                  rowKey="id" size="small" dataSource={frags}
                  columns={[
                    { title: 'Key', dataIndex: 'key', width: 160, render: (k: string) => <code>{`{{fragment:${k}}}`}</code> },
                    { title: '名称', dataIndex: 'name', width: 200 },
                    { title: '分类', dataIndex: 'category', width: 100 },
                    { title: '内容', dataIndex: 'content', ellipsis: true },
                    { title: '启用', dataIndex: 'enabled', width: 70, render: (v: boolean) => (v ? '是' : '否') },
                    {
                      title: '操作', width: 140,
                      render: (_: unknown, r: PromptFragment) => (
                        <Space size={4}>
                          <Button size="small" onClick={() => { fragForm.setFieldsValue(r); setFragModal(r) }}>编辑</Button>
                          <Popconfirm title="删除该片段？" onConfirm={async () => { await deletePromptFragment(r.id); loadFrags() }}>
                            <Button size="small" danger>删除</Button>
                          </Popconfirm>
                        </Space>
                      ),
                    },
                  ]}
                />
              </>
            ),
          },
          {
            key: 'routing',
            label: `路由规则（${routes.length}）`,
            children: (
              <>
                <Space style={{ marginBottom: 12 }}>
                  <Button type="primary" icon={<PlusOutlined />} onClick={() => {
                    routeForm.resetFields()
                    routeForm.setFieldsValue({ match_type: 'fault_type', match_mode: 'exact', priority: 100, enabled: true })
                    setRouteModal(null)
                  }}>新增规则</Button>
                  <span style={{ color: '#8c8c8c', fontSize: 12 }}>命中顺序：exact → prefix → default；priority 越小越优先</span>
                </Space>
                <Table<PromptRouting>
                  rowKey="id" size="small" dataSource={routes}
                  columns={[
                    { title: '匹配类型', dataIndex: 'match_type', width: 110 },
                    { title: '匹配值', dataIndex: 'match_value', width: 220, render: (v: string) => (v ? <code>{v}</code> : <span style={{ color: '#999' }}>（default）</span>) },
                    { title: '模式', dataIndex: 'match_mode', width: 90 },
                    { title: '→ 模板', dataIndex: 'template_name', width: 240 },
                    { title: '优先级', dataIndex: 'priority', width: 80 },
                    { title: '启用', dataIndex: 'enabled', width: 70, render: (v: boolean) => (v ? '是' : '否') },
                    {
                      title: '操作', width: 140,
                      render: (_: unknown, r: PromptRouting) => (
                        <Space size={4}>
                          <Button size="small" onClick={() => { routeForm.setFieldsValue(r); setRouteModal(r) }}>编辑</Button>
                          <Popconfirm title="删除该规则？" onConfirm={async () => { await deletePromptRouting(r.id); loadRoutes() }}>
                            <Button size="small" danger>删除</Button>
                          </Popconfirm>
                        </Space>
                      ),
                    },
                  ]}
                />
              </>
            ),
          },
        ]}
      />

      {/* 模板查看/编辑 */}
      <Drawer
        title={creating ? '新增模板' : editing ? `编辑模板：${current?.name}` : `查看模板：${current?.name}`}
        open={drawerOpen} onClose={() => setDrawerOpen(false)} width={720}
        extra={editing && <Button type="primary" onClick={handleSave}>保存</Button>}
      >
        <Form form={form} layout="vertical">
          {creating && (
            <>
              <Form.Item name="name" label="模板名（唯一）" rules={[{ required: true }]}><Input /></Form.Item>
              <Form.Item name="category" label="分类">
                <Select options={['diagnosis', 'repair_plan', 'risk_assessment', 'verification'].map((c) => ({ value: c, label: c }))} />
              </Form.Item>
            </>
          )}
          <Form.Item name="scene" label="场景" tooltip="用于前端分组与路由；诊断类模板按场景归类">
            <Select allowClear options={SCENES} disabled={!editing} />
          </Form.Item>
          {!creating && (
            <Descriptions column={2} style={{ marginBottom: 12 }} size="small">
              <Descriptions.Item label="分类">{current?.category}</Descriptions.Item>
              <Descriptions.Item label="版本">{current?.version}</Descriptions.Item>
            </Descriptions>
          )}
          <Form.Item name="system_prompt" label="System Prompt（可用 {{fragment:key}} 引用公共片段）" rules={[{ required: true }]}>
            <Input.TextArea rows={5} disabled={!editing} />
          </Form.Item>
          <Form.Item name="user_prompt_template" label="User Prompt 模板" rules={[{ required: true }]}>
            <Input.TextArea rows={7} disabled={!editing} />
          </Form.Item>
          <Form.Item name="sample_vars" label="预览样例变量（JSON，可选）" tooltip="预览渲染时预填这些变量">
            <Input.TextArea rows={3} disabled={!editing} placeholder='{"cluster_name":"测试环境","namespace":"qa-bgs"}' />
          </Form.Item>
          {!creating && <Form.Item name="variables" label="变量列表"><Input disabled /></Form.Item>}
        </Form>
      </Drawer>

      {/* 预览渲染 */}
      <Modal
        title={`预览渲染：${previewTpl?.name}`} open={previewOpen} onCancel={() => setPreviewOpen(false)}
        onOk={doPreview} okText="渲染" width={820}
      >
        <div style={{ marginBottom: 8, color: '#8c8c8c', fontSize: 12 }}>样例变量（JSON）</div>
        <Input.TextArea rows={6} value={varsText} onChange={(e) => setVarsText(e.target.value)} style={{ fontFamily: 'monospace', fontSize: 12 }} />
        <Button type="primary" onClick={doPreview} style={{ marginTop: 8 }}>渲染</Button>
        {previewOut && (
          <div style={{ marginTop: 12 }}>
            {previewOut.missing_vars.length > 0 && (
              <Alert type="warning" showIcon style={{ marginBottom: 8 }}
                message={`缺少变量：${previewOut.missing_vars.join(', ')}（渲染后会变成空串）`} />
            )}
            <div style={{ fontSize: 12, color: '#8c8c8c', marginBottom: 4 }}>System Prompt</div>
            <pre style={{ maxHeight: 160, overflow: 'auto', background: '#fafafa', padding: 8, fontSize: 12 }}>{previewOut.system_prompt}</pre>
            <div style={{ fontSize: 12, color: '#8c8c8c', margin: '8px 0 4px' }}>User Prompt</div>
            <pre style={{ maxHeight: 240, overflow: 'auto', background: '#fafafa', padding: 8, fontSize: 12 }}>{previewOut.user_prompt}</pre>
          </div>
        )}
      </Modal>

      {/* 片段编辑 */}
      <Modal
        title={fragModal ? `编辑片段：${fragModal.key}` : '新增片段'} open={fragModal !== undefined}
        onCancel={() => setFragModal(undefined)} onOk={saveFrag} width={680}
      >
        <Form form={fragForm} layout="vertical">
          {!fragModal && <Form.Item name="key" label="Key（模板里写 {{fragment:key}}）" rules={[{ required: true }]}><Input /></Form.Item>}
          <Form.Item name="name" label="名称"><Input /></Form.Item>
          <Form.Item name="category" label="分类"><Input placeholder="common / format / redline" /></Form.Item>
          <Form.Item name="content" label="内容" rules={[{ required: true }]}><Input.TextArea rows={8} /></Form.Item>
          <Form.Item name="enabled" label="启用" initialValue={true}><Select options={[{ value: true, label: '启用' }, { value: false, label: '停用' }]} /></Form.Item>
        </Form>
      </Modal>

      {/* 路由编辑 */}
      <Modal
        title={routeModal ? '编辑路由规则' : '新增路由规则'} open={routeModal !== undefined}
        onCancel={() => setRouteModal(undefined)} onOk={saveRoute} width={620}
      >
        <Form form={routeForm} layout="vertical">
          <Form.Item name="match_type" label="匹配类型">
            <Select options={[{ value: 'fault_type', label: 'fault_type' }, { value: 'scene', label: 'scene' }]} />
          </Form.Item>
          <Form.Item name="match_value" label="匹配值（default 模式留空）"><Input placeholder="如 CrashLoopBackOff / AppOOM / PodRestart" /></Form.Item>
          <Form.Item name="match_mode" label="匹配模式">
            <Select options={[{ value: 'exact', label: 'exact 精确' }, { value: 'prefix', label: 'prefix 前缀' }, { value: 'default', label: 'default 兜底' }]} />
          </Form.Item>
          <Form.Item name="template_name" label="模板名" rules={[{ required: true }]}><Input placeholder="如 pod_crash_diagnosis" /></Form.Item>
          <Form.Item name="priority" label="优先级（越小越优先）"><InputNumber min={1} max={9999} style={{ width: '100%' }} /></Form.Item>
          <Form.Item name="enabled" label="启用" initialValue={true}>
            <Select options={[{ value: true, label: '启用' }, { value: false, label: '停用' }]} />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
