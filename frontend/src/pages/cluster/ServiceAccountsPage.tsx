// 集群内「配置 → 服务账户」页（R35 补充，2026-11-13 17:59）
// 列：名称 / 项目 / 角色 / 保密字典 / 创建时间
// 行操作：编辑信息 / 编辑 YAML / 修改角色 / 删除
import { useCallback, useEffect, useState } from 'react'
import {
  Button, Card, Dropdown, Form, Input, Modal, Popconfirm, Select, Space, Table, Tag, message,
} from 'antd'
import { MoreOutlined, ReloadOutlined } from '@ant-design/icons'
import {
  getServiceAccounts, getObjectYaml, applyObjectYaml, deleteObject, updateObjectMetadata,
} from '../../services/api'
import type { ServiceAccountRow } from '../../services/api'
import { useClusterNamespace, nsParam, NS_ALL } from './NamespaceContext'

// KubeSphere 默认的三个内置角色 + 自定义
const ROLE_OPTIONS = [
  { value: 'admin', label: 'admin（项目管理）' },
  { value: 'operator', label: 'operator（操作）' },
  { value: 'viewer', label: 'viewer（只读）' },
]

export default function ServiceAccountsPage({ clusterId }: { clusterId: number }) {
  const { ns, setNs, namespaces, loading: nsLoading } = useClusterNamespace()
  const [rows, setRows] = useState<ServiceAccountRow[]>([])
  const [loading, setLoading] = useState(false)
  const [keyword, setKeyword] = useState('')

  // 编辑信息
  const [metaTarget, setMetaTarget] = useState<ServiceAccountRow | null>(null)
  const [metaForm] = Form.useForm()
  const [metaSaving, setMetaSaving] = useState(false)

  // 编辑 YAML
  const [yamlTarget, setYamlTarget] = useState<ServiceAccountRow | null>(null)
  const [yamlText, setYamlText] = useState('')
  const [yamlLoading, setYamlLoading] = useState(false)
  const [yamlSaving, setYamlSaving] = useState(false)

  // 修改角色
  const [roleTarget, setRoleTarget] = useState<ServiceAccountRow | null>(null)
  const [roleValue, setRoleValue] = useState<string>('')
  const [roleSaving, setRoleSaving] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const d = await getServiceAccounts(clusterId, nsParam(ns))
      setRows(d?.items || [])
    } catch {
      setRows([])
    } finally {
      setLoading(false)
    }
  }, [clusterId, ns])

  useEffect(() => { load() }, [load])

  const view = rows.filter((r) => {
    if (!keyword.trim()) return true
    const k = keyword.trim().toLowerCase()
    return `${r.name} ${r.namespace} ${r.role}`.toLowerCase().includes(k)
  })

  const openMeta = async (r: ServiceAccountRow) => {
    setMetaTarget(r)
    metaForm.resetFields()
    try {
      const d = await getObjectYaml(clusterId, 'serviceaccount', r.namespace, r.name) as { yaml?: string }
      const alias = /kubesphere\.io\/alias-name:\s*"?([^"\n]+)"?/.exec(d?.yaml || '')?.[1] || ''
      const desc = /kubesphere\.io\/description:\s*"?([^"\n]+)"?/.exec(d?.yaml || '')?.[1] || ''
      metaForm.setFieldsValue({ alias, description: desc })
    } catch {
      /* 注解读取失败不阻断，允许直接改 */
    }
  }

  const saveMeta = async () => {
    if (!metaTarget) return
    const v = await metaForm.validateFields()
    setMetaSaving(true)
    try {
      // 保留现有 labels / 其它注解：先取最新 YAML 再覆盖合并
      const d = await getObjectYaml(clusterId, 'serviceaccount', metaTarget.namespace, metaTarget.name) as { yaml?: string }
      const yaml = d?.yaml || ''
      const labels: Record<string, string> = {}
      const annotations: Record<string, string> = {}
      // 粗略解析 metadata 下的 labels/annotations（两空格缩进的 key: value）
      const section = (name: string) => {
        const m = new RegExp(`\\n\\s{2}${name}:\\n((?:\\s{4}[^\\n]+\\n?)*)`).exec(yaml)
        return m?.[1] || ''
      }
      const parse = (block: string) => {
        const out: Record<string, string> = {}
        block.split('\n').forEach((line) => {
          const m = /^\s{4}([^:\s]+):\s*"?(.*?)"?\s*$/.exec(line)
          if (m) out[m[1]] = m[2]
        })
        return out
      }
      Object.assign(labels, parse(section('labels')))
      Object.assign(annotations, parse(section('annotations')))
      if (v.alias) annotations['kubesphere.io/alias-name'] = v.alias
      else delete annotations['kubesphere.io/alias-name']
      if (v.description) annotations['kubesphere.io/description'] = v.description
      else delete annotations['kubesphere.io/description']
      await updateObjectMetadata(clusterId, 'serviceaccount', metaTarget.namespace, metaTarget.name, labels, annotations)
      message.success('已保存')
      setMetaTarget(null)
      load()
    } catch (e) {
      message.error((e as Error).message || '保存失败')
    } finally {
      setMetaSaving(false)
    }
  }

  const openYaml = async (r: ServiceAccountRow) => {
    setYamlTarget(r)
    setYamlText('')
    setYamlLoading(true)
    try {
      const d = await getObjectYaml(clusterId, 'serviceaccount', r.namespace, r.name) as { yaml?: string }
      setYamlText(d?.yaml || '')
    } catch (e) {
      message.error((e as Error).message || 'YAML 获取失败')
    } finally {
      setYamlLoading(false)
    }
  }

  const saveYaml = async () => {
    if (!yamlTarget) return
    setYamlSaving(true)
    try {
      await applyObjectYaml(clusterId, 'serviceaccount', yamlText)
      message.success('已应用')
      setYamlTarget(null)
      load()
    } catch (e) {
      message.error((e as Error).message || '应用失败')
    } finally {
      setYamlSaving(false)
    }
  }

  const saveRole = async () => {
    if (!roleTarget) return
    setRoleSaving(true)
    try {
      const d = await getObjectYaml(clusterId, 'serviceaccount', roleTarget.namespace, roleTarget.name) as { yaml?: string }
      const yaml = d?.yaml || ''
      const annotations: Record<string, string> = {}
      // 保留其它注解，只改 kubesphere.io/role
      const m = /\n\s{2}annotations:\n((?:\s{4}[^\n]+\n?)*)/.exec(yaml)
      ;(m?.[1] || '').split('\n').forEach((line) => {
        const kv = /^\s{4}([^:\s]+):\s*"?(.*?)"?\s*$/.exec(line)
        if (kv) annotations[kv[1]] = kv[2]
      })
      if (roleValue) annotations['kubesphere.io/role'] = roleValue
      else delete annotations['kubesphere.io/role']
      await updateObjectMetadata(clusterId, 'serviceaccount', roleTarget.namespace, roleTarget.name, {}, annotations)
      message.success('已修改角色')
      setRoleTarget(null)
      load()
    } catch (e) {
      message.error((e as Error).message || '修改失败')
    } finally {
      setRoleSaving(false)
    }
  }

  const remove = async (r: ServiceAccountRow) => {
    try {
      await deleteObject(clusterId, 'serviceaccount', r.namespace, r.name)
      message.success('已删除')
      load()
    } catch (e) {
      message.error((e as Error).message || '删除失败')
    }
  }

  const columns = [
    { title: '名称', dataIndex: 'name', ellipsis: true },
    { title: '项目', dataIndex: 'namespace', width: 160, render: (v: string) => v || '-' },
    {
      title: '角色', dataIndex: 'role', width: 140,
      render: (v: string) => (v ? <Tag color="blue">{v}</Tag> : <Tag>未绑定</Tag>),
    },
    {
      title: '保密字典', dataIndex: 'secrets', width: 220, ellipsis: true,
      render: (v: string[]) => (v && v.length ? v.join('、') : '-'),
    },
    { title: '创建时间', dataIndex: 'created_at', width: 170, render: (v: string) => (v ? v.replace('T', ' ').slice(0, 19) : '-') },
    {
      title: '操作', width: 70, fixed: 'right' as const,
      render: (_: unknown, r: ServiceAccountRow) => (
        <Dropdown
          trigger={['click']}
          menu={{
            items: [
              { key: 'meta', label: '编辑信息' },
              { key: 'yaml', label: '编辑 YAML' },
              { key: 'role', label: '修改角色' },
              { type: 'divider' as const },
              { key: 'delete', label: '删除', danger: true },
            ],
            onClick: ({ key }) => {
              if (key === 'meta') openMeta(r)
              else if (key === 'yaml') openYaml(r)
              else if (key === 'role') { setRoleTarget(r); setRoleValue(r.role || '') }
              else if (key === 'delete') {
                Modal.confirm({
                  title: `删除服务账户 ${r.name}？`,
                  content: '删除后使用该账户的工作负载将无法认证，请确认。',
                  okButtonProps: { danger: true },
                  onOk: () => remove(r),
                })
              }
            },
          }}
        >
          <Button size="small" type="link"><MoreOutlined /></Button>
        </Dropdown>
      ),
    },
  ]

  return (
    <Card
      size="small"
      title="服务账户"
      extra={(
        <Space>
          <Select
            style={{ width: 200 }}
            loading={nsLoading}
            value={ns}
            onChange={setNs}
            options={[{ value: NS_ALL, label: '全部项目' }, ...namespaces.map((n) => ({ value: n, label: n }))]}
          />
          <Input
            allowClear
            placeholder="搜索名称 / 角色"
            style={{ width: 200 }}
            value={keyword}
            onChange={(e) => setKeyword(e.target.value)}
          />
          <Button icon={<ReloadOutlined />} onClick={load}>刷新</Button>
        </Space>
      )}
    >
      <Table<ServiceAccountRow>
        size="small"
        rowKey={(r) => `${r.namespace}/${r.name}`}
        loading={loading}
        dataSource={view}
        columns={columns}
        scroll={{ x: 900 }}
        locale={{ emptyText: '暂无服务账户' }}
      />

      {/* 编辑信息 */}
      <Modal
        title="编辑信息"
        open={!!metaTarget}
        onCancel={() => setMetaTarget(null)}
        onOk={saveMeta}
        confirmLoading={metaSaving}
        destroyOnClose
      >
        <Form form={metaForm} layout="vertical">
          <Form.Item label="名称">
            <Input value={metaTarget?.name} disabled />
          </Form.Item>
          <Form.Item name="alias" label="别名" rules={[{ max: 63, message: '最长 63 个字符' }]}>
            <Input placeholder="可选，便于识别的中文别名" />
          </Form.Item>
          <Form.Item name="description" label="描述" rules={[{ max: 256, message: '最长 256 个字符' }]}>
            <Input.TextArea autoSize={{ minRows: 2 }} placeholder="可选" />
          </Form.Item>
        </Form>
      </Modal>

      {/* 编辑 YAML */}
      <Modal
        title={`编辑 YAML · ${yamlTarget?.name || ''}`}
        open={!!yamlTarget}
        onCancel={() => setYamlTarget(null)}
        onOk={saveYaml}
        confirmLoading={yamlSaving}
        width={760}
        destroyOnClose
      >
        <Input.TextArea
          value={yamlText}
          onChange={(e) => setYamlText(e.target.value)}
          autoSize={{ minRows: 18, maxRows: 26 }}
          style={{ fontFamily: 'Menlo, Monaco, "Courier New", monospace', fontSize: 12 }}
          placeholder={yamlLoading ? '加载中…' : ''}
        />
      </Modal>

      {/* 修改角色 */}
      <Modal
        title={`修改角色 · ${roleTarget?.name || ''}`}
        open={!!roleTarget}
        onCancel={() => setRoleTarget(null)}
        onOk={saveRole}
        confirmLoading={roleSaving}
        destroyOnClose
      >
        <Select
          style={{ width: '100%' }}
          value={roleValue}
          onChange={setRoleValue}
          allowClear
          placeholder="选择角色（留空=清除角色注解）"
          options={ROLE_OPTIONS}
        />
        <div style={{ color: '#8c8c8c', fontSize: 12, marginTop: 8 }}>
          角色写入注解 <code>kubesphere.io/role</code>，用于列表展示与后续权限绑定。
        </div>
      </Modal>
    </Card>
  )
}
