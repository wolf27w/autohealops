// 集群详情页：命名空间下拉 + 工作负载类型 Tab 左右切换（无状态/有状态/守护集/定时任务/容器组）
// 注：按老板要求**去掉「搜索（服务名）」输入框**，只保留命名空间下拉
import { useEffect, useMemo, useState, type ReactNode } from 'react'
import type { PodTarget, MetricPoint } from '../types'
import { Card, Table, Tag, Button, Space, Select, Tabs, Empty, Drawer, Segmented, Modal, Input, message, Dropdown, Progress } from 'antd'
import { ArrowLeftOutlined, FilterOutlined, MoreOutlined } from '@ant-design/icons'
import { useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { Line } from '@ant-design/charts'
import { getClusterDetail, getClusters, triggerCollect, getPodMetrics, getPodLogs, getPodYaml, applyPodYaml, execPod, inspectCluster, getInspectionReports, downloadInspectionReport, getClusterInspectProgress, getClusterPods, getMiscResources, deleteObject, deleteWorkload, runProactiveScan } from '../services/api'
import type { ClusterPod } from '../services/api'
import type { ProactiveScanResult } from '../services/api'
import PodEventsModal from '../components/PodEventsModal'
import WorkloadYamlModal, { type WorkloadTarget } from '../components/WorkloadYamlModal'
import WorkloadMountsPanel from '../components/WorkloadMountsPanel'
import PodDetailDrawer from '../components/PodDetailDrawer'
import ConfigObjectYamlModal, { type ConfigObjectTarget } from '../components/ConfigObjectYamlModal'
import ConfigObjectInfoModal from '../components/ConfigObjectInfoModal'
import ConfigObjectDataModal from '../components/ConfigObjectDataModal'
import ServiceSettingsModal from '../components/ServiceSettingsModal'
import IngressSettingsModal from '../components/IngressSettingsModal'
import PvcCreateModal from '../components/PvcCreateModal'
import WorkloadCreateModal, { type WorkloadCreateTarget } from '../components/WorkloadCreateModal'
import PodLogStreamModal from '../components/PodLogStreamModal'
import { podStatusColor, podReadyText } from '../utils/podStatus'

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
  [key: string]: unknown
}

interface NamespaceDetail {
  namespace: string
  deployments: ResourceItem[]
  statefulsets: ResourceItem[]
  cronjobs: ResourceItem[]
  daemonsets: ResourceItem[]
  pods: ResourceItem[]
}

interface MiscRow {
  namespace: string
  name: string
  [key: string]: unknown
}

interface MiscData {
  services: MiscRow[]
  ingresses: MiscRow[]
  configmaps: MiscRow[]
  secrets: MiscRow[]
  pvcs: MiscRow[]
}

export default function ClusterDetail() {
  const { id } = useParams()
  const navigate = useNavigate()
  const [namespaces, setNamespaces] = useState<NamespaceDetail[]>([])
  const [clusterName, setClusterName] = useState('')
  const [searchParams, setSearchParams] = useSearchParams()
  // 命名空间选择持久化：优先 URL ?ns=（支持返回/刷新/深链），其次 localStorage（记住上次选择）
  const [activeNs, setActiveNs] = useState<string>(() => {
    const fromUrl = searchParams.get('ns')
    if (fromUrl) return fromUrl
    try {
      return localStorage.getItem(`cluster_ns:${id}`) || '_all_'
    } catch {
      return '_all_'
    }
  })
  const VALID_TYPES = ['deployments', 'statefulsets', 'daemonsets', 'cronjobs', 'services', 'ingresses', 'configmaps', 'secrets', 'pvcs']
  const [activeType, setActiveType] = useState<string>(() => {
    const fromUrl = searchParams.get('type')
    if (fromUrl && VALID_TYPES.includes(fromUrl)) return fromUrl
    // N4：默认落在「工作负载」（不再记忆上次类型）
    return 'deployments'
  })
  const [loading, setLoading] = useState(false)
  const [metricsPod, setMetricsPod] = useState<PodTarget | null>(null)
  const [logPod, setLogPod] = useState<PodTarget | null>(null)
  const [execTarget, setExecTarget] = useState<PodTarget | null>(null)
  const [yamlPod, setYamlPod] = useState<PodTarget | null>(null)
  const [eventsPod, setEventsPod] = useState<PodTarget | null>(null)
  const [detailPod, setDetailPod] = useState<PodTarget | null>(null)
  const [wlYaml, setWlYaml] = useState<WorkloadTarget | null>(null)
  const [mountTarget, setMountTarget] = useState<WorkloadTarget | null>(null)
  const [mountTypes, setMountTypes] = useState<Array<'configMap' | 'secret' | 'pvc'>>(['configMap'])
  const [misc, setMisc] = useState<MiscData>({ services: [], ingresses: [], configmaps: [], secrets: [], pvcs: [] })
  const [configObj, setConfigObj] = useState<ConfigObjectTarget | null>(null)
  const [infoObj, setInfoObj] = useState<ConfigObjectTarget | null>(null)
  const [dataObj, setDataObj] = useState<ConfigObjectTarget | null>(null)
  const [svcObj, setSvcObj] = useState<ConfigObjectTarget | null>(null)
  const [ingObj, setIngObj] = useState<ConfigObjectTarget | null>(null)
  const [pvcObj, setPvcObj] = useState<ConfigObjectTarget | null>(null)
  const [wlCreate, setWlCreate] = useState<WorkloadCreateTarget | null>(null)
  const [livePods, setLivePods] = useState<Record<string, ClusterPod>>({})
  // 实时 Pod 列表（全 phase：Running/Pending/Failed…，滚动更新增删即时反映）
  const [podList, setPodList] = useState<ClusterPod[]>([])
  const [statusFilter, setStatusFilter] = useState<string | undefined>(undefined)
  const [page, setPage] = useState(1)
  const [inspectOpen, setInspectOpen] = useState(false)
  const [appInspectOpen, setAppInspectOpen] = useState(false)
  const [aiNs, setAiNs] = useState<string>('')
  const [aiKinds, setAiKinds] = useState<string[]>([])
  const [aiLoading, setAiLoading] = useState(false)
  const [aiResult, setAiResult] = useState<ProactiveScanResult | null>(null)
  const runAppInspect = async () => {
    setAiLoading(true); setAiResult(null)
    try {
      const r = await runProactiveScan({ cluster_id: Number(id), namespace: aiNs || undefined, kinds: aiKinds })
      if (r?.skipped) message.info(`巡检跳过：${r.reason || '已有巡检进行中'}`)
      setAiResult(r)
    } catch { /* 拦截器已提示 */ } finally { setAiLoading(false) }
  }

  // 工作负载类型 Tab：仅 URL ?type= 同步（不再本地记忆，N4 保证默认落到工作负载）
  const changeType = (v: string) => {
    setActiveType(v)
    const next = new URLSearchParams(searchParams)
    if (v && v !== 'deployments') next.set('type', v)
    else next.delete('type')
    setSearchParams(next, { replace: true })
  }

  // 切换命名空间/类型/状态筛选时重置分页到第 1 页
  useEffect(() => { setPage(1) }, [activeNs, activeType, statusFilter])

  const paginationConfig = {
    current: page,
    pageSize: 20,
    onChange: setPage,
    showSizeChanger: true,
    pageSizeOptions: ['10', '20', '50', '100'],
    showTotal: (t: number) => `共 ${t} 条`,
    size: 'small' as const,
  }

  const load = () => {
    setLoading(true)
    getClusterDetail(Number(id)).then((d) => setNamespaces(d as NamespaceDetail[])).finally(() => setLoading(false))
    getClusters().then((clusters) => {
      const c = clusters.find((x) => x.id === Number(id))
      if (c) setClusterName(c.name)
    })
  }

  useEffect(load, [id])

  // 实时 Pod 信息（创建时间/重启/节点）：拉 k8s 实时列表，按 命名空间/名称 合并到容器组表
  // （DB 快照无创建时间；namespace 空=全部）
  const loadLivePods = () => {
    if (activeType !== 'pods') return
    getClusterPods(Number(id), activeNs === '_all_' ? '' : activeNs)
      .then((d) => {
        const pods = (d as { pods?: ClusterPod[] }).pods || []
        const m: Record<string, ClusterPod> = {}
        for (const p of pods) m[`${p.namespace}-${p.name}`] = p
        setLivePods(m)
        setPodList(pods)
      })
      .catch(() => { setLivePods({}); setPodList([]) })
  }
  useEffect(loadLivePods, [id, activeType, activeNs]) // eslint-disable-line react-hooks/exhaustive-deps
  // 容器组 Tab 实时刷新（5s）：创建过程/滚动更新增删即时可见
  useEffect(() => {
    if (activeType !== 'pods') return
    const t = setInterval(loadLivePods, 5000)
    return () => clearInterval(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, activeType, activeNs])

  // 其它资源（服务/路由/ConfigMap/Secret/存储）：切到对应 Tab 时按命名空间实时拉取
  const MISC_TYPES = ['services', 'ingresses', 'configmaps', 'secrets', 'pvcs']
  useEffect(() => {
    if (!MISC_TYPES.includes(activeType)) return
    getMiscResources(Number(id), activeNs === '_all_' ? '' : activeNs)
      .then((d) => setMisc(d as unknown as MiscData))
      .catch(() => setMisc({ services: [], ingresses: [], configmaps: [], secrets: [], pvcs: [] }))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, activeType, activeNs])

  const reloadMisc = () => {
    getMiscResources(Number(id), activeNs === '_all_' ? '' : activeNs)
      .then((d) => setMisc(d as unknown as MiscData))
      .catch(() => { /* ignore */ })
  }

  const handleDeleteObject = async (kind: string, ns: string, name: string) => {
    try {
      await deleteObject(Number(id), kind, ns, name)
      message.success('已删除')
      reloadMisc()
    } catch {
      message.error('删除失败')
    }
  }

  const handleDeleteWorkload = async (kind: string, ns: string, name: string) => {
    try {
      await deleteWorkload(Number(id), kind, ns, name)
      message.success('已删除')
      load()
    } catch {
      message.error('删除失败')
    }
  }

  // URL 没有 ns 但本地记住了 → 同步进 URL，保证「返回」也停留在原命名空间
  useEffect(() => {
    if (searchParams.get('ns')) return
    try {
      const saved = localStorage.getItem(`cluster_ns:${id}`)
      if (saved && saved !== '_all_') {
        const next = new URLSearchParams(searchParams)
        next.set('ns', saved)
        setSearchParams(next, { replace: true })
      }
    } catch { /* ignore */ }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id])

  // 根据选中的 namespace 过滤
  const filteredNs = useMemo(() => {
    if (activeNs === '_all_') return namespaces
    return namespaces.filter((n) => n.namespace === activeNs)
  }, [activeNs, namespaces])

  // 按类型聚合当前选中 namespace 的数据
  const dataByType = useMemo(() => {
    const result: Record<string, ResourceItem[]> = {
      deployments: [], statefulsets: [], cronjobs: [], daemonsets: [], pods: [],
    }
    for (const ns of filteredNs) {
      for (const type of ['deployments', 'statefulsets', 'cronjobs', 'daemonsets', 'pods'] as const) {
        for (const item of ns[type] || []) {
          result[type].push({ ...item, namespace: ns.namespace })
        }
      }
    }
    return result
  }, [filteredNs])

  // 前端展示类型 → 后端资源 kind（用于跳转服务详情）
  const tabKind = (r: ResourceItem) => {
    if (activeType === 'deployments') return 'deployment'
    if (activeType === 'statefulsets') return 'statefulset'
    if (activeType === 'cronjobs') return 'cronjob'
    if (activeType === 'daemonsets') return 'daemonset'
    return 'deployment'
  }

  const renderResources = (pod: ResourceItem) => {
    const parts: string[] = []
    if (pod.cpu_request || pod.cpu_limit) parts.push(`CPU ${pod.cpu_request || '-'}~${pod.cpu_limit || '-'}`)
    if (pod.memory_request || pod.memory_limit) parts.push(`内存 ${pod.memory_request || '-'}~${pod.memory_limit || '-'}`)
    return parts.length > 0 ? parts.join(' ｜ ') : ''
  }

  // 工作负载状态：已停止（副本 0）/ 更新中（未全部就绪）/ 运行中（全部就绪）
  const workloadStatus = (r: ResourceItem) => {
    if (!r.replicas) return '已停止'
    if ((r.ready_replicas || 0) >= r.replicas) return '运行中'
    return '更新中'
  }
  const STATUS_COLOR: Record<string, string> = { 运行中: 'green', 更新中: 'orange', 已停止: 'default' }

  // 状态筛选由「状态」列表头的前置筛选图标负责（不在工具栏重复）
  const finalData = useMemo(() => {
    if (!statusFilter) return dataByType
    const f = (arr: ResourceItem[]) => arr.filter((x) => workloadStatus(x) === statusFilter)
    return {
      ...dataByType,
      deployments: f(dataByType.deployments),
      statefulsets: f(dataByType.statefulsets),
      daemonsets: f(dataByType.daemonsets),
    }
  }, [dataByType, statusFilter])

  // 容器组行：以实时 Pod 列表为准（全 phase），补充快照里的资源 request/limit
  const podsRows = useMemo(
    () => {
      const snap: Record<string, Partial<ResourceItem>> = {}
      for (const p of (dataByType.pods || [])) snap[`${p.namespace}-${p.name}`] = p
      return podList.map((p) => {
        const s = snap[`${p.namespace}-${p.name}`] || {}
        return { ...p, status: p.status || p.phase || (p.ready ? 'Running' : 'Pending'),
          cpu_request: s.cpu_request, memory_request: s.memory_request,
          cpu_limit: s.cpu_limit, memory_limit: s.memory_limit }
      })
    },
    [podList, dataByType.pods],
  )

  // 「状态」列表头：筛选图标放在文字前面（避免看着像挂在“更新时间”上）
  const statusHeader = (
    <Space size={4}>
      <Dropdown
        trigger={['click']}
        menu={{
          selectable: true,
          selectedKeys: statusFilter ? [statusFilter] : [],
          items: [
            { key: '__all__', label: '全部状态' },
            { key: '运行中', label: '运行中' },
            { key: '更新中', label: '更新中' },
            { key: '已停止', label: '已停止' },
          ],
          onClick: ({ key }) => setStatusFilter(key === '__all__' ? undefined : key),
        }}
      >
        <FilterOutlined style={{ cursor: 'pointer', color: statusFilter ? '#1677ff' : undefined }} />
      </Dropdown>
      <span>状态</span>
    </Space>
  )

  const replicasColumns = [
    { title: '命名空间', dataIndex: 'namespace' },
    { title: '名称', dataIndex: 'name', render: (v: string, r: ResourceItem) => (
      <a onClick={() => navigate(`/clusters/${id}/workloads/${tabKind(r)}/${r.namespace}/${r.name}`)}>{v}</a>
    ) },
    {
      title: statusHeader, key: 'status', dataIndex: 'status', width: 200,
      render: (_: unknown, r: ResourceItem) => {
        const st = workloadStatus(r)
        return <Tag color={STATUS_COLOR[st]}>{st}（{r.ready_replicas || 0}/{r.replicas}）</Tag>
      },
    },
    { title: '更新时间', key: 'update_time', dataIndex: 'update_time', width: 180,
      render: (v: unknown) => (v ? String(v).slice(0, 19).replace('T', ' ') : '-') },
    { title: '操作', key: 'actions', width: 70, render: (_: unknown, r: ResourceItem) => (
      <Dropdown trigger={['click']} menu={{ items: [
        { key: 'cm', label: '配置字典' },
        { key: 'secret', label: '配置密钥' },
        { key: 'pvc', label: '配置 PVC' },
        { type: 'divider' as const },
        { key: 'yaml', label: '编辑 YAML' },
        { type: 'divider' as const },
        { key: 'delete', label: '删除', danger: true },
      ], onClick: ({ key }) => {
        const tgt = { clusterId: Number(id), kind: tabKind(r), namespace: r.namespace, name: r.name }
        if (key === 'cm') { setMountTypes(['configMap']); setMountTarget(tgt) }
        else if (key === 'secret') { setMountTypes(['secret']); setMountTarget(tgt) }
        else if (key === 'pvc') { setMountTypes(['pvc']); setMountTarget(tgt) }
        else if (key === 'yaml') setWlYaml(tgt)
        else if (key === 'delete') Modal.confirm({
          title: `删除 ${tabKind(r)} ${r.namespace}/${r.name}？`,
          content: '删除后该工作负载的全部 Pod 将被回收，请谨慎操作。',
          okButtonProps: { danger: true },
          onOk: () => handleDeleteWorkload(tabKind(r), r.namespace, r.name),
        })
      } }}>
        <Button size="small" type="link"><MoreOutlined /></Button>
      </Dropdown>
    ) },
  ]

  const typeTabItems = [
    {
      key: 'deployments',
      label: '无状态',
      children: (
        <Table rowKey={(r) => `${r.namespace}-${r.name}`} size="small" dataSource={finalData.deployments}
          pagination={paginationConfig}
          columns={replicasColumns} locale={{ emptyText: '无 Deployment' }} />
      ),
    },
    {
      key: 'statefulsets',
      label: '有状态',
      children: (
        <Table rowKey={(r) => `${r.namespace}-${r.name}`} size="small" dataSource={finalData.statefulsets}
          pagination={paginationConfig}
          columns={replicasColumns} locale={{ emptyText: '无 StatefulSet' }} />
      ),
    },
    {
      key: 'daemonsets',
      label: '守护集',
      children: (
        <Table rowKey={(r) => `${r.namespace}-${r.name}`} size="small" dataSource={finalData.daemonsets}
          pagination={paginationConfig}
          columns={replicasColumns} locale={{ emptyText: '无 DaemonSet' }} />
      ),
    },
    {
      key: 'pods-group',
      label: '容器组',
      children: (
        <Table rowKey={(r) => `${r.namespace}-${r.name}`} size="small" dataSource={podsRows}
          pagination={paginationConfig}
          columns={[
            { title: '命名空间', dataIndex: 'namespace', width: 180 },
            { title: '名称', dataIndex: 'name', render: (v: string, r: ResourceItem) => (
              <a onClick={() => setDetailPod({ clusterId: Number(id), namespace: r.namespace, name: r.name })}>{v}</a>
            ) },
            { title: '状态', dataIndex: 'status', width: 150, render: (s: string) => <Tag color={podStatusColor(s)}>{s}</Tag> },
            { title: '就绪', key: 'ready', width: 70, render: (_: unknown, r: ResourceItem) => podReadyText(r.ready_containers, r.total_containers) },
            { title: '重启', dataIndex: 'restart_count', width: 70, render: (v: number) => (v ?? '-') },
            { title: '节点', dataIndex: 'node_name', width: 160, render: (v: string) => v || '-' },
            { title: '创建时间', dataIndex: 'created_at', width: 165, render: (v: unknown) => (v ? String(v).slice(0, 19).replace('T', ' ') : '-') },
            { title: '资源（最小~最大）', render: (_: unknown, r: ResourceItem) => renderResources(r) || '-' },
            { title: '操作', width: 70, render: (_: unknown, r: ResourceItem) => (
              <Dropdown trigger={['click']} menu={{ items: [
                { key: 'metrics', label: '监控' },
                { key: 'logs', label: '日志' },
                { key: 'exec', label: '进容器' },
                { key: 'events', label: '事件' },
                { key: 'yaml', label: '编辑 YAML' },
                { type: 'divider' as const },
                { key: 'delete', label: '删除', danger: true },
              ], onClick: ({ key }) => {
                if (key === 'metrics') setMetricsPod({ ...r, clusterId: Number(id) })
                else if (key === 'logs') setLogPod({ ...r, clusterId: Number(id) })
                else if (key === 'exec') setExecTarget({ ...r, clusterId: Number(id) })
                else if (key === 'events') setEventsPod({ ...r, clusterId: Number(id) })
                else if (key === 'yaml') setYamlPod({ ...r, clusterId: Number(id) })
                else if (key === 'delete') Modal.confirm({
                  title: `删除容器组 ${r.namespace}/${r.name}？`,
                  content: 'Pod 被控制器托管时会自动重建；裸 Pod 删除后不再恢复。',
                  okButtonProps: { danger: true },
                  onOk: () => handleDeleteWorkload('pod', r.namespace, r.name),
                })
              } }}>
                <Button size="small" type="link"><MoreOutlined /></Button>
              </Dropdown>
            ) },
          ]}
          locale={{ emptyText: '无 Pod' }} />
      ),
    },
    {
      key: 'cronjobs',
      label: '定时任务',
      children: (
        <Table rowKey={(r) => `${r.namespace}-${r.name}`} size="small" dataSource={dataByType.cronjobs}
          pagination={paginationConfig}
          columns={[
            { title: '命名空间', dataIndex: 'namespace' },
            { title: '名称', dataIndex: 'name', render: (v: string, r: ResourceItem) => (
              <a onClick={() => navigate(`/clusters/${id}/workloads/cronjob/${r.namespace}/${r.name}`)}>{v}</a>
            ) },
            { title: '更新时间', dataIndex: 'update_time', width: 180,
              render: (v: unknown) => (v ? String(v).slice(0, 19).replace('T', ' ') : '-') },
            { title: '操作', width: 70, render: (_: unknown, r: ResourceItem) => (
              <Dropdown trigger={['click']} menu={{ items: [
                { key: 'yaml', label: '编辑 YAML' },
                { type: 'divider' as const },
                { key: 'delete', label: '删除', danger: true },
              ], onClick: ({ key }) => {
                if (key === 'yaml') setWlYaml({ clusterId: Number(id), kind: 'cronjob', namespace: r.namespace, name: r.name })
                else if (key === 'delete') Modal.confirm({
                  title: `删除 cronjob ${r.namespace}/${r.name}？`,
                  okButtonProps: { danger: true },
                  onOk: () => handleDeleteWorkload('cronjob', r.namespace, r.name),
                })
              } }}>
                <Button size="small" type="link"><MoreOutlined /></Button>
              </Dropdown>
            ) },
          ]}
          locale={{ emptyText: '无 CronJob' }} />
      ),
    },
    {
      key: 'services',
      label: '服务',
      children: (
        <Table rowKey={(r) => `${r.namespace}-${r.name}`} size="small" dataSource={misc.services}
          pagination={paginationConfig}
          columns={[
            { title: '命名空间', dataIndex: 'namespace', width: 180 },
            { title: '名称', dataIndex: 'name' },
            { title: '类型', dataIndex: 'type', width: 130, render: (v: string) => <Tag color={v === 'LoadBalancer' ? 'blue' : v === 'NodePort' ? 'green' : 'default'}>{v}</Tag> },
            { title: 'ClusterIP', dataIndex: 'cluster_ip', width: 150 },
            { title: '端口', dataIndex: 'ports', render: (v: string) => v || '-' },
            { title: '外部 IP', dataIndex: 'external_ip', width: 150, render: (v: string) => v || '-' },
            { title: '操作', width: 80, render: (_: unknown, r: MiscRow) => (
              <Dropdown trigger={['click']} menu={{ items: [
                { key: 'info', label: '编辑信息' },
                { key: 'settings', label: '编辑设置' },
                { key: 'yaml', label: '编辑 YAML' },
                { type: 'divider' as const },
                { key: 'delete', label: '删除', danger: true },
              ], onClick: ({ key }) => {
                const t = { clusterId: Number(id), kind: 'service', namespace: r.namespace, name: r.name }
                if (key === 'info') setInfoObj(t)
                else if (key === 'settings') setSvcObj(t)
                else if (key === 'yaml') setConfigObj(t)
                else if (key === 'delete') Modal.confirm({ title: `删除服务 ${r.name}？`, okButtonProps: { danger: true }, onOk: () => handleDeleteObject('service', r.namespace, r.name) })
              } }}>
                <Button size="small" type="link"><MoreOutlined /></Button>
              </Dropdown>
            ) },
          ]}
          locale={{ emptyText: '无 Service' }} />
      ),
    },
    {
      key: 'ingresses',
      label: '路由',
      children: (
        <Table rowKey={(r) => `${r.namespace}-${r.name}`} size="small" dataSource={misc.ingresses}
          pagination={paginationConfig}
          columns={[
            { title: '命名空间', dataIndex: 'namespace', width: 180 },
            { title: '名称', dataIndex: 'name' },
            { title: 'Hosts', dataIndex: 'hosts', render: (v: string) => v || '-' },
            { title: 'Class', dataIndex: 'class', width: 130, render: (v: string) => v || '-' },
            { title: '地址', dataIndex: 'address', width: 150, render: (v: string) => v || '-' },
            { title: '操作', width: 80, render: (_: unknown, r: MiscRow) => (
              <Dropdown trigger={['click']} menu={{ items: [
                { key: 'info', label: '编辑信息' },
                { key: 'settings', label: '编辑设置' },
                { key: 'yaml', label: '编辑 YAML' },
                { type: 'divider' as const },
                { key: 'delete', label: '删除', danger: true },
              ], onClick: ({ key }) => {
                const t = { clusterId: Number(id), kind: 'ingress', namespace: r.namespace, name: r.name }
                if (key === 'info') setInfoObj(t)
                else if (key === 'settings') setIngObj(t)
                else if (key === 'yaml') setConfigObj(t)
                else if (key === 'delete') Modal.confirm({ title: `删除路由 ${r.name}？`, okButtonProps: { danger: true }, onOk: () => handleDeleteObject('ingress', r.namespace, r.name) })
              } }}>
                <Button size="small" type="link"><MoreOutlined /></Button>
              </Dropdown>
            ) },
          ]}
          locale={{ emptyText: '无 Ingress' }} />
      ),
    },
    {
      key: 'configmaps',
      label: '配置字典',
      children: (
        <Table rowKey={(r) => `${r.namespace}-${r.name}`} size="small" dataSource={misc.configmaps}
          pagination={paginationConfig}
          columns={[
            { title: '命名空间', dataIndex: 'namespace', width: 220 },
            { title: '名称', dataIndex: 'name' },
            { title: '数据项数', dataIndex: 'data_count', width: 120 },
            { title: '操作', width: 80, render: (_: unknown, r: MiscRow) => (
              <Dropdown trigger={['click']} menu={{ items: [
                { key: 'info', label: '编辑信息' },
                { key: 'settings', label: '编辑设置' },
                { key: 'yaml', label: '编辑 YAML' },
                { type: 'divider' as const },
                { key: 'delete', label: '删除', danger: true },
              ], onClick: ({ key }) => {
                const t = { clusterId: Number(id), kind: 'configmap', namespace: r.namespace, name: r.name }
                if (key === 'info') setInfoObj(t)
                else if (key === 'settings') setDataObj(t)
                else if (key === 'yaml') setConfigObj(t)
                else if (key === 'delete') Modal.confirm({ title: `删除配置字典 ${r.name}？`, okButtonProps: { danger: true }, onOk: () => handleDeleteObject('configmap', r.namespace, r.name) })
              } }}>
                <Button size="small" type="link"><MoreOutlined /></Button>
              </Dropdown>
            ) },
          ]}
          locale={{ emptyText: '无配置字典' }} />
      ),
    },
    {
      key: 'secrets',
      label: '保密字典',
      children: (
        <Table rowKey={(r) => `${r.namespace}-${r.name}`} size="small" dataSource={misc.secrets}
          pagination={paginationConfig}
          columns={[
            { title: '命名空间', dataIndex: 'namespace', width: 220 },
            { title: '名称', dataIndex: 'name' },
            { title: '类型', dataIndex: 'type', width: 200, render: (v: string) => v || '-' },
            { title: '数据项数', dataIndex: 'data_count', width: 120 },
            { title: '操作', width: 80, render: (_: unknown, r: MiscRow) => (
              <Dropdown trigger={['click']} menu={{ items: [
                { key: 'info', label: '编辑信息' },
                { key: 'settings', label: '编辑设置' },
                { key: 'yaml', label: '编辑 YAML' },
                { type: 'divider' as const },
                { key: 'delete', label: '删除', danger: true },
              ], onClick: ({ key }) => {
                const t = { clusterId: Number(id), kind: 'secret', namespace: r.namespace, name: r.name }
                if (key === 'info') setInfoObj(t)
                else if (key === 'settings') setDataObj(t)
                else if (key === 'yaml') setConfigObj(t)
                else if (key === 'delete') Modal.confirm({ title: `删除保密字典 ${r.name}？`, okButtonProps: { danger: true }, onOk: () => handleDeleteObject('secret', r.namespace, r.name) })
              } }}>
                <Button size="small" type="link"><MoreOutlined /></Button>
              </Dropdown>
            ) },
          ]}
          locale={{ emptyText: '无保密字典' }} />
      ),
    },
    {
      key: 'pvcs',
      label: '存储',
      children: (
        <Table rowKey={(r) => `${r.namespace}-${r.name}`} size="small" dataSource={misc.pvcs}
          pagination={paginationConfig}
          columns={[
            { title: '命名空间', dataIndex: 'namespace', width: 180 },
            { title: '名称', dataIndex: 'name' },
            { title: '状态', dataIndex: 'status', width: 100, render: (v: string) => <Tag color={v === 'Bound' ? 'green' : v === 'Pending' ? 'orange' : 'red'}>{v}</Tag> },
            { title: '容量', dataIndex: 'capacity', width: 100 },
            { title: 'StorageClass', dataIndex: 'storage_class', width: 160, render: (v: string) => v || '-' },
            { title: '绑定 PV', dataIndex: 'volume_name', ellipsis: true, render: (v: string) => v || '-' },
            { title: '访问模式', dataIndex: 'access_modes', width: 130, render: (v: string) => v || '-' },
            { title: '操作', width: 80, render: (_: unknown, r: MiscRow) => (
              <Dropdown trigger={['click']} menu={{ items: [
                { key: 'info', label: '编辑信息' },
                { key: 'yaml', label: '编辑 YAML' },
                { type: 'divider' as const },
                { key: 'delete', label: '删除', danger: true },
              ], onClick: ({ key }) => {
                const t = { clusterId: Number(id), kind: 'pvc', namespace: r.namespace, name: r.name }
                if (key === 'info') setInfoObj(t)
                else if (key === 'yaml') setConfigObj(t)
                else if (key === 'delete') Modal.confirm({ title: `删除存储 ${r.name}？（会释放存储，数据可能丢失）`, okButtonProps: { danger: true }, onOk: () => handleDeleteObject('pvc', r.namespace, r.name) })
              } }}>
                <Button size="small" type="link"><MoreOutlined /></Button>
              </Dropdown>
            ) },
          ]}
          locale={{ emptyText: '无 PVC' }} />
      ),
    },
  ]

  // 表格按类型索引（用于分组标签页内切换）
  const tableByType: Record<string, ReactNode> = {}
  for (const t of typeTabItems) tableByType[String(t.key)] = t.children
  const keyOfType = (t: string) => (t === 'pods' ? 'pods-group' : t)

  // 标签页分组：10 个类型 → 6 个标签（工作负载/容器组/定时任务/网络/配置/存储），降低密集度
  const GROUPS = [
    { key: 'workloads', label: '工作负载', types: ['deployments', 'statefulsets', 'daemonsets', 'pods'], names: { deployments: '无状态', statefulsets: '有状态', daemonsets: '守护集', pods: '容器组' } as Record<string, string> },
    { key: 'cronjobs', label: '定时任务', types: ['cronjobs'] },
    { key: 'network', label: '网络', types: ['services', 'ingresses'], names: { services: '服务', ingresses: '路由' } as Record<string, string> },
    { key: 'config', label: '配置', types: ['configmaps', 'secrets'], names: { configmaps: '配置字典', secrets: '保密字典' } as Record<string, string> },
    { key: 'storage', label: '存储', types: ['pvcs'] },
  ]
  const groupOfType = (t: string) => GROUPS.find((g) => g.types.includes(t))?.key || 'workloads'
  const tabItems = GROUPS.map((g) => ({
    key: g.key,
    label: g.label,
    children: (
      <div>
        {g.types.length > 1 && (
          <Segmented
            size="small"
            value={activeType}
            onChange={(v) => changeType(String(v))}
            options={g.types.map((t) => ({ value: t, label: g.names?.[t] ?? t }))}
            style={{ marginBottom: 14 }}
          />
        )}
        {tableByType[keyOfType(g.types.includes(activeType) ? activeType : g.types[0])]}
      </div>
    ),
  }))

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12 }}>
        <Button type="text" size="small" icon={<ArrowLeftOutlined />} onClick={() => navigate('/clusters')}>返回</Button>
        <span style={{ fontSize: 16, fontWeight: 700 }}>{clusterName || `集群 ${id}`}</span>
        <span style={{ flex: 1 }} />
        <Button size="small" onClick={() => triggerCollect(Number(id)).then(() => load())}>刷新采集</Button>
        <Button size="small" onClick={() => navigate(`/node-management?cluster=${id}`)}>集群节点</Button>
        <Button size="small" onClick={() => navigate(`/resource-governance?cluster=${id}`)}>资源治理</Button>
        <Button size="small" onClick={() => { setAiResult(null); setAiNs(activeNs && activeNs !== '_all_' ? activeNs : ''); setAiKinds([]); setAppInspectOpen(true) }}>应用巡检</Button>
        <Button size="small" type="primary" onClick={() => setInspectOpen(true)}>集群巡检</Button>
      </div>

      <Card size="small">
        {namespaces.length === 0 ? (
          <Empty description="暂无数据，请先触发采集或添加集群" />
        ) : (
          <Tabs activeKey={groupOfType(activeType)} onChange={(k) => changeType(GROUPS.find((g) => g.key === k)!.types[0])} items={tabItems}
            tabBarExtraContent={(
              <Space size={8}>
                <span style={{ color: '#8aa196', fontSize: 12 }}>命名空间</span>
                <Select
                  size="small"
                  style={{ width: 190 }}
                  value={activeNs}
                  onChange={(v) => {
                    const val = (v as string) || '_all_'
                    setActiveNs(val)
                    try { localStorage.setItem(`cluster_ns:${id}`, val) } catch { /* ignore */ }
                    const next = new URLSearchParams(searchParams)
                    if (val && val !== '_all_') next.set('ns', val)
                    else next.delete('ns')
                    setSearchParams(next, { replace: true })
                  }}
                  showSearch
                  optionFilterProp="label"
                  options={[
                    { value: '_all_', label: '全部命名空间' },
                    ...namespaces.map((n) => ({ value: n.namespace, label: n.namespace })),
                  ]}
                />
                {(() => {
                  const ns = activeNs === '_all_' ? 'default' : activeNs
                  if (activeType === 'deployments') return <Button type="primary" size="small" onClick={() => setWlCreate({ clusterId: Number(id), kind: 'deployment', namespace: ns })}>创建无状态</Button>
                  if (activeType === 'statefulsets') return <Button type="primary" size="small" onClick={() => setWlCreate({ clusterId: Number(id), kind: 'statefulset', namespace: ns })}>创建有状态</Button>
                  if (activeType === 'daemonsets') return <Button type="primary" size="small" onClick={() => setWlCreate({ clusterId: Number(id), kind: 'daemonset', namespace: ns })}>创建守护集</Button>
                  if (activeType === 'cronjobs') return <Button type="primary" size="small" onClick={() => setWlCreate({ clusterId: Number(id), kind: 'cronjob', namespace: ns })}>创建定时任务</Button>
                  if (activeType === 'configmaps') return <Button type="primary" size="small" onClick={() => setDataObj({ clusterId: Number(id), kind: 'configmap', namespace: ns, name: '', create: true })}>创建配置字典</Button>
                  if (activeType === 'secrets') return <Button type="primary" size="small" onClick={() => setDataObj({ clusterId: Number(id), kind: 'secret', namespace: ns, name: '', create: true })}>创建保密字典</Button>
                  if (activeType === 'services') return <Button type="primary" size="small" onClick={() => setSvcObj({ clusterId: Number(id), kind: 'service', namespace: ns, name: '', create: true })}>创建服务</Button>
                  if (activeType === 'ingresses') return <Button type="primary" size="small" onClick={() => setIngObj({ clusterId: Number(id), kind: 'ingress', namespace: ns, name: '', create: true })}>创建路由</Button>
                  if (activeType === 'pvcs') return <Button type="primary" size="small" onClick={() => setPvcObj({ clusterId: Number(id), kind: 'pvc', namespace: ns, name: '', create: true })}>创建存储</Button>
                  return undefined
                })()}
              </Space>
            )} />
        )}
      </Card>

      <PodMetricsDrawer pod={metricsPod} onClose={() => setMetricsPod(null)} />
      <PodLogsDrawer pod={logPod} onClose={() => setLogPod(null)} />
      <ExecPodModal pod={execTarget} onClose={() => setExecTarget(null)} />
      <PodYamlModal pod={yamlPod} onClose={() => setYamlPod(null)} />
      <PodEventsModal pod={eventsPod} onClose={() => setEventsPod(null)} />
      <PodDetailDrawer pod={detailPod} onClose={() => setDetailPod(null)} />
      <ConfigObjectYamlModal target={configObj} onClose={() => setConfigObj(null)} onSaved={reloadMisc} />
      <ConfigObjectInfoModal target={infoObj} onClose={() => setInfoObj(null)} onSaved={reloadMisc} />
      <ConfigObjectDataModal target={dataObj} onClose={() => setDataObj(null)} onSaved={reloadMisc} />
      <ServiceSettingsModal target={svcObj} onClose={() => setSvcObj(null)} onSaved={reloadMisc} />
      <IngressSettingsModal target={ingObj} onClose={() => setIngObj(null)} onSaved={reloadMisc} />
      <PvcCreateModal target={pvcObj} onClose={() => setPvcObj(null)} onSaved={reloadMisc} />
      <WorkloadYamlModal target={wlYaml} onClose={() => setWlYaml(null)} />
      <Modal title={mountTarget ? `${mountTypes.map((t) => ({ configMap: '配置字典', secret: '配置密钥', pvc: 'PVC' } as const)[t]).join(' / ')}：${mountTarget.namespace}/${mountTarget.name}` : ''}
        open={!!mountTarget} onCancel={() => setMountTarget(null)} footer={null} width={960} destroyOnClose>
        {mountTarget && (
          <WorkloadMountsPanel clusterId={mountTarget.clusterId} kind={mountTarget.kind}
            namespace={mountTarget.namespace} name={mountTarget.name} types={mountTypes} />
        )}
      </Modal>
      <WorkloadCreateModal target={wlCreate} onClose={() => setWlCreate(null)} onSaved={load} />
      <InspectModal clusterId={Number(id)} clusterName={clusterName} open={inspectOpen} onClose={() => setInspectOpen(false)} />

      <Modal title={`应用巡检：${clusterName || id}`} open={appInspectOpen} onCancel={() => setAppInspectOpen(false)}
        footer={<Button onClick={() => setAppInspectOpen(false)}>关闭</Button>} width={760}>
        <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', alignItems: 'flex-end', marginBottom: 12 }}>
          <div>
            <div style={{ fontSize: 12, color: '#888', marginBottom: 4 }}>命名空间（必选）</div>
            <Select style={{ width: 200 }} value={aiNs || undefined} onChange={setAiNs} placeholder="请选择命名空间"
              options={namespaces.map((n) => ({ label: n.namespace, value: n.namespace }))} />
          </div>
          <div>
            <div style={{ fontSize: 12, color: '#888', marginBottom: 4 }}>工作负载类型（自选，默认不选）</div>
            <Select mode="multiple" style={{ width: 300 }} value={aiKinds} onChange={setAiKinds} placeholder="请选择类型"
              options={[{ label: '无状态 Deployment', value: 'deployment' }, { label: '有状态 StatefulSet', value: 'statefulset' }, { label: '守护集 DaemonSet', value: 'daemonset' }]} />
          </div>
          <Button type="primary" loading={aiLoading} disabled={!aiNs || aiKinds.length === 0} onClick={runAppInspect}>开始巡检</Button>
        </div>
        <div style={{ color: '#888', fontSize: 12, marginBottom: 10 }}>
          对所选范围内的「有状态 / 无状态」工作负载巡检：检查内存/CPU 持续高水位、频繁重启等异常，命中则自动发起 AI 全面诊断（含 JVM/日志/指标），不建告警。
        </div>
        {aiResult && (
          aiResult.skipped ? <div>巡检跳过：{aiResult.reason}</div> : (
            <div>
              <div style={{ marginBottom: 8 }}>
                扫描工作负载 <b>{aiResult.scanned_workloads ?? '-'}</b> 个 · 发现异常 <b style={{ color: (aiResult.hits || 0) > 0 ? '#cf1322' : '#52c41a' }}>{aiResult.hits ?? 0}</b> 个 · 已诊断 <b>{aiResult.diagnosed ?? 0}</b> 个
                {(aiResult.skipped_open_alert || aiResult.skipped_cooldown) ? <span style={{ color: '#888' }}>（跳过：在途告警 {aiResult.skipped_open_alert ?? 0} / 冷却 {aiResult.skipped_cooldown ?? 0}）</span> : null}
              </div>
              {(aiResult.items || []).length > 0 ? (
                <Table size="small" rowKey="id" pagination={false} dataSource={aiResult.items}
                  columns={[
                    { title: '工作负载', dataIndex: 'workload' },
                    { title: '类型', dataIndex: 'kind', width: 110 },
                    { title: '命名空间', dataIndex: 'namespace', width: 130 },
                    { title: 'Pod', dataIndex: 'pod' },
                    { title: '原因', dataIndex: 'reason' },
                  ]} />
              ) : <Empty description="范围内未发现异常（阈值：内存/CPU ≥85% 持续、近 30min 重启 ≥2）" />}
              <div style={{ marginTop: 8, fontSize: 12, color: '#888' }}>诊断结果见「智能诊断 → 应用诊断助手」→ 带「巡检」标签的会话。</div>
            </div>
          )
        )}
      </Modal>
    </div>
  )
}

// 集群巡检报告弹窗：打开即触发体检，同时展示历史报告
function InspectModal({ clusterId, clusterName, open, onClose }: { clusterId: number; clusterName: string; open: boolean; onClose: () => void }) {
  const [report, setReport] = useState<Record<string, unknown> | null>(null)
  const [history, setHistory] = useState<Array<Record<string, unknown>>>([])
  const [loading, setLoading] = useState(false)
  const [prog, setProg] = useState<{ pct: number; step: string } | null>(null)

  const run = () => {
    setLoading(true)
    setProg({ pct: 5, step: '准备中…' })
    const timer = setInterval(() => {
      getClusterInspectProgress(clusterId).then((p) => { if (p) setProg({ pct: p.pct, step: p.step }) }).catch(() => {})
    }, 800)
    inspectCluster(clusterId)
      .then((d) => setReport(d as Record<string, unknown>))
      .catch((e) => message.error((e as { response?: { data?: { message?: string } } })?.response?.data?.message || '巡检失败'))
      .finally(() => { clearInterval(timer); setProg(null); setLoading(false) })
  }
  const loadHistory = () => getInspectionReports(clusterId).then((d) => setHistory((d as Array<Record<string, unknown>>) || [])).catch(() => {})

  const downloadReport = async (reportId: number) => {
    try {
      const blob = await downloadInspectionReport(reportId)
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `cluster-inspect-${clusterId}-${reportId}.md`
      a.click()
      URL.revokeObjectURL(url)
    } catch {
      message.error('下载失败')
    }
  }

  useEffect(() => {
    if (!open) return
    setReport(null)
    run()
    loadHistory()
  }, [open, clusterId])

  if (!open) return null
  const items = (report?.items as Array<Record<string, unknown>>) || []
  const stats = (report?.stats as Record<string, unknown>) || {}
  const healthColor: Record<string, string> = { healthy: 'green', warning: 'orange', critical: 'red' }
  return (
    <Modal title={`集群巡检：${clusterName || clusterId}`} open={open} onCancel={onClose} footer={<Button onClick={onClose}>关闭</Button>} width={760}>
      <Space style={{ marginBottom: 12 }}>
        <Button type="primary" loading={loading} onClick={run}>立即巡检</Button>
        <Button onClick={loadHistory}>刷新历史</Button>
      </Space>
      {loading && prog && (
        <Card size="small" style={{ marginBottom: 12 }}>
          <Progress percent={prog.pct} status="active" />
          <div style={{ marginTop: 6, color: '#666', fontSize: 12 }}>{prog.step}</div>
        </Card>
      )}
      {report ? (
        <div>
          <Card size="small" style={{ marginBottom: 12 }}>
            <Space direction="vertical" size={4}>
              <div><Tag color={healthColor[(report.health as string) || 'warning']}>{(report.health as string) || '-'}</Tag>
                <b>{(report.summary as string) || ''}</b></div>
              <div style={{ color: '#888', fontSize: 12 }}>巡检时间：{(report.inspected_at as string) || '-'} · 版本 {(report.version as string) || '-'}</div>
            </Space>
          </Card>
          <Table
            rowKey="name" size="small" pagination={false}
            dataSource={items}
            columns={[
              { title: '类别', dataIndex: 'category', width: 90 },
              { title: '体检项', dataIndex: 'name', width: 120 },
              { title: '结果', dataIndex: 'value' },
              { title: '状态', dataIndex: 'status', width: 90, render: (s: string) => <Tag color={healthColor[s] || 'default'}>{s}</Tag> },
            ]}
            locale={{ emptyText: '无体检项' }}
          />
          {(report.fault_top as Array<Record<string, unknown>>)?.length ? (
            <div style={{ marginTop: 12 }}>
              <b>故障 Top：</b>
              {(report.fault_top as Array<Record<string, unknown>>).map((f, i) => (
                <Tag key={String(f.fault_type)} style={{ marginLeft: i > 0 ? 4 : 8 }}>{String(f.fault_type)} ×{String(f.count)}</Tag>
              ))}
            </div>
          ) : null}
        </div>
      ) : <Empty description={loading ? '巡检中...' : '点击「立即巡检」开始体检'} />}
      {history.length > 0 ? (
        <div style={{ marginTop: 16 }}>
          <b>历史报告</b>
          <Table
            rowKey="report_id" size="small" pagination={false} style={{ marginTop: 8 }}
            dataSource={history}
            columns={[
              { title: '时间', dataIndex: 'inspected_at', width: 160 },
              { title: '结论', dataIndex: 'summary', ellipsis: true },
              { title: '健康', dataIndex: 'health', width: 90, render: (s: string) => <Tag color={healthColor[s] || 'default'}>{s}</Tag> },
              { title: '操作', width: 80, render: (_: unknown, r: Record<string, unknown>) => (
                <Button size="small" onClick={() => downloadReport(Number(r.report_id))}>下载</Button>
              ) },
            ]}
          />
        </div>
      ) : null}
    </Modal>
  )
}

// Pod 指标监控抽屉（CPU/内存/网络趋势）
function PodMetricsDrawer({ pod, onClose }: { pod: PodTarget | null; onClose: () => void }) {
  const [range, setRange] = useState<string>('1h')
  const [points, setPoints] = useState<MetricPoint[]>([])
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!pod) return
    setLoading(true)
    getPodMetrics(pod.clusterId, pod.namespace, pod.name, range)
      .then((d) => setPoints((d as { points?: MetricPoint[] }).points || []))
      .finally(() => setLoading(false))
  }, [pod, range])

  if (!pod) return null
  const ts = (v: MetricPoint) => (v?.sampled_at || '').slice(11, 19)
  const cpuData = points.map((p) => ({ t: ts(p), v: Number(p.cpu_usage) }))
  const memData = points.map((p) => ({ t: ts(p), v: Number(p.memory_usage) / 1024 / 1024 }))
  const netData = points.flatMap((p, i) => {
    const prev = i > 0 ? points[i - 1] : p
    const rx = Math.max(0, (Number(p.network_rx) - Number(prev.network_rx)) / 60 / 1024)
    const tx = Math.max(0, (Number(p.network_tx) - Number(prev.network_tx)) / 60 / 1024)
    return [{ t: ts(p), v: rx, type: 'rx' }, { t: ts(p), v: tx, type: 'tx' }]
  })
  const base = { data: [], xField: 't', yField: 'v', smooth: true, height: 180, animation: false }

  return (
    <Drawer title={`Pod 监控：${pod.name}`} open={!!pod} onClose={onClose} width={720}>
      <Space style={{ marginBottom: 12 }}>
        <Segmented value={range} onChange={(v) => setRange(v as string)} options={[
          { value: '1h', label: '1小时' }, { value: '24h', label: '24小时' }, { value: '72h', label: '3天' },
        ]} />
      </Space>
      {loading ? <Empty description="加载中..." /> : points.length === 0 ? <Empty description="暂无指标数据" /> : (
        <div>
          <div style={{ marginBottom: 16 }}><b>CPU（core）</b><Line {...base} data={cpuData} /></div>
          <div style={{ marginBottom: 16 }}><b>内存（MB）</b><Line {...base} data={memData} /></div>
          <div><b>网络（KB/s）</b><Line {...base} data={netData} colorField="type" /></div>
        </div>
      )}
    </Drawer>
  )
}

// Pod 容器日志抽屉
function PodLogsDrawer({ pod, onClose }: { pod: PodTarget | null; onClose: () => void }) {
  const [logs, setLogs] = useState('')
  const [loading, setLoading] = useState(false)
  const [container, setContainer] = useState('')
  const [streamOpen, setStreamOpen] = useState(false)

  const load = () => {
    if (!pod) return
    setLoading(true)
    getPodLogs(pod.clusterId, pod.namespace, pod.name, container, 200)
      .then((d) => setLogs((d as { logs?: string }).logs || '(无日志)'))
      .catch(() => setLogs('日志获取失败'))
      .finally(() => setLoading(false))
  }
  useEffect(() => { setContainer(''); load() }, [pod])

  if (!pod) return null
  return (
    <Drawer title={`容器日志：${pod.name}`} open={!!pod} onClose={onClose} width={900}>
      <Space style={{ marginBottom: 12 }}>
        <Input placeholder="容器名（空=第一个）" value={container} onChange={(e) => setContainer(e.target.value)} style={{ width: 220 }} />
        <Button type="primary" onClick={load}>查询</Button>
        <Button onClick={() => setStreamOpen(true)}>实时跟随</Button>
      </Space>
      <pre style={{ maxHeight: '70vh', overflow: 'auto', background: '#f5f5f5', padding: 12, fontSize: 12, whiteSpace: 'pre-wrap' }}>
        {loading ? '加载中...' : logs}
      </pre>
      <PodLogStreamModal
        pod={streamOpen && pod ? { clusterId: pod.clusterId, namespace: pod.namespace, name: pod.name } : null}
        onClose={() => setStreamOpen(false)} />
    </Drawer>
  )
}

// 进入容器执行命令弹窗
function ExecPodModal({ pod, onClose }: { pod: PodTarget | null; onClose: () => void }) {
  const [command, setCommand] = useState('')
  const [container, setContainer] = useState('')
  const [output, setOutput] = useState('')
  const [loading, setLoading] = useState(false)

  const run = async () => {
    if (!command.trim()) return
    setLoading(true)
    try {
      const d = (await execPod(pod!.clusterId, pod!.namespace, pod!.name, command.trim(), container)) as { output?: string }
      setOutput(d?.output || '(无输出)')
    } catch {
      setOutput('执行失败')
    } finally {
      setLoading(false)
    }
  }
  useEffect(() => { setCommand(''); setContainer(''); setOutput('') }, [pod])

  if (!pod) return null
  return (
    <Modal title={`进入容器：${pod.name}`} open={!!pod} onCancel={onClose} footer={null} width={820}>
      <Space.Compact style={{ width: '100%', marginBottom: 12 }}>
        <Input placeholder="容器名（空=第一个）" value={container} onChange={(e) => setContainer(e.target.value)} style={{ width: 200 }} />
        <Input placeholder="只读命令：ps aux / env / df -h / free -m / ls / cat 等" value={command}
          onChange={(e) => setCommand(e.target.value)} onPressEnter={run} />
        <Button type="primary" onClick={run} loading={loading}>执行</Button>
      </Space.Compact>
      <pre style={{ maxHeight: '50vh', overflow: 'auto', background: '#1e1e1e', color: '#0f0', padding: 12, fontSize: 12, whiteSpace: 'pre-wrap' }}>
        {output || '输入命令后执行，结果在此显示'}
      </pre>
    </Modal>
  )
}

// 编辑 Pod YAML 弹窗
function PodYamlModal({ pod, onClose }: { pod: PodTarget | null; onClose: () => void }) {
  const [yaml, setYaml] = useState('')
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!pod) return
    setLoading(true)
    getPodYaml(pod.clusterId, pod.namespace, pod.name)
      .then((d) => setYaml((d as { yaml?: string }).yaml || ''))
      .catch(() => message.error('YAML 获取失败'))
      .finally(() => setLoading(false))
  }, [pod])

  const apply = async () => {
    if (!yaml.trim()) return
    setLoading(true)
    try {
      await applyPodYaml(pod!.clusterId, pod!.namespace, pod!.name, yaml)
      message.success('YAML 已应用')
      onClose()
    } catch {
      message.error('YAML 应用失败')
    } finally {
      setLoading(false)
    }
  }

  if (!pod) return null
  return (
    <Modal title={`编辑 Pod YAML：${pod.name}`} open={!!pod} onCancel={onClose} onOk={apply} okText="应用" confirmLoading={loading} width={900}>
      <Input.TextArea value={yaml} onChange={(e) => setYaml(e.target.value)} rows={22} style={{ fontFamily: 'monospace', fontSize: 12 }} />
    </Modal>
  )
}
