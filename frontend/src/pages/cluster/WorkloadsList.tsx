// 集群内「工作负载列表」页组件（独立于 ClusterDetail 的工作负载 Tab）
// 数据来源：getClusterDetail（NamespaceDetail[]，按 kind 聚合），不请求新接口
import { useEffect, useMemo, useState, type Key } from 'react'
import { Table, Tag, Button, Space, Select, Input, Dropdown, Modal, InputNumber, message, Empty, Card, Tabs, Form } from 'antd'
import { MoreOutlined, SearchOutlined } from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'
import { useClusterNamespace, NS_ALL } from './NamespaceContext'
import {
  getClusterDetail,
  getWorkloadYaml,
  scaleWorkload,
  rolloutWorkload,
  deleteWorkload,
  batchWorkloadOp,
  getWorkloads,
  applyWorkloadYaml,
  recreateWorkload,
} from '../../services/api'
import WorkloadYamlModal, { type WorkloadTarget } from '../../components/WorkloadYamlModal'
import WorkloadCreateModal, { type WorkloadCreateTarget } from '../../components/WorkloadCreateModal'
import { parseYaml, yamlToValues, stringifyYaml, asMap, asStr, type YamlNode } from '../../components/workloadYaml'
import ReleaseHistoryModal from '../../components/ReleaseHistoryModal'
import ReleaseModal from '../../components/ReleaseModal'

interface ResourceItem {
  name: string
  namespace: string
  status?: string
  replicas?: number
  ready_replicas?: number
  update_time?: string
  cpu_request?: string | number
  cpu_limit?: string | number
  memory_request?: string | number
  memory_limit?: string | number
  publish_enabled?: boolean
  [key: string]: unknown
}

interface NamespaceDetail {
  namespace: string
  deployments: ResourceItem[]
  statefulsets: ResourceItem[]
  daemonsets: ResourceItem[]
  cronjobs: ResourceItem[]
  pods: ResourceItem[]
}

const KIND_TO_KEY: Record<string, keyof Pick<NamespaceDetail, 'deployments' | 'statefulsets' | 'daemonsets' | 'cronjobs'>> = {
  deployment: 'deployments',
  statefulset: 'statefulsets',
  daemonset: 'daemonsets',
  cronjob: 'cronjobs',
}

const KIND_LABEL: Record<string, string> = {
  deployment: '无状态',
  statefulset: '有状态',
  daemonset: '守护进程',
  cronjob: '定时任务',
}

const STATUS_COLOR: Record<string, string> = { 运行中: 'green', 更新中: 'orange', 已停止: 'default' }

const ALIAS_KEY = 'kubesphere.io/alias-name'
const DESC_KEY = 'kubesphere.io/description'

// 编辑信息：名称只读 + 别名/描述（对齐 KubeSphere「编辑信息」入口）。
// 工作负载没有专用 metadata 接口（updateObjectMetadata 仅限 configmap/secret/service/ingress/pvc），
// 这里走 getWorkloadYaml → 合并注解 → applyWorkloadYaml 整份回写，保留其它注解与 labels。
function EditInfoModal({ target, kindLabel, onClose, onSaved }: {
  target: WorkloadTarget | null
  kindLabel: string
  onClose: () => void
  onSaved?: () => void
}) {
  const [form] = Form.useForm()
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)

  // 剥离服务端管理字段（同 mergeWorkloadYaml），避免 server-side apply 冲突
  const cleanRoot = (root: YamlNode) => {
    const r = asMap(root)
    delete r.status
    const meta = asMap(r.metadata)
    for (const k of ['generation', 'selfLink', 'managedFields', 'resourceVersion', 'uid', 'creationTimestamp']) delete meta[k]
    r.metadata = meta
    return r
  }

  useEffect(() => {
    if (!target) return
    setLoading(true)
    getWorkloadYaml(target.clusterId, target.kind, target.namespace, target.name)
      .then((d) => {
        const root = parseYaml((d as { yaml?: string })?.yaml || '')
        const meta = asMap(asMap(root).metadata)
        const ann = asMap(meta.annotations)
        form.setFieldsValue({ name: target.name, alias: asStr(ann[ALIAS_KEY]), description: asStr(ann[DESC_KEY]) })
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
      const d = await getWorkloadYaml(target.clusterId, target.kind, target.namespace, target.name)
      const root = parseYaml((d as { yaml?: string })?.yaml || '')
      const r = cleanRoot(root)
      const meta = asMap(r.metadata)
      const ann = asMap(meta.annotations)
      ann[ALIAS_KEY] = (v.alias || '').trim()
      ann[DESC_KEY] = (v.description || '').trim()
      meta.annotations = ann
      await applyWorkloadYaml(target.clusterId, target.kind, target.namespace, target.name, stringifyYaml(r))
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

export default function WorkloadsList({ clusterId, kind }: { clusterId: number; kind: 'deployment' | 'statefulset' | 'daemonset' | 'cronjob' }) {
  const navigate = useNavigate()
  const { ns, setNs, namespaces, loading: nsLoading } = useClusterNamespace()
  const [details, setDetails] = useState<NamespaceDetail[]>([])
  const [loading, setLoading] = useState(false)
  const [searchText, setSearchText] = useState('')
  const [page, setPage] = useState(1)

  // 弹窗状态
  const [wlYaml, setWlYaml] = useState<WorkloadTarget | null>(null)
  const [releaseTarget, setReleaseTarget] = useState<WorkloadTarget | null>(null)
  const [releaseHistoryTarget, setReleaseHistoryTarget] = useState<WorkloadTarget | null>(null)
  const [wlCreate, setWlCreate] = useState<WorkloadCreateTarget | null>(null)
  const [wlEdit, setWlEdit] = useState<WorkloadCreateTarget | null>(null)
  const [scaleTarget, setScaleTarget] = useState<WorkloadTarget | null>(null)
  const [scaleValue, setScaleValue] = useState<number>(1)
  const [editInfoTarget, setEditInfoTarget] = useState<WorkloadTarget | null>(null)

  // 批量操作状态
  const [wlSel, setWlSel] = useState<Key[]>([])
  const [batchScaleOpen, setBatchScaleOpen] = useState(false)
  const [batchReplicas, setBatchReplicas] = useState<number>(1)

  const arrKey = KIND_TO_KEY[kind] ?? 'deployments'
  const isCronjob = kind === 'cronjob'
  const batchable = !isCronjob
  const scaleable = kind === 'deployment' || kind === 'statefulset'
  const restartable = kind === 'deployment' || kind === 'statefulset' || kind === 'daemonset'
  const kindLabel = KIND_LABEL[kind] ?? kind

  const load = () => {
    setLoading(true)
    getClusterDetail(clusterId)
      .then((d) => setDetails(d as NamespaceDetail[]))
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [clusterId, kind])

  // 5s 轮询刷新（组件卸载清理）
  useEffect(() => {
    const t = setInterval(() => {
      getClusterDetail(clusterId)
        .then((d) => setDetails(d as NamespaceDetail[]))
        .catch(() => { /* 轮询失败不影响页面 */ })
    }, 5000)
    return () => clearInterval(t)
  }, [clusterId, kind])

  // 切换命名空间/状态/搜索时重置分页
  useEffect(() => { setPage(1) }, [ns, searchText])

  const paginationConfig = {
    current: page,
    pageSize: 20,
    onChange: setPage,
    showSizeChanger: true,
    pageSizeOptions: ['10', '20', '50', '100'],
    showTotal: (t: number) => `共 ${t} 条`,
    size: 'small' as const,
  }

  // 工作负载状态：已停止（副本 0）/ 更新中（未全部就绪）/ 运行中（全部就绪）
  const workloadStatus = (r: ResourceItem) => {
    if (r.replicas == null) return '' // cronjob 无副本概念
    if (!r.replicas) return '已停止'
    if ((r.ready_replicas || 0) >= r.replicas) return '运行中'
    return '更新中'
  }

  const renderResources = (r: ResourceItem) => {
    const parts: string[] = []
    if (r.cpu_request || r.cpu_limit) parts.push(`CPU ${r.cpu_request ?? '-'}~${r.cpu_limit ?? '-'}`)
    if (r.memory_request || r.memory_limit) parts.push(`内存 ${r.memory_request ?? '-'}~${r.memory_limit ?? '-'}`)
    return parts.length > 0 ? parts.join(' ｜ ') : ''
  }

  // 按 kind 聚合当前命名空间的数据（含名称搜索 + 状态筛选）
  const rows = useMemo(() => {
    const q = searchText.trim().toLowerCase()
    const result: ResourceItem[] = []
    for (const d of details) {
      if (ns !== NS_ALL && d.namespace !== ns) continue
      for (const item of (d[arrKey] as ResourceItem[]) || []) {
        if (q && !(item.name || '').toLowerCase().includes(q) && !d.namespace.toLowerCase().includes(q)) continue
        result.push({ ...item, namespace: d.namespace })
      }
    }
    return result
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [details, ns, searchText, arrKey])

  // 命名空间下拉选项（统一用 context 的命名空间列表）
  const nsOptions = useMemo(() => {
    return [{ value: NS_ALL, label: '全部命名空间' }, ...namespaces.map((n) => ({ value: n, label: n }))]
  }, [namespaces])

  const tgt = (r: ResourceItem): WorkloadTarget => ({ clusterId, kind, namespace: r.namespace, name: r.name })

  const handleDelete = async (r: ResourceItem) => {
    try {
      await deleteWorkload(clusterId, kind, r.namespace, r.name)
      message.success('已删除')
      load()
    } catch {
      message.error('删除失败')
    }
  }

  const confirmDelete = (r: ResourceItem) => {
    Modal.confirm({
      title: `删除 ${kindLabel} ${r.namespace}/${r.name}？`,
      content: '删除后该工作负载的全部 Pod 将被回收，请谨慎操作。',
      okButtonProps: { danger: true },
      okText: '确认删除',
      onOk: () => handleDelete(r),
    })
  }

  const handleRollout = async (r: ResourceItem) => {
    try {
      await rolloutWorkload(clusterId, { kind, namespace: r.namespace, name: r.name, action: 'restart' })
      message.success('已触发滚动重启')
      load()
    } catch { /* 拦截器已提示 */ }
  }

  // 重新创建：走专用 recreate 接口（后端 deployment=先建新后删旧；statefulset/daemonset=滚动重启）
  const handleRecreate = (r: ResourceItem) => {
    Modal.confirm({
      title: `重新创建 ${kindLabel} ${r.namespace}/${r.name}？`,
      content: '将删除并立即用当前配置重建该工作负载，Pod 会重新调度，服务可能短暂中断。',
      okText: '重新创建',
      okButtonProps: { danger: true },
      onOk: async () => {
        try {
          await recreateWorkload(clusterId, { kind, namespace: r.namespace, name: r.name })
          message.success('已重新创建')
          load()
        } catch (e) {
          message.error(`重新创建失败：${(e as Error)?.message || '未知错误'}`)
        }
      },
    })
  }

  // 打开单个伸缩副本弹窗：默认当前副本；停止态回填上次副本
  const openEdit = async (r: ResourceItem) => {
    try {
      const d = await getWorkloadYaml(clusterId, kind, r.namespace, r.name)
      const yaml = (d as { yaml?: string })?.yaml || ''
      setWlEdit({
        clusterId, kind, namespace: r.namespace, name: r.name, create: false,
        initialValues: yaml ? yamlToValues(parseYaml(yaml), kind) : undefined,
        existingYaml: yaml,
      })
    } catch { message.error('读取工作负载配置失败') }
  }

  const openScale = async (r: ResourceItem) => {
    const cur = Number(r.replicas ?? 0)
    let last = 0
    if (cur === 0) {
      try {
        const list = await getWorkloads(clusterId)
        const w = list.find((x) => x.namespace === r.namespace && x.name === r.name && x.kind === kind)
        last = Number((w as unknown as { last_replicas?: number } | undefined)?.last_replicas ?? 0)
      } catch { /* 取不到就按 1 */ }
    }
    setScaleValue(cur > 0 ? cur : (last > 0 ? last : 1))
    setScaleTarget(tgt(r))
  }

  const submitScale = async () => {
    if (scaleValue == null || Number.isNaN(scaleValue)) { message.warning('请填副本数（0 = 停止）'); return }
    if (!scaleTarget) return
    try {
      await scaleWorkload(clusterId, { kind, namespace: scaleTarget.namespace, name: scaleTarget.name, replicas: scaleValue })
      message.success(`已调整为 ${scaleValue} 副本`)
      setScaleTarget(null)
      load()
    } catch { /* 拦截器已提示 */ }
  }

  // ===== 批量操作 =====
  useEffect(() => { setWlSel([]) }, [ns, kind])

  const selItems = useMemo(() => {
    return rows.filter((r) => wlSel.includes(`${r.namespace}-${r.name}`))
      .map((r) => ({ namespace: r.namespace, name: r.name }))
  }, [rows, wlSel])

  const BATCH_LABEL: Record<string, string> = { delete: '批量删除', scale: '批量改副本', restart: '批量重启' }

  const runBatch = async (action: 'delete' | 'scale' | 'restart', reps?: number | null) => {
    if (!selItems.length) { message.warning('请先选择工作负载'); return }
    try {
      const d = await batchWorkloadOp(clusterId, { kind, action, items: selItems, replicas: reps ?? null, confirm: action === 'delete' })
      const bad = d.results.filter((x) => !x.success)
      if (bad.length === 0) message.success(`${BATCH_LABEL[action]}：成功 ${d.ok}/${d.total}`)
      else message.warning(`${BATCH_LABEL[action]}：成功 ${d.ok}/${d.total}，失败 ${bad.length} 个（${bad.slice(0, 3).map((x) => `${x.namespace}/${x.name}: ${x.message}`).join('；')}${bad.length > 3 ? ' …' : ''}）`)
      setWlSel([])
      load()
    } catch { /* 拦截器已提示 */ }
  }

  const confirmBatchDelete = () => {
    if (!selItems.length) { message.warning('请先选择工作负载'); return }
    const names = selItems.slice(0, 8).map((x) => `${x.namespace}/${x.name}`).join('\n')
    Modal.confirm({
      title: `批量删除：${selItems.length} 个${kindLabel}工作负载？`,
      content: (
        <div>
          <div style={{ whiteSpace: 'pre-line', fontFamily: 'monospace', fontSize: 12, maxHeight: 160, overflow: 'auto', marginBottom: 8 }}>{names}{selItems.length > 8 ? `\n… 共 ${selItems.length} 个` : ''}</div>
          <div style={{ color: '#cf1322' }}>删除后全部 Pod 将被回收，不可恢复。</div>
        </div>
      ),
      okText: '确认删除',
      okButtonProps: { danger: true },
      onOk: () => runBatch('delete'),
    })
  }

  const openBatchScale = async () => {
    const first = rows.find((r) => wlSel.includes(`${r.namespace}-${r.name}`))
    const cur = Number(first?.replicas ?? 0)
    let last = 0
    if (cur === 0 && first) {
      try {
        const list = await getWorkloads(clusterId)
        const w = list.find((x) => x.namespace === first.namespace && x.name === first.name && x.kind === kind)
        last = Number((w as unknown as { last_replicas?: number } | undefined)?.last_replicas ?? 0)
      } catch { /* ignore */ }
    }
    setBatchReplicas(cur > 0 ? cur : (last > 0 ? last : 1))
    setBatchScaleOpen(true)
  }

  const submitBatchScale = async () => {
    if (batchReplicas == null || Number.isNaN(batchReplicas)) { message.warning('请填副本数（0 = 停止）'); return }
    await runBatch('scale', batchReplicas)
    setBatchScaleOpen(false)
  }

  const rowSelection = batchable ? {
    selectedRowKeys: wlSel,
    onChange: (keys: Key[]) => setWlSel(keys),
  } : undefined

  const columns = [
    {
      title: '名称', dataIndex: 'name',
      render: (v: string, r: ResourceItem) => (
        <a onClick={() => navigate(`/clusters/${clusterId}/workloads/${kind}/${r.namespace}/${r.name}`)}>{v}</a>
      ),
    },
    {
      title: '状态', key: 'status', dataIndex: 'status', width: 140,
      filters: [
        { text: '运行中', value: '运行中' },
        { text: '更新中', value: '更新中' },
        { text: '已停止', value: '已停止' },
      ],
      onFilter: (value: unknown, r: ResourceItem) => workloadStatus(r) === value,
      render: (_: unknown, r: ResourceItem) => {
        const st = workloadStatus(r)
        return st ? <Tag color={STATUS_COLOR[st]}>{st}</Tag> : '-'
      },
    },
    { title: '项目', dataIndex: 'namespace', width: 180 },
    {
      title: '更新时间', key: 'update_time', dataIndex: 'update_time', width: 180,
      render: (v: unknown) => (v ? String(v).slice(0, 19).replace('T', ' ') : '-'),
    },
    {
      title: '副本', key: 'replicas', width: 100,
      render: (_: unknown, r: ResourceItem) => (r.replicas == null ? '-' : `${r.ready_replicas ?? 0}/${r.replicas}`),
    },
    { title: '资源', render: (_: unknown, r: ResourceItem) => renderResources(r) || '-' },
    {
      title: '操作', key: 'actions', width: 140,
      render: (_: unknown, r: ResourceItem) => (
        <Space size="small">
          {kind === 'deployment' && r.publish_enabled && (
            <Button size="small" type="primary" onClick={() => setReleaseTarget(tgt(r))}>发布</Button>
          )}
          <Dropdown
            trigger={['click']}
            menu={{
              items: [
                // 2026-11-23 R63：列表页只留低风险/唯一的动作；
                // 编辑设置、编辑 YAML、伸缩副本、滚动重启 统一收敛到详情页（原与详情页 5 项全重复）
                { key: 'info', label: '编辑信息' },
                { key: 'recreate', label: '重新创建' },
                ...(kind === 'deployment' ? [{ key: 'history', label: '发布历史' }] : []),
                { type: 'divider' as const },
                { key: 'delete', label: '删除', danger: true },
              ],
              onClick: ({ key: k }) => {
                if (k === 'info') setEditInfoTarget(tgt(r))
                else if (k === 'yaml') setWlYaml(tgt(r))
                else if (k === 'recreate') handleRecreate(r)
                else if (k === 'edit') openEdit(r)
                else if (k === 'scale') openScale(r)
                else if (k === 'rollout') handleRollout(r)
                else if (k === 'history') setReleaseHistoryTarget(tgt(r))
                else if (k === 'delete') confirmDelete(r)
              },
            }}
          >
            <Button size="small" type="link"><MoreOutlined /></Button>
          </Dropdown>
        </Space>
      ),
    },
  ]

  const batchBar = batchable && wlSel.length > 0 ? (
    <Space size={8} wrap style={{ marginBottom: 12 }}>
      <span>已选 <b>{wlSel.length}</b> 个{kindLabel}</span>
      {scaleable && <Button size="small" onClick={openBatchScale}>批量改副本</Button>}
      <Button size="small" onClick={() => runBatch('restart')}>批量重启</Button>
      <Button size="small" danger onClick={confirmBatchDelete}>批量删除</Button>
      <Button size="small" type="link" onClick={() => setWlSel([])}>清除选择</Button>
      <span style={{ fontSize: 12, color: '#999' }}>（改副本填 0 = 停止）</span>
    </Space>
  ) : null

  const KINDS = [
    { label: '无状态', value: 'deployment' },
    { label: '有状态', value: 'statefulset' },
    { label: '守护进程', value: 'daemonset' },
    { label: '定时任务', value: 'cronjob' },
  ]

  return (
    <div>
      <Card size="small">
        <Tabs
          activeKey={kind}
          onChange={(k) => navigate(`/clusters/${clusterId}/workloads/${k}`)}
          items={KINDS.map((k) => ({ key: k.value, label: k.label }))}
          style={{ marginBottom: 8 }}
        />
        <Space size={8} wrap style={{ marginBottom: 12 }}>
          <span style={{ color: '#8aa196', fontSize: 12 }}>命名空间</span>
          <Select
            size="small"
            style={{ width: 200 }}
            value={ns}
            onChange={(v) => setNs(v as string)}
            showSearch
            optionFilterProp="label"
            loading={nsLoading}
            options={nsOptions}
          />
          <Input
            size="small"
            allowClear
            placeholder="搜索名称"
            prefix={<SearchOutlined />}
            style={{ width: 180 }}
            value={searchText}
            onChange={(e) => setSearchText(e.target.value)}
          />
          <Button type="primary" size="small"
            onClick={() => setWlCreate({ clusterId, kind, namespace: ns === NS_ALL ? 'default' : ns })}>
            创建{kindLabel}
          </Button>
        </Space>

        {batchBar}

        <Table
          rowKey={(r) => `${r.namespace}-${r.name}`}
          size="small"
          loading={loading}
          dataSource={rows}
          pagination={paginationConfig}
          rowSelection={rowSelection}
          columns={columns}
          locale={{ emptyText: <Empty description={`无 ${kindLabel} 工作负载`} image={Empty.PRESENTED_IMAGE_SIMPLE} /> }}
        />
      </Card>

      <WorkloadCreateModal target={wlCreate} onClose={() => setWlCreate(null)} onSaved={load} />
      <WorkloadCreateModal target={wlEdit} onClose={() => setWlEdit(null)} onSaved={load} />
      <WorkloadYamlModal target={wlYaml} onClose={() => setWlYaml(null)} />
      <EditInfoModal target={editInfoTarget} kindLabel={kindLabel} onClose={() => setEditInfoTarget(null)} onSaved={load} />
      {releaseTarget && (
        <ReleaseModal open clusterId={releaseTarget.clusterId} namespace={releaseTarget.namespace}
          serviceName={releaseTarget.name} onClose={() => setReleaseTarget(null)} />
      )}
      {releaseHistoryTarget && (
        <ReleaseHistoryModal open clusterId={releaseHistoryTarget.clusterId} namespace={releaseHistoryTarget.namespace}
          serviceName={releaseHistoryTarget.name} onClose={() => setReleaseHistoryTarget(null)} />
      )}

      {/* 单个伸缩副本 */}
      <Modal open={!!scaleTarget} width={440} title={scaleTarget ? `伸缩副本：${scaleTarget.namespace}/${scaleTarget.name}` : ''}
        onCancel={() => setScaleTarget(null)} onOk={submitScale} okText="应用" destroyOnClose>
        <div style={{ marginBottom: 8 }}>目标副本数（0 = 停止，N = 启动/扩缩容）：</div>
        <InputNumber min={0} max={9999} style={{ width: 160 }} value={scaleValue}
          onChange={(v) => setScaleValue(Number(v ?? 0))} />
        <div style={{ marginTop: 10, fontSize: 12, color: '#888' }}>
          改为 0 时平台会记住当前副本数（30 天），下次打开自动回填。
        </div>
      </Modal>

      {/* 批量改副本 */}
      <Modal open={batchScaleOpen} width={440} title={`批量改副本（${selItems.length} 个${kindLabel}）`}
        onCancel={() => setBatchScaleOpen(false)} onOk={submitBatchScale} okText="应用" destroyOnClose>
        <div style={{ marginBottom: 8 }}>目标副本数（0 = 停止，N = 启动/扩缩容）：</div>
        <InputNumber min={0} max={9999} style={{ width: 160 }} value={batchReplicas}
          onChange={(v) => setBatchReplicas(Number(v ?? 0))} />
        <div style={{ marginTop: 10, fontSize: 12, color: '#888' }}>
          所有选中的工作负载都会改成同一个副本数。改为 0 时平台会记住当前副本数（30 天），列表里「已停止」的行会显示上次副本，下次打开这里会自动回填。
        </div>
      </Modal>
    </div>
  )
}
