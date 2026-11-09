// 发布历史（R19）：构建记录列表 → 点开看 Blue Ocean 式阶段/日志
import { useEffect, useState } from 'react'
import { Card, Table, Tag, Button, Space, Select, Modal } from 'antd'
import { getReleaseBuilds, getClusters } from '../services/api'
import type { ReleaseBuild } from '../services/api'
import type { Cluster } from '../types'
import PipelineView from '../components/PipelineView'

const stColor = (s: string) => {
  if (s === 'success') return 'green'
  if (s === 'failure') return 'red'
  if (s === 'running' || s === 'queued') return 'blue'
  if (s === 'aborted') return 'orange'
  return 'default'
}
const stText = (s: string) => {
  const m: Record<string, string> = { success: '成功', failure: '失败', running: '构建中', queued: '排队中', aborted: '已中止', unknown: '未知' }
  return m[s] || s
}

export default function ReleaseHistory() {
  const [builds, setBuilds] = useState<ReleaseBuild[]>([])
  const [clusters, setClusters] = useState<Cluster[]>([])
  const [clusterId, setClusterId] = useState<number>()
  const [ns, setNs] = useState<string>('')
  const [loading, setLoading] = useState(false)
  const [sel, setSel] = useState<ReleaseBuild | null>(null)

  const load = () => {
    setLoading(true)
    getReleaseBuilds({ cluster_id: clusterId, namespace: ns || undefined, limit: 200 })
      .then((d) => setBuilds(d || []))
      .catch(() => setBuilds([]))
      .finally(() => setLoading(false))
  }
  useEffect(() => { getClusters().then((d) => setClusters((d || []) as Cluster[])) }, [])
  useEffect(load, [clusterId, ns])

  const clusterName = (id: number) => clusters.find((c) => c.id === id)?.name || id

  const cols = [
    { title: '时间', dataIndex: 'created_at', width: 170, render: (v: string) => (v || '').slice(0, 19).replace('T', ' ') },
    { title: '集群', dataIndex: 'cluster_id', width: 110, render: (v: number) => clusterName(v) },
    { title: 'namespace', dataIndex: 'namespace', width: 130 },
    { title: '服务', dataIndex: 'service_name', width: 160 },
    { title: 'Job', dataIndex: 'job_name', ellipsis: true },
    { title: '分支', dataIndex: 'branch', width: 150, ellipsis: true },
    { title: '构建号', dataIndex: 'build_number', width: 80 },
    { title: '状态', dataIndex: 'status', width: 90, render: (s: string) => <Tag color={stColor(s)}>{stText(s)}</Tag> },
    { title: '操作人', dataIndex: 'triggered_by_name', width: 150, ellipsis: true },
    { title: '操作', width: 80, render: (_: unknown, r: ReleaseBuild) => (
      <Button size="small" type="link" onClick={() => setSel(r)}>查看</Button>
    ) },
  ]

  return (
    <Card size="small" title="发布历史"
      extra={<Space>
        <Select allowClear placeholder="集群" style={{ width: 160 }} value={clusterId} onChange={setClusterId}
          options={clusters.map((c) => ({ value: c.id, label: c.name }))} />
        <Select allowClear placeholder="namespace" style={{ width: 160 }} value={ns || undefined} onChange={(v) => setNs(v || '')}
          options={Array.from(new Set(builds.map((b) => b.namespace))).map((n) => ({ value: n, label: n }))} />
        <Button onClick={load}>刷新</Button>
      </Space>}>
      <Table rowKey="id" size="small" loading={loading} dataSource={builds} columns={cols}
        pagination={{ pageSize: 20, showTotal: (t) => `共 ${t} 条` }} />

      <Modal open={!!sel} onCancel={() => setSel(null)} footer={null} width={860}
        title={sel ? `${sel.service_name} · ${sel.job_name} #${sel.build_number}` : ''} destroyOnClose>
        {sel && <PipelineView buildId={sel.id} jobName={sel.job_name} buildNumber={sel.build_number} live={sel.status === 'running' || sel.status === 'queued'} />}
      </Modal>
    </Card>
  )
}
