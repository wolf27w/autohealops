// 服务列表页：列对齐 KubeSphere 3.4.1（名称 / 项目 / 内部访问 / 外部访问 / 创建时间）
// 行操作：编辑信息 / 编辑 YAML / 编辑服务 / 编辑外部访问 / 删除（删除置底）
import { useEffect, useState } from 'react'
import { Card, Table, Button, Dropdown, Modal, message, Select, Space } from 'antd'
import { MoreOutlined } from '@ant-design/icons'
import { getMiscResources, deleteObject } from '../../services/api'
import { useClusterNamespace, NS_ALL, nsParam } from './NamespaceContext'
import ConfigObjectYamlModal, { type ConfigObjectTarget } from '../../components/ConfigObjectYamlModal'
import ServiceSettingsModal from '../../components/ServiceSettingsModal'
import ObjectMetaModal from '../../components/ObjectMetaModal'

interface MiscRow { namespace: string; name: string; [key: string]: unknown }
interface MiscData {
  services: MiscRow[]
  ingresses: MiscRow[]
  configmaps: MiscRow[]
  secrets: MiscRow[]
  pvcs: MiscRow[]
}

const fmt = (v?: unknown) => (v ? String(v).slice(0, 19).replace('T', ' ') : '-')

// ports 字段形如 "80:30080/TCP, 443:30443/TCP" → 内部访问取 "port/protocol"
const portForInternal = (ports?: unknown): string =>
  ((ports as string) || '').split(',').map((p) => {
    const t = p.trim()
    const m = t.match(/^(\d+):\d+\/(.+)$/)
    return m ? `${m[1]}/${m[2]}` : t
  }).filter(Boolean).join(', ')

export default function ServicesList({ clusterId }: { clusterId: number }) {
  const { ns, setNs, namespaces, loading: nsLoading } = useClusterNamespace()
  const [misc, setMisc] = useState<MiscData>({ services: [], ingresses: [], configmaps: [], secrets: [], pvcs: [] })
  const [loading, setLoading] = useState(false)
  const [yamlObj, setYamlObj] = useState<ConfigObjectTarget | null>(null)
  const [infoObj, setInfoObj] = useState<ConfigObjectTarget | null>(null)
  const [svcObj, setSvcObj] = useState<ConfigObjectTarget | null>(null)
  const [svcMode, setSvcMode] = useState<'service' | 'external'>('service')

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
      title: `删除服务 ${name}？`,
      content: '删除服务会解除其负载均衡/端点，不影响后端 Pod。',
      okText: '确认删除',
      okButtonProps: { danger: true },
      onOk: () => deleteObject(clusterId, 'service', nsName, name)
        .then(() => { message.success('已删除'); load() })
        .catch(() => message.error('删除失败')),
    })
  }

  const dropdown = (r: MiscRow) => (
    <Dropdown trigger={['click']} menu={{
      items: [
        { key: 'info', label: '编辑信息' },
        { key: 'yaml', label: '编辑 YAML' },
        { key: 'service', label: '编辑服务' },
        { key: 'external', label: '编辑外部访问' },
        { type: 'divider' as const },
        { key: 'delete', label: '删除', danger: true },
      ],
      onClick: ({ key }) => {
        const t = { clusterId, kind: 'service', namespace: r.namespace, name: r.name }
        if (key === 'info') setInfoObj(t)
        else if (key === 'yaml') setYamlObj(t)
        else if (key === 'service') { setSvcMode('service'); setSvcObj(t) }
        else if (key === 'external') { setSvcMode('external'); setSvcObj(t) }
        else if (key === 'delete') handleDelete(r.namespace, r.name)
      },
    }}>
      <Button size="small" type="link"><MoreOutlined /></Button>
    </Dropdown>
  )

  const columns = [
    { title: '名称', dataIndex: 'name' },
    { title: '项目', dataIndex: 'namespace', width: 180 },
    { title: '内部访问', key: 'internal', render: (_: unknown, r: MiscRow) => {
        const ip = (r.cluster_ip as string) || ''
        const ports = portForInternal(r.ports)
        if (!ip) return ports || '-'
        return ports ? `${ip}:${ports}` : ip
      } },
    { title: '外部访问', dataIndex: 'external_ip', render: (v: string) => v || '-' },
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
            onClick={() => { setSvcMode('service'); setSvcObj({ clusterId, kind: 'service', namespace: ns === NS_ALL ? 'default' : ns, name: '', create: true }) }}>
            创建服务
          </Button>
        </div>
        <Table
          rowKey={(r) => `${r.namespace}-${r.name}`}
          size="small"
          loading={loading}
          dataSource={misc.services}
          columns={columns}
          pagination={{ pageSize: 20, showSizeChanger: true, pageSizeOptions: ['10', '20', '50', '100'], showTotal: (t: number) => `共 ${t} 条`, size: 'small' }}
          locale={{ emptyText: '无 Service' }}
        />
      </Card>

      <ConfigObjectYamlModal target={yamlObj} onClose={() => setYamlObj(null)} onSaved={load} />
      <ObjectMetaModal open={!!infoObj} clusterId={clusterId} kind="service" namespace={infoObj?.namespace ?? ''} name={infoObj?.name ?? ''} onClose={() => setInfoObj(null)} onSaved={load} />
      <ServiceSettingsModal target={svcObj} mode={svcMode} onClose={() => setSvcObj(null)} onSaved={load} />
    </div>
  )
}
