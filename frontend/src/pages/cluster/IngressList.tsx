// 应用路由列表页（Ingress）：列对齐 KubeSphere 3.4.1（名称 / 网关地址 / 项目 / 创建时间）
// 行操作：编辑信息 / 编辑 YAML / 编辑路由规则 / 编辑注解 / 编辑 TLS（我们追加）/ 删除（置底）
import { useEffect, useState } from 'react'
import { Card, Table, Button, Dropdown, Modal, message, Select, Space, Form, Input } from 'antd'
import { MoreOutlined } from '@ant-design/icons'
import { getMiscResources, deleteObject, getObjectDetail, updateObjectMetadata } from '../../services/api'
import { useClusterNamespace, NS_ALL, nsParam } from './NamespaceContext'
import ConfigObjectYamlModal, { type ConfigObjectTarget } from '../../components/ConfigObjectYamlModal'
import IngressSettingsModal from '../../components/IngressSettingsModal'

const ALIAS_KEY = 'kubesphere.io/alias-name'
const DESC_KEY = 'kubesphere.io/description'

interface MiscRow { namespace: string; name: string; [key: string]: unknown }
interface MiscData {
  services: MiscRow[]
  ingresses: MiscRow[]
  configmaps: MiscRow[]
  secrets: MiscRow[]
  pvcs: MiscRow[]
}

const fmt = (v?: unknown) => (v ? String(v).slice(0, 19).replace('T', ' ') : '-')

// 编辑信息：名称只读 + 别名/描述（对齐 KubeSphere「编辑信息」入口）
function EditInfoModal({ target, kindLabel, onClose, onSaved }: {
  target: ConfigObjectTarget | null
  kindLabel: string
  onClose: () => void
  onSaved?: () => void
}) {
  const [form] = Form.useForm()
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    if (!target) return
    setLoading(true)
    getObjectDetail(target.clusterId, target.kind, target.namespace, target.name)
      .then((d) => {
        const ann = ((d as { annotations?: Record<string, string> }).annotations) || {}
        form.setFieldsValue({ name: target.name, alias: ann[ALIAS_KEY] || '', description: ann[DESC_KEY] || '' })
      })
      .catch(() => message.error('加载失败'))
      .finally(() => setLoading(false))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [target])

  const submit = async () => {
    if (!target) return
    const v = await form.validateFields()
    setSaving(true)
    try {
      const d = (await getObjectDetail(target.clusterId, target.kind, target.namespace, target.name)) as
        { labels?: Record<string, string>; annotations?: Record<string, string> }
      const ann = { ...(d.annotations || {}) }
      ann[ALIAS_KEY] = (v.alias || '').trim()
      ann[DESC_KEY] = (v.description || '').trim()
      await updateObjectMetadata(target.clusterId, target.kind, target.namespace, target.name, d.labels || {}, ann)
      message.success('已保存')
      onSaved?.()
      onClose()
    } catch {
      message.error('保存失败')
    } finally {
      setSaving(false)
    }
  }

  return (
    <Modal title={`编辑信息：${target?.name || ''}（${kindLabel}）`} open={!!target} onCancel={onClose}
      onOk={submit} okText="保存" confirmLoading={saving} width={640}>
      <Form form={form} layout="vertical">
        <Form.Item name="name" label="名称"><Input disabled /></Form.Item>
        <Form.Item name="alias" label="别名" rules={[{
          validator: (_: unknown, val: string) => {
            if (!val || !val.trim()) return Promise.resolve()
            const t = val.trim()
            return /^[\u4e00-\u9fa5A-Za-z0-9]([\u4e00-\u9fa5A-Za-z0-9-]*[\u4e00-\u9fa5A-Za-z0-9])?$/.test(t) && t.length <= 63
              ? Promise.resolve()
              : Promise.reject(new Error('别名只能包含中文、字母、数字和连字符（-），最长 63 个字符'))
          },
        }]}>
          <Input placeholder="请输入别名" />
        </Form.Item>
        <Form.Item name="description" label="描述" rules={[{ max: 256, message: '描述最长 256 个字符' }]}>
          <Input.TextArea rows={3} placeholder="请输入描述" />
        </Form.Item>
      </Form>
    </Modal>
  )
}

export default function IngressList({ clusterId }: { clusterId: number }) {
  const { ns, setNs, namespaces, loading: nsLoading } = useClusterNamespace()
  const [misc, setMisc] = useState<MiscData>({ services: [], ingresses: [], configmaps: [], secrets: [], pvcs: [] })
  const [loading, setLoading] = useState(false)
  const [yamlObj, setYamlObj] = useState<ConfigObjectTarget | null>(null)
  const [infoObj, setInfoObj] = useState<ConfigObjectTarget | null>(null)
  const [ingObj, setIngObj] = useState<ConfigObjectTarget | null>(null)
  const [ingMode, setIngMode] = useState<'rules' | 'annotations' | 'tls'>('rules')

  const load = () => {
    setLoading(true)
    getMiscResources(clusterId, nsParam(ns))
      .then((d) => setMisc(d as unknown as MiscData))
      .catch(() => setMisc({ services: [], ingresses: [], configmaps: [], secrets: [], pvcs: [] }))
      .finally(() => setLoading(false))
  }
  useEffect(() => { load() }, [clusterId, ns]) // eslint-disable-line react-hooks/exhaustive-deps

  const handleDelete = (nsName: string, name: string) => {
    Modal.confirm({
      title: `删除路由 ${name}？`,
      content: '删除路由后，对应域名/路径的入口将失效。',
      okText: '确认删除',
      okButtonProps: { danger: true },
      onOk: () => deleteObject(clusterId, 'ingress', nsName, name)
        .then(() => { message.success('已删除'); load() })
        .catch(() => message.error('删除失败')),
    })
  }

  const dropdown = (r: MiscRow) => (
    <Dropdown trigger={['click']} menu={{
      items: [
        { key: 'info', label: '编辑信息' },
        { key: 'yaml', label: '编辑 YAML' },
        { key: 'rules', label: '编辑路由规则' },
        { key: 'annotations', label: '编辑注解' },
        { key: 'tls', label: '编辑 TLS' },   // 我们追加的入口（KubeSphere 没有，功能不砍）
        { type: 'divider' as const },
        { key: 'delete', label: '删除', danger: true },
      ],
      onClick: ({ key }) => {
        const t = { clusterId, kind: 'ingress', namespace: r.namespace, name: r.name }
        if (key === 'info') setInfoObj(t)
        else if (key === 'yaml') setYamlObj(t)
        else if (key === 'rules') { setIngMode('rules'); setIngObj(t) }
        else if (key === 'annotations') { setIngMode('annotations'); setIngObj(t) }
        else if (key === 'tls') { setIngMode('tls'); setIngObj(t) }
        else if (key === 'delete') handleDelete(r.namespace, r.name)
      },
    }}>
      <Button size="small" type="link"><MoreOutlined /></Button>
    </Dropdown>
  )

  const columns = [
    { title: '名称', dataIndex: 'name' },
    { title: '网关地址', dataIndex: 'address', render: (v: string) => v || '-' },
    { title: '项目', dataIndex: 'namespace', width: 180 },
    { title: '创建时间', key: 'created_at', dataIndex: 'created_at', width: 170, render: fmt },
    { title: '操作', width: 70, render: (_: unknown, r: MiscRow) => dropdown(r) },
  ]

  return (
    <div>
      <Card size="small">
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
          <Space size={8}>
            <span style={{ color: '#8aa196', fontSize: 12 }}>命名空间</span>
            <Select
              size="small"
              style={{ width: 200 }}
              value={ns}
              onChange={(v) => setNs(v as string)}
              showSearch
              optionFilterProp="label"
              loading={nsLoading}
              options={[
                { value: NS_ALL, label: '全部命名空间' },
                ...namespaces.map((n) => ({ value: n, label: n })),
              ]}
            />
          </Space>
          <Button type="primary" size="small"
            onClick={() => { setIngMode('rules'); setIngObj({ clusterId, kind: 'ingress', namespace: ns === NS_ALL ? 'default' : ns, name: '', create: true }) }}>
            创建路由
          </Button>
        </div>
        <Table
          rowKey={(r) => `${r.namespace}-${r.name}`}
          size="small"
          loading={loading}
          dataSource={misc.ingresses}
          columns={columns}
          pagination={{ pageSize: 20, showSizeChanger: true, pageSizeOptions: ['10', '20', '50', '100'], showTotal: (t: number) => `共 ${t} 条`, size: 'small' }}
          locale={{ emptyText: '无 Ingress' }}
        />
      </Card>

      <ConfigObjectYamlModal target={yamlObj} onClose={() => setYamlObj(null)} onSaved={load} />
      <EditInfoModal target={infoObj} kindLabel="路由" onClose={() => setInfoObj(null)} onSaved={load} />
      <IngressSettingsModal target={ingObj} mode={ingMode} onClose={() => setIngObj(null)} onSaved={load} />
    </div>
  )
}
