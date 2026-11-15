// 存储与配置页：持久卷声明（PVC）/ 配置字典（ConfigMap）/ 密钥（Secret）/ 存储类（StorageClass）四个子页
// 数据源 getMiscResources(clusterId) → { configmaps, secrets, pvcs, storage_classes }
// 每个子页只展示对应那一个 Tab（由 initialTab 决定）；操作复用现有弹窗组件
// 创建：PVC 走 PvcCreateModal，ConfigMap/Secret 走两步向导 ConfigObjectCreateModal（可在向导内切 YAML）；存储类不提供创建
import { useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { Card, Table, Tabs, Button, Space, Tag, Tooltip, message, Dropdown, Modal, Select } from 'antd'
import { MoreOutlined, ReloadOutlined, PlusOutlined } from '@ant-design/icons'
import { getMiscResources, deleteObject } from '../../services/api'
import { useClusterNamespace, NS_ALL, nsParam } from './NamespaceContext'
import ConfigObjectYamlModal, { type ConfigObjectTarget } from '../../components/ConfigObjectYamlModal'
import ConfigObjectInfoModal from '../../components/ConfigObjectInfoModal'
import ConfigObjectDataModal from '../../components/ConfigObjectDataModal'
import ObjectMetaModal from '../../components/ObjectMetaModal'
import ConfigObjectCreateModal from '../../components/ConfigObjectCreateModal'
import PvcCreateModal from '../../components/PvcCreateModal'

interface MiscRow {
  namespace: string
  name: string
  [key: string]: unknown
}

interface StorageClassRow {
  name: string
  provisioner?: string
  is_default?: boolean
  pvc_count?: number
  allow_expansion?: boolean
  supports_clone?: boolean
  supports_snapshot?: boolean
  created_at?: string
}

interface MiscData {
  configmaps: MiscRow[]
  secrets: MiscRow[]
  pvcs: MiscRow[]
  storage_classes: StorageClassRow[]
}

const EMPTY: MiscData = { configmaps: [], secrets: [], pvcs: [], storage_classes: [] }

const fmtTs = (v?: unknown) => (v ? String(v).slice(0, 19).replace('T', ' ') : '-')

export type StorageTab = 'pvcs' | 'configmaps' | 'secrets' | 'storageclasses'

const PAGE_TITLE: Record<StorageTab, string> = {
  pvcs: '持久卷声明',
  configmaps: '配置字典',
  secrets: '保密字典',
  storageclasses: '存储类',
}

export default function StorageConfig({ clusterId, initialTab = 'pvcs' }: { clusterId: number; initialTab?: StorageTab }) {
  const { ns, setNs, namespaces, loading: nsLoading } = useClusterNamespace()
  const [misc, setMisc] = useState<MiscData>(EMPTY)
  const [loading, setLoading] = useState(false)
  const [activeKey, setActiveKey] = useState<StorageTab>(initialTab)
  // 弹窗目标
  const [yamlObj, setYamlObj] = useState<ConfigObjectTarget | null>(null)    // 编辑/创建 YAML（ConfigMap/Secret）
  const [infoObj, setInfoObj] = useState<ConfigObjectTarget | null>(null)    // 查看信息（标签/注解）
  const [metaObj, setMetaObj] = useState<ConfigObjectTarget | null>(null)    // 编辑信息（别名/描述）
  const [dataObj, setDataObj] = useState<ConfigObjectTarget | null>(null)    // 编辑设置（数据）
  const [createObj, setCreateObj] = useState<ConfigObjectTarget | null>(null) // 创建向导（ConfigMap/Secret）
  const [pvcObj, setPvcObj] = useState<ConfigObjectTarget | null>(null)      // 创建 PVC

  // 存储类是集群级资源，不按命名空间筛选（始终拉全量）
  const isStorageClasses = initialTab === 'storageclasses'
  const effNs = isStorageClasses ? '' : nsParam(ns)

  const load = () => {
    setLoading(true)
    getMiscResources(clusterId, effNs)
      .then((d) => setMisc((d as unknown as MiscData) || EMPTY))
      .catch(() => setMisc(EMPTY))
      .finally(() => setLoading(false))
  }
  useEffect(load, [clusterId, effNs]) // eslint-disable-line react-hooks/exhaustive-deps

  const reloadMisc = () => {
    getMiscResources(clusterId, effNs)
      .then((d) => setMisc((d as unknown as MiscData) || EMPTY))
      .catch(() => { /* ignore */ })
  }

  const handleDelete = (kind: string, ns: string, name: string) => {
    Modal.confirm({
      title: `删除 ${name}？`,
      content: kind === 'pvc' ? '会释放存储，数据可能丢失。' : undefined,
      okButtonProps: { danger: true },
      onOk: async () => {
        try {
          await deleteObject(clusterId, kind, ns, name)
          message.success('已删除')
          reloadMisc()
        } catch {
          message.error('删除失败')
        }
      },
    })
  }

  // 操作 ⋯：编辑信息 / 编辑 YAML / 编辑设置（仅 ConfigMap·Secret）/ 查看信息 / 删除（置底）
  const makeActions = (kind: 'configmap' | 'secret' | 'pvc', withData: boolean) =>
    (r: MiscRow) => {
      const t: ConfigObjectTarget = { clusterId, kind, namespace: r.namespace, name: r.name }
      const items: { key: string; label: string; danger?: boolean }[] = [
        { key: 'meta', label: '编辑信息' },
        { key: 'yaml', label: '编辑 YAML' },
        ...(withData ? [{ key: 'data', label: '编辑设置' }] : []),
        { key: 'info', label: '查看信息' },
        { key: 'delete', label: '删除', danger: true },
      ]
      return (
        <Dropdown
          trigger={['click']}
          menu={{
            items,
            onClick: ({ key }) => {
              if (key === 'meta') setMetaObj(t)
              else if (key === 'yaml') setYamlObj(t)
              else if (key === 'data') setDataObj(t)
              else if (key === 'info') setInfoObj(t)
              else if (key === 'delete') handleDelete(kind, r.namespace, r.name)
            },
          }}
        >
          <Button size="small" type="link" icon={<MoreOutlined />} />
        </Dropdown>
      )
    }

  // 存储类操作 ⋯：编辑信息 / 删除（cluster-scoped，无命名空间）
  const makeStorageClassActions = (r: StorageClassRow) => {
    const t: ConfigObjectTarget = { clusterId, kind: 'storageclass', namespace: '-', name: r.name }
    const items: { key: string; label: string; danger?: boolean }[] = [
      { key: 'meta', label: '编辑信息' },
      { key: 'delete', label: '删除', danger: true },
    ]
    return (
      <Dropdown
        trigger={['click']}
        menu={{
          items,
          onClick: ({ key }) => {
            if (key === 'meta') setMetaObj(t)
            else if (key === 'delete') handleDelete('storageclass', '-', r.name)
          },
        }}
      >
        <Button size="small" type="link" icon={<MoreOutlined />} />
      </Dropdown>
    )
  }

  const pvcColumns = [
    { title: '名称', dataIndex: 'name' },
    { title: '项目', dataIndex: 'namespace', width: 180 },
    { title: '状态', dataIndex: 'status', width: 100, render: (v: string) => <Tag color={v === 'Bound' ? 'green' : v === 'Pending' ? 'orange' : 'red'}>{v || '-'}</Tag> },
    { title: '容量', dataIndex: 'capacity', width: 110, render: (v: string) => v || '-' },
    { title: '访问模式', dataIndex: 'access_modes', width: 140, render: (v: string) => v || '-' },
    {
      title: '挂载状态', dataIndex: 'mounted', width: 120,
      render: (v: boolean, r: MiscRow) => {
        const by = (r.mounted_by as string[] | undefined) || []
        const label = v ? '已挂载' : '未挂载'
        return (
          <Tooltip title={by.length ? `挂载 Pod：${by.join('、')}` : undefined}>
            <Tag color={v ? 'green' : 'default'}>{label}</Tag>
          </Tooltip>
        )
      },
    },
    { title: 'StorageClass', dataIndex: 'storage_class', width: 150, render: (v: string) => v || '-' },
    { title: '创建时间', dataIndex: 'created_at', width: 160, render: fmtTs },
    { title: '操作', width: 70, render: makeActions('pvc', false) },
  ]

  const cmColumns = [
    { title: '名称', dataIndex: 'name' },
    { title: '命名空间', dataIndex: 'namespace', width: 200 },
    { title: '字段', dataIndex: 'data_count', width: 110, render: (v: number) => (v === undefined ? '-' : `${v} 项`) },
    { title: '创建时间', dataIndex: 'created_at', width: 160, render: fmtTs },
    { title: '操作', width: 70, render: makeActions('configmap', true) },
  ]

  const secretColumns = [
    { title: '名称', dataIndex: 'name' },
    { title: '命名空间', dataIndex: 'namespace', width: 200 },
    { title: '类型', dataIndex: 'type', width: 220, render: (v: string) => v || '-' },
    { title: '字段数量', dataIndex: 'data_count', width: 110, render: (v: number) => (v === undefined ? '-' : `${v} 项`) },
    { title: '创建时间', dataIndex: 'created_at', width: 160, render: fmtTs },
    { title: '操作', width: 70, render: makeActions('secret', true) },
  ]

  // 允许克隆·快照·扩展：三者都支持 → 「支持」；都支持不了 → 「-」；否则逐项 Tag
  const renderCapabilities = (r: StorageClassRow) => {
    const { allow_expansion: exp, supports_clone: clone, supports_snapshot: snap } = r
    if (exp && clone && snap) return <Tag color="green">支持</Tag>
    if (!exp && !clone && !snap) return '-'
    return (
      <Space size={4}>
        {exp && <Tag>扩展</Tag>}
        {clone && <Tag>克隆</Tag>}
        {snap && <Tag>快照</Tag>}
      </Space>
    )
  }

  const scColumns = [
    { title: '名称', dataIndex: 'name' },
    { title: 'PVC 数', dataIndex: 'pvc_count', width: 100, render: (v: number) => (v === undefined ? '-' : String(v)) },
    { title: '默认存储类', dataIndex: 'is_default', width: 110, render: (v: boolean) => (v ? <Tag color="blue">默认</Tag> : '-') },
    { title: '允许克隆·快照·扩展', key: 'capabilities', width: 180, render: (_: unknown, r: StorageClassRow) => renderCapabilities(r) },
    { title: '供应者', dataIndex: 'provisioner', width: 200, render: (v: string) => v || '-' },
    { title: '创建时间', dataIndex: 'created_at', width: 160, render: fmtTs },
    { title: '操作', width: 70, render: (_: unknown, r: StorageClassRow) => makeStorageClassActions(r) },
  ]

  // 创建：PVC 走 PvcCreateModal；配置字典/密钥走两步向导 ConfigObjectCreateModal
  const onCreate = () => {
    const targetNs = ns === NS_ALL ? 'default' : ns
    if (activeKey === 'pvcs') setPvcObj({ clusterId, kind: 'pvc', namespace: targetNs, name: '', create: true })
    else if (activeKey === 'configmaps') setCreateObj({ clusterId, kind: 'configmap', namespace: targetNs, name: '', create: true })
    else setCreateObj({ clusterId, kind: 'secret', namespace: targetNs, name: '', create: true })
  }

  // 创建向导里点「编辑 YAML」→ 关向导、开 YAML 创建
  const onCreateEditYaml = (namespace: string) => {
    const kind = activeKey === 'configmaps' ? 'configmap' : 'secret'
    setYamlObj({ clusterId, kind, namespace, name: '', create: true })
  }

  const createLabel = activeKey === 'pvcs' ? '创建存储' : activeKey === 'configmaps' ? '创建配置字典' : '创建密钥'

  // 每个子页只展示对应那一个 Tab
  const tabByKey: Record<StorageTab, { key: StorageTab; label: string; children: ReactNode }> = {
    pvcs: {
      key: 'pvcs',
      label: '持久卷声明（PVC）',
      children: (
        <Table
          rowKey={(r) => `${r.namespace}-${r.name}`}
          size="small"
          loading={loading}
          dataSource={misc.pvcs}
          pagination={{ pageSize: 20 }}
          columns={pvcColumns}
          locale={{ emptyText: '无 PVC' }}
        />
      ),
    },
    configmaps: {
      key: 'configmaps',
      label: '配置字典（ConfigMap）',
      children: (
        <Table
          rowKey={(r) => `${r.namespace}-${r.name}`}
          size="small"
          loading={loading}
          dataSource={misc.configmaps}
          pagination={{ pageSize: 20 }}
          columns={cmColumns}
          locale={{ emptyText: '无配置字典' }}
        />
      ),
    },
    secrets: {
      key: 'secrets',
      label: '密钥（Secret）',
      children: (
        <Table
          rowKey={(r) => `${r.namespace}-${r.name}`}
          size="small"
          loading={loading}
          dataSource={misc.secrets}
          pagination={{ pageSize: 20 }}
          columns={secretColumns}
          locale={{ emptyText: '无密钥' }}
        />
      ),
    },
    storageclasses: {
      key: 'storageclasses',
      label: '存储类（StorageClass）',
      children: (
        <Table
          rowKey={(r) => r.name}
          size="small"
          loading={loading}
          dataSource={misc.storage_classes}
          pagination={{ pageSize: 20 }}
          columns={scColumns}
          locale={{ emptyText: '无存储类' }}
        />
      ),
    },
  }

  const tabs = [tabByKey[initialTab]]
  const pageTitle = PAGE_TITLE[initialTab]

  return (
    <div style={{ padding: 16 }}>
      <Card size="small" title={pageTitle}
        extra={
          <Space>
            {!isStorageClasses && (
              <>
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
              </>
            )}
            <Button icon={<ReloadOutlined />} onClick={load}>刷新</Button>
          </Space>
        }>
        <Tabs
          activeKey={activeKey}
          onChange={(k) => setActiveKey(k as StorageTab)}
          items={tabs}
          tabBarExtraContent={
            !isStorageClasses ? (
              <Button type="primary" size="small" icon={<PlusOutlined />} onClick={onCreate}>{createLabel}</Button>
            ) : undefined
          }
        />
      </Card>

      <ConfigObjectYamlModal target={yamlObj} onClose={() => setYamlObj(null)} onSaved={reloadMisc} />
      <ConfigObjectInfoModal target={infoObj} onClose={() => setInfoObj(null)} onSaved={reloadMisc} />
      <ObjectMetaModal open={!!metaObj} clusterId={clusterId} kind={metaObj?.kind ?? 'configmap'} namespace={metaObj?.namespace ?? ''} name={metaObj?.name ?? ''} onClose={() => setMetaObj(null)} onSaved={reloadMisc} />
      <ConfigObjectDataModal target={dataObj} onClose={() => setDataObj(null)} onSaved={reloadMisc} />
      <ConfigObjectCreateModal target={createObj} onClose={() => setCreateObj(null)} onSaved={reloadMisc} onEditYaml={onCreateEditYaml} />
      <PvcCreateModal target={pvcObj} onClose={() => setPvcObj(null)} onSaved={reloadMisc} />
    </div>
  )
}
