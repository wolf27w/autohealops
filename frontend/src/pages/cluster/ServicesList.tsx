// 服务/路由列表页：两个子 Tab（服务 / 应用路由），操作收进 ⋯（编辑 YAML / 编辑设置 / 删除）
import { useEffect, useState } from 'react'
import { Card, Table, Tag, Button, Dropdown, Modal, message, Tabs } from 'antd'
import { MoreOutlined } from '@ant-design/icons'
import { getMiscResources, deleteObject } from '../../services/api'
import ConfigObjectYamlModal, { type ConfigObjectTarget } from '../../components/ConfigObjectYamlModal'
import ServiceSettingsModal from '../../components/ServiceSettingsModal'
import IngressSettingsModal from '../../components/IngressSettingsModal'

interface MiscRow { namespace: string; name: string; [key: string]: unknown }
interface MiscData {
  services: MiscRow[]
  ingresses: MiscRow[]
  configmaps: MiscRow[]
  secrets: MiscRow[]
  pvcs: MiscRow[]
}

const fmt = (v?: unknown) => (v ? String(v).slice(0, 19).replace('T', ' ') : '-')
const SERVICE_TYPE_COLOR: Record<string, string> = { LoadBalancer: 'blue', NodePort: 'green', ClusterIP: 'default', ExternalName: 'purple' }

export default function ServicesList({ clusterId }: { clusterId: number }) {
  const [tab, setTab] = useState<string>('services')
  const [misc, setMisc] = useState<MiscData>({ services: [], ingresses: [], configmaps: [], secrets: [], pvcs: [] })
  const [loading, setLoading] = useState(false)
  const [yamlObj, setYamlObj] = useState<ConfigObjectTarget | null>(null)
  const [svcObj, setSvcObj] = useState<ConfigObjectTarget | null>(null)
  const [ingObj, setIngObj] = useState<ConfigObjectTarget | null>(null)

  const load = () => {
    setLoading(true)
    getMiscResources(clusterId)
      .then((d) => setMisc(d as unknown as MiscData))
      .catch(() => setMisc({ services: [], ingresses: [], configmaps: [], secrets: [], pvcs: [] }))
      .finally(() => setLoading(false))
  }
  useEffect(() => { load() }, [clusterId]) // eslint-disable-line react-hooks/exhaustive-deps

  const handleDelete = (kind: string, ns: string, name: string) => {
    const label = kind === 'service' ? '服务' : '路由'
    Modal.confirm({
      title: `删除${label} ${name}？`,
      content: kind === 'service'
        ? '删除服务会解除其负载均衡/端点，不影响后端 Pod。'
        : '删除路由后，对应域名/路径的入口将失效。',
      okText: '确认删除',
      okButtonProps: { danger: true },
      onOk: () => deleteObject(clusterId, kind, ns, name)
        .then(() => { message.success('已删除'); load() })
        .catch(() => message.error('删除失败')),
    })
  }

  const dropdown = (kind: string, r: MiscRow) => (
    <Dropdown trigger={['click']} menu={{
      items: [
        { key: 'settings', label: '编辑设置' },
        { key: 'yaml', label: '编辑 YAML' },
        { type: 'divider' as const },
        { key: 'delete', label: '删除', danger: true },
      ],
      onClick: ({ key }) => {
        const t = { clusterId, kind, namespace: r.namespace, name: r.name }
        if (key === 'settings') { kind === 'service' ? setSvcObj(t) : setIngObj(t) }
        else if (key === 'yaml') setYamlObj(t)
        else if (key === 'delete') handleDelete(kind, r.namespace, r.name)
      },
    }}>
      <Button size="small" type="link"><MoreOutlined /></Button>
    </Dropdown>
  )

  const serviceColumns = [
    { title: '名称', dataIndex: 'name' },
    { title: '命名空间', dataIndex: 'namespace', width: 180 },
    { title: '类型', dataIndex: 'type', width: 130, render: (v: string) => (
      <Tag color={SERVICE_TYPE_COLOR[v] || 'default'}>{v}</Tag>
    ) },
    { title: '端口', dataIndex: 'ports', render: (v: string) => v || '-' },
    { title: '创建时间', key: 'created_at', dataIndex: 'created_at', width: 170, render: fmt },
    { title: '操作', width: 70, render: (_: unknown, r: MiscRow) => dropdown('service', r) },
  ]

  const ingressColumns = [
    { title: '名称', dataIndex: 'name' },
    { title: '命名空间', dataIndex: 'namespace', width: 180 },
    { title: '类型', dataIndex: 'class', width: 130, render: (v: string) => (v ? <Tag>{v}</Tag> : '-') },
    { title: 'Hosts', dataIndex: 'hosts', render: (v: string) => v || '-' },
    { title: '创建时间', key: 'created_at', dataIndex: 'created_at', width: 170, render: fmt },
    { title: '操作', width: 70, render: (_: unknown, r: MiscRow) => dropdown('ingress', r) },
  ]

  const items = [
    {
      key: 'services',
      label: '服务',
      children: (
        <Table
          rowKey={(r) => `${r.namespace}-${r.name}`}
          size="small"
          loading={loading}
          dataSource={misc.services}
          columns={serviceColumns}
          pagination={{ pageSize: 20, showSizeChanger: true, pageSizeOptions: ['10', '20', '50', '100'], showTotal: (t: number) => `共 ${t} 条`, size: 'small' }}
          locale={{ emptyText: '无 Service' }}
        />
      ),
    },
    {
      key: 'ingresses',
      label: '应用路由',
      children: (
        <Table
          rowKey={(r) => `${r.namespace}-${r.name}`}
          size="small"
          loading={loading}
          dataSource={misc.ingresses}
          columns={ingressColumns}
          pagination={{ pageSize: 20, showSizeChanger: true, pageSizeOptions: ['10', '20', '50', '100'], showTotal: (t: number) => `共 ${t} 条`, size: 'small' }}
          locale={{ emptyText: '无 Ingress' }}
        />
      ),
    },
  ]

  return (
    <div>
      <Card size="small">
        <Tabs activeKey={tab} onChange={setTab} items={items} />
      </Card>

      <ConfigObjectYamlModal target={yamlObj} onClose={() => setYamlObj(null)} onSaved={load} />
      <ServiceSettingsModal target={svcObj} onClose={() => setSvcObj(null)} onSaved={load} />
      <IngressSettingsModal target={ingObj} onClose={() => setIngObj(null)} onSaved={load} />
    </div>
  )
}
