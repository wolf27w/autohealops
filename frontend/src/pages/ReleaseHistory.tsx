// 发布历史（R19）：构建记录列表 → 点开看 Blue Ocean 式阶段/日志
import { useEffect, useState } from 'react'
import { Card, Table, Tag, Button, Space, Select, Modal, Popconfirm, message } from 'antd'
import { getReleaseBuilds, getClusters, rollbackRelease, rollbackWorkloadImage } from '../services/api'
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
  const [rolling, setRolling] = useState<number | null>(null)
  const [imgRolling, setImgRolling] = useState<number | null>(null)

  // 2026-11-23 R70：直接回滚集群里的镜像（走平台回滚 RPC，不重新构建）
  const doImageRollback = async (r: ReleaseBuild) => {
    setImgRolling(r.id)
    try {
      const res = await rollbackWorkloadImage(r.cluster_id, r.namespace, 'deployment', r.service_name, 0)
      message.success(`已回滚：${r.namespace}/${r.service_name} revision ${res.from_revision} → ${res.to_revision}`)
      load()
    } catch (e) {
      message.error(`回滚失败：${(e as Error).message}`)
    } finally {
      setImgRolling(null)
    }
  }

  const doRollback = async (r: ReleaseBuild) => {
    setRolling(r.id)
    try {
      const res = await rollbackRelease(r.id)
      message.success(`已触发回滚：基于 #${res.rollback_of} → 新构建 #${res.build_number}（分支 ${res.branch || '默认'}）`)
      load()
    } catch (e) {
      message.error(`回滚失败：${(e as Error).message}`)
    } finally {
      setRolling(null)
    }
  }

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
    {
      title: '来源', dataIndex: 'rollback_of_build_id', width: 100,
      render: (v: number | null | undefined) => (v ? <Tag color="orange">回滚</Tag> : <span style={{ color: '#999' }}>发布</span>),
    },
    { title: '操作人', dataIndex: 'triggered_by_name', width: 150, ellipsis: true },
    { title: '操作', width: 160, render: (_: unknown, r: ReleaseBuild) => (
      <Space size={4}>
        <Button size="small" type="link" onClick={() => setSel(r)}>查看</Button>
        <Popconfirm
          title={`重发构建 #${r.build_number}？`}
          description={`将以 ${r.service_name} 的分支「${r.branch || '默认'}」重新触发 ${r.job_name}（会走 Jenkins 重新构建）`}
          okText="重发" cancelText="取消" onConfirm={() => doRollback(r)}
        >
          <Button size="small" type="link" loading={rolling === r.id}
            disabled={r.status === 'queued' || r.status === 'running'}>重发</Button>
        </Popconfirm>
        {/* 2026-11-23 R70：集群侧镜像回滚（不重新构建，直接切回上一个 revision） */}
        <Popconfirm
          title={`把 ${r.namespace}/${r.service_name} 回滚到上一个版本？`}
          description="直接修改集群 Deployment 回到上一个 revision（不重新构建，立即生效）"
          okText="回滚" cancelText="取消" onConfirm={() => doImageRollback(r)}
        >
          <Button size="small" type="link" danger loading={imgRolling === r.id}
            disabled={r.status === 'queued' || r.status === 'running'}>回滚镜像</Button>
        </Popconfirm>
      </Space>
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
