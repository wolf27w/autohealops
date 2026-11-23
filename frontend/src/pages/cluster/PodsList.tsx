// 容器组列表页：命名空间下拉 + 名称搜索 + 5s 轮询（无刷新按钮，全局顶栏有）
// 行操作收进 ⋯：监控/日志/进容器/JVM 诊断/编辑 YAML/隔离/删除；支持行选择批量删除
import { useEffect, useMemo, useState, type Key } from 'react'
import { Card, Table, Tag, Button, Space, Select, Input, Dropdown, Modal, message, Drawer, Empty, Segmented } from 'antd'
import { MoreOutlined, SearchOutlined } from '@ant-design/icons'
import { Line } from '@ant-design/charts'
import {
  getClusterPods, getPodMetrics, getPodYaml, applyPodYaml,
  deleteWorkload, isolatePod, unisolatePod, getIsolatedPods,
} from '../../services/api'
import type { ClusterPod } from '../../services/api'
import type { PodTarget, MetricPoint } from '../../types'
import { useClusterNamespace, NS_ALL, nsParam } from './NamespaceContext'
import PodDetailDrawer from '../../components/PodDetailDrawer'
import PodLogStreamModal from '../../components/PodLogStreamModal'
import TerminalModal from '../../components/TerminalModal'
import JvmDiagModal from '../../components/JvmDiagModal'
import { podStatusColor, podReadyText } from '../../utils/podStatus'

const fmt = (v?: string) => (v ? String(v).slice(0, 19).replace('T', ' ') : '-')
const podStatus = (p: ClusterPod) => p.status || p.phase || (p.ready ? 'Running' : 'Pending')

export default function PodsList({ clusterId }: { clusterId: number }) {
  const { ns, setNs, namespaces, loading: nsLoading } = useClusterNamespace()
  const [pods, setPods] = useState<ClusterPod[]>([])
  const [searchText, setSearchText] = useState('')
  const [selected, setSelected] = useState<Key[]>([])
  const [isolated, setIsolated] = useState<Set<string>>(new Set())

  // 弹窗状态
  const [detailPod, setDetailPod] = useState<PodTarget | null>(null)
  const [metricsPod, setMetricsPod] = useState<PodTarget | null>(null)
  const [logPod, setLogPod] = useState<PodTarget | null>(null)
  const [execPod, setExecPod] = useState<PodTarget | null>(null)
  const [jvmPod, setJvmPod] = useState<PodTarget | null>(null)
  const [yamlPod, setYamlPod] = useState<PodTarget | null>(null)

  const load = () => {
    getClusterPods(clusterId, nsParam(ns))
      .then((d) => setPods((d as { pods?: ClusterPod[] }).pods || []))
      .catch(() => setPods([]))
  }

  // 首次 + 切换命名空间立即加载
  useEffect(() => { load() }, [clusterId, ns]) // eslint-disable-line react-hooks/exhaustive-deps

  // 5s 轮询
  useEffect(() => {
    const t = setInterval(load, 5000)
    return () => clearInterval(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [clusterId, ns])

  // 具体命名空间时加载「已隔离 Pod」集合（「全部」无法按 ns 查，清空兜底）
  useEffect(() => {
    if (ns === NS_ALL) { setIsolated(new Set()); return }
    getIsolatedPods(clusterId, ns)
      .then((d) => setIsolated(new Set(((d as { pods?: string[] }).pods || []).map((n) => `${ns}-${n}`))))
      .catch(() => setIsolated(new Set()))
  }, [clusterId, ns])

  // 命名空间变化时清空勾选
  useEffect(() => { setSelected([]) }, [ns])

  const rows = useMemo(() => {
    const q = searchText.trim().toLowerCase()
    return pods.filter((p) => {
      if (q && !(p.name || '').toLowerCase().includes(q) && !(p.namespace || '').toLowerCase().includes(q)) return false
      return true
    })
  }, [pods, searchText])

  const selectedItems = useMemo(
    () => rows.filter((p) => selected.includes(`${p.namespace}-${p.name}`)),
    [rows, selected],
  )

  const handleIsolate = (p: ClusterPod) => {
    Modal.confirm({
      title: `隔离容器组 ${p.namespace}/${p.name}？`,
      content: '隔离后该 Pod 会从 Service 端点摘除（不再接收新流量），请确认。',
      okText: '隔离',
      onOk: async () => {
        try {
          await isolatePod(clusterId, p.namespace, p.name)
          message.success('已隔离')
          setIsolated((prev) => new Set(prev).add(`${p.namespace}-${p.name}`))
        } catch { message.error('隔离失败') }
      },
    })
  }

  const handleUnisolate = async (p: ClusterPod) => {
    try {
      await unisolatePod(clusterId, p.namespace, p.name)
      message.success('已取消隔离')
      setIsolated((prev) => { const n = new Set(prev); n.delete(`${p.namespace}-${p.name}`); return n })
    } catch { message.error('取消隔离失败') }
  }

  const confirmDeleteOne = (p: ClusterPod) => {
    Modal.confirm({
      title: `删除容器组 ${p.namespace}/${p.name}？`,
      content: 'Pod 被控制器托管时会自动重建；裸 Pod 删除后不再恢复。',
      okText: '确认删除',
      okButtonProps: { danger: true },
      onOk: () => deleteWorkload(clusterId, 'pod', p.namespace, p.name)
        .then(() => { message.success('已删除'); load() })
        .catch(() => message.error('删除失败')),
    })
  }

  const confirmBatchDelete = () => {
    if (!selectedItems.length) { message.warning('请先选择容器组'); return }
    const names = selectedItems.slice(0, 8).map((p) => `${p.namespace}/${p.name}`).join('\n')
    Modal.confirm({
      title: `批量删除：${selectedItems.length} 个容器组？`,
      content: (
        <div>
          <div style={{ whiteSpace: 'pre-line', fontFamily: 'monospace', fontSize: 12, maxHeight: 160, overflow: 'auto', marginBottom: 8 }}>
            {names}{selectedItems.length > 8 ? `\n… 共 ${selectedItems.length} 个` : ''}
          </div>
          <div style={{ color: '#cf1322' }}>被控制器托管的 Pod 会自动重建；裸 Pod 删除后不再恢复。</div>
        </div>
      ),
      okText: '确认删除',
      okButtonProps: { danger: true },
      onOk: async () => {
        const results = await Promise.allSettled(selectedItems.map((p) => deleteWorkload(clusterId, 'pod', p.namespace, p.name)))
        const ok = results.filter((r) => r.status === 'fulfilled').length
        if (ok === results.length) message.success(`已删除 ${ok} 个`)
        else message.warning(`成功 ${ok}/${results.length}，失败 ${results.length - ok} 个`)
        setSelected([])
        load()
      },
    })
  }

  const openAction = (key: string, p: ClusterPod) => {
    const tgt: PodTarget = { clusterId, namespace: p.namespace, name: p.name }
    if (key === 'metrics') setMetricsPod(tgt)
    else if (key === 'logs') setLogPod(tgt)
    else if (key === 'exec') setExecPod(tgt)
    else if (key === 'jvm') setJvmPod(tgt)
    else if (key === 'yaml') setYamlPod(tgt)
    else if (key === 'isolate') handleIsolate(p)
    else if (key === 'unisolate') handleUnisolate(p)
    else if (key === 'delete') confirmDeleteOne(p)
  }

  const columns = [
    { title: '名称', dataIndex: 'name', render: (v: string, r: ClusterPod) => (
      <a onClick={() => setDetailPod({ clusterId, namespace: r.namespace, name: r.name })}>{v}</a>
    ) },
    { title: '命名空间', dataIndex: 'namespace', width: 180 },
    { title: '状态', dataIndex: 'status', width: 160,
      filters: [
        { text: 'Running', value: 'Running' },
        { text: 'Pending', value: 'Pending' },
        { text: 'CrashLoopBackOff', value: 'CrashLoopBackOff' },
        { text: 'Terminating', value: 'Terminating' },
        { text: 'Succeeded', value: 'Succeeded' },
        { text: 'Failed', value: 'Failed' },
      ],
      onFilter: (value: unknown, r: ClusterPod) => podStatus(r) === value,
      render: (_: unknown, r: ClusterPod) => (
        <Tag color={podStatusColor(podStatus(r))}>{podStatus(r)}</Tag>
      ) },
    { title: '就绪', key: 'ready', width: 70, render: (_: unknown, r: ClusterPod) => podReadyText(r.ready_containers, r.total_containers) },
    { title: '重启', dataIndex: 'restart_count', width: 70, render: (v: number) => (v ?? '-') },
    { title: '节点', dataIndex: 'node_name', width: 160, render: (v: string) => v || '-' },
    { title: '创建时间', dataIndex: 'created_at', width: 165, render: fmt },
    { title: '操作', width: 70, render: (_: unknown, r: ClusterPod) => {
      const key = `${r.namespace}-${r.name}`
      return (
        <Dropdown trigger={['click']} menu={{
          items: [
            // 2026-11-23 R64：统一顺序（与 WorkloadDetail 一致）——进容器→日志→监控→JVM→隔离→YAML→删除
            { key: 'exec', label: '进容器' },
            { key: 'logs', label: '日志' },
            { key: 'metrics', label: '监控' },
            { key: 'jvm', label: 'JVM 诊断' },
            isolated.has(key) ? { key: 'unisolate', label: '取消隔离' } : { key: 'isolate', label: '隔离' },
            { key: 'yaml', label: '查看 YAML' },
            { type: 'divider' as const },
            { key: 'delete', label: '删除', danger: true },
          ],
          onClick: ({ key: k }) => openAction(k, r),
        }}>
          <Button size="small" type="link"><MoreOutlined /></Button>
        </Dropdown>
      )
    } },
  ]

  return (
    <div>
      <Card size="small">
        <Space size={8} style={{ marginBottom: 12 }}>
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
          <Input
            size="small"
            allowClear
            placeholder="搜索名称"
            prefix={<SearchOutlined />}
            style={{ width: 180 }}
            value={searchText}
            onChange={(e) => setSearchText(e.target.value)}
          />
        </Space>

        {selectedItems.length > 0 && (
          <Space size={8} wrap style={{ marginBottom: 12 }}>
            <span>已选 <b>{selectedItems.length}</b> 个容器组</span>
            <Button size="small" danger onClick={confirmBatchDelete}>批量删除</Button>
            <Button size="small" type="link" onClick={() => setSelected([])}>清除选择</Button>
          </Space>
        )}

        <Table
          rowKey={(r) => `${r.namespace}-${r.name}`}
          size="small"
          dataSource={rows}
          columns={columns}
          rowSelection={{ selectedRowKeys: selected, onChange: (keys: Key[]) => setSelected(keys) }}
          pagination={{
            pageSize: 20,
            showSizeChanger: true,
            pageSizeOptions: ['10', '20', '50', '100'],
            showTotal: (t: number) => `共 ${t} 条`,
            size: 'small',
          }}
          locale={{ emptyText: '无 Pod' }}
        />
      </Card>

      <PodDetailDrawer pod={detailPod} onClose={() => setDetailPod(null)} />
      <PodMetricsDrawer pod={metricsPod} onClose={() => setMetricsPod(null)} />
      <PodLogStreamModal pod={logPod} onClose={() => setLogPod(null)} />
      <TerminalModal pod={execPod} onClose={() => setExecPod(null)} />
      <JvmDiagModal pod={jvmPod} onClose={() => setJvmPod(null)} />
      <PodYamlModal pod={yamlPod} onClose={() => setYamlPod(null)} />
    </div>
  )
}

// Pod 指标监控抽屉（CPU/内存/网络趋势；数据源 = getPodMetrics）
function PodMetricsDrawer({ pod, onClose }: { pod: PodTarget | null; onClose: () => void }) {
  const [range, setRange] = useState('1h')
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
          { value: '10m', label: '10分钟' }, { value: '1h', label: '1小时' }, { value: '24h', label: '24小时' }, { value: '72h', label: '3天' }, { value: '168h', label: '7天' },
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
    if (!yaml.trim()) { message.warning('YAML 不能为空'); return }
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
