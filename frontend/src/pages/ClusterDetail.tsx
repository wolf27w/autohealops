// 集群详情页：命名空间下拉 + 工作负载类型 Tab 左右切换（无状态/有状态/守护集/定时任务/容器组）
// 注：按老板要求**去掉「搜索（服务名）」输入框**，只保留命名空间下拉
import { useEffect, useMemo, useState } from 'react'
import type { PodTarget, MetricPoint } from '../types'
import { Card, Table, Tag, Button, Space, Select, Tabs, Empty, Drawer, Segmented, Modal, Input, message, Dropdown } from 'antd'
import { ArrowLeftOutlined, FilterOutlined } from '@ant-design/icons'
import { useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { Line } from '@ant-design/charts'
import { getClusterDetail, getClusters, triggerCollect, getPodMetrics, getPodLogs, getPodYaml, applyPodYaml, execPod, inspectCluster, getInspectionReports } from '../services/api'

const POD_STATUS_COLOR: Record<string, string> = {
  Running: 'green', Pending: 'orange', Failed: 'red', Succeeded: 'blue', Unknown: 'gray',
}

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
  const [activeType, setActiveType] = useState('deployments')
  const [loading, setLoading] = useState(false)
  const [metricsPod, setMetricsPod] = useState<PodTarget | null>(null)
  const [logPod, setLogPod] = useState<PodTarget | null>(null)
  const [execTarget, setExecTarget] = useState<PodTarget | null>(null)
  const [yamlPod, setYamlPod] = useState<PodTarget | null>(null)
  const [statusFilter, setStatusFilter] = useState<string | undefined>(undefined)
  const [page, setPage] = useState(1)
  const [inspectOpen, setInspectOpen] = useState(false)

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
  ]

  const tabItems = [
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
        <Table rowKey={(r) => `${r.namespace}-${r.name}`} size="small" dataSource={finalData.pods}
          pagination={paginationConfig}
          columns={[
            { title: '命名空间', dataIndex: 'namespace', width: 180 },
            { title: '名称', dataIndex: 'name' },
            { title: '状态', dataIndex: 'status', width: 120, render: (s: string) => <Tag color={POD_STATUS_COLOR[s] || 'default'}>{s}</Tag> },
            { title: '资源（最小~最大）', render: (_: unknown, r: ResourceItem) => renderResources(r) || '-' },
            { title: '操作', width: 230, render: (_: unknown, r: ResourceItem) => (
              <Space size="small">
                <Button size="small" type="link" onClick={() => setMetricsPod({ ...r, clusterId: Number(id) })}>监控</Button>
                <Button size="small" type="link" onClick={() => setLogPod({ ...r, clusterId: Number(id) })}>日志</Button>
                <Button size="small" type="link" onClick={() => setExecTarget({ ...r, clusterId: Number(id) })}>进容器</Button>
                <Button size="small" type="link" onClick={() => setYamlPod({ ...r, clusterId: Number(id) })}>YAML</Button>
              </Space>
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
          ]}
          locale={{ emptyText: '无 CronJob' }} />
      ),
    },
  ]

  return (
    <div>
      <Space style={{ marginBottom: 16 }} wrap>
        <Button icon={<ArrowLeftOutlined />} onClick={() => navigate('/clusters')}>返回</Button>
        <span style={{ fontSize: 16, fontWeight: 'bold' }}>{clusterName || `集群 ${id}`}</span>
        <Button onClick={() => triggerCollect(Number(id)).then(() => load())}>刷新采集</Button>
        <Button type="primary" onClick={() => setInspectOpen(true)}>巡检</Button>
      </Space>

      <Card size="small">
        <div style={{ marginBottom: 12, display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
          <span>命名空间：</span>
          <Select
            style={{ width: 260 }}
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
        </div>

        {namespaces.length === 0 ? (
          <Empty description="暂无数据，请先触发采集或添加集群" />
        ) : (
          <Tabs activeKey={activeType} onChange={setActiveType} items={tabItems} />
        )}
      </Card>

      <PodMetricsDrawer pod={metricsPod} onClose={() => setMetricsPod(null)} />
      <PodLogsDrawer pod={logPod} onClose={() => setLogPod(null)} />
      <ExecPodModal pod={execTarget} onClose={() => setExecTarget(null)} />
      <PodYamlModal pod={yamlPod} onClose={() => setYamlPod(null)} />
      <InspectModal clusterId={Number(id)} clusterName={clusterName} open={inspectOpen} onClose={() => setInspectOpen(false)} />
    </div>
  )
}

// 集群巡检报告弹窗：打开即触发体检，同时展示历史报告
function InspectModal({ clusterId, clusterName, open, onClose }: { clusterId: number; clusterName: string; open: boolean; onClose: () => void }) {
  const [report, setReport] = useState<Record<string, unknown> | null>(null)
  const [history, setHistory] = useState<Array<Record<string, unknown>>>([])
  const [loading, setLoading] = useState(false)

  const run = () => {
    setLoading(true)
    inspectCluster(clusterId)
      .then((d) => setReport(d as Record<string, unknown>))
      .catch((e) => message.error((e as { response?: { data?: { message?: string } } })?.response?.data?.message || '巡检失败'))
      .finally(() => setLoading(false))
  }
  const loadHistory = () => getInspectionReports(clusterId).then((d) => setHistory((d as Array<Record<string, unknown>>) || [])).catch(() => {})

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
      </Space>
      <pre style={{ maxHeight: '70vh', overflow: 'auto', background: '#f5f5f5', padding: 12, fontSize: 12, whiteSpace: 'pre-wrap' }}>
        {loading ? '加载中...' : logs}
      </pre>
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
