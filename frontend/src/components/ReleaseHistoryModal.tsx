// 某服务的发布历史（弹窗）：列表 + 点开看阶段/日志
import { useEffect, useState } from 'react'
import { Modal, Table, Tag, Button, Space, Empty } from 'antd'
import { getReleaseBuilds } from '../services/api'
import type { ReleaseBuild } from '../services/api'
import PipelineView from './PipelineView'

interface Props {
  open: boolean
  clusterId: number
  namespace?: string
  serviceName?: string
  onClose: () => void
}

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

export default function ReleaseHistoryModal({ open, clusterId, namespace, serviceName, onClose }: Props) {
  const [builds, setBuilds] = useState<ReleaseBuild[]>([])
  const [loading, setLoading] = useState(false)
  const [sel, setSel] = useState<ReleaseBuild | null>(null)

  const load = () => {
    setLoading(true)
    getReleaseBuilds({ cluster_id: clusterId, namespace: namespace || undefined, service_name: serviceName || undefined, limit: 200 })
      .then((d) => setBuilds(d || [])).catch(() => setBuilds([])).finally(() => setLoading(false))
  }
  useEffect(() => {
    if (!open) return
    setSel(null); load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, clusterId, namespace, serviceName])

  const cols = [
    { title: '时间', dataIndex: 'created_at', width: 170, render: (v: string) => (v || '').slice(0, 19).replace('T', ' ') },
    { title: '构建号', dataIndex: 'build_number', width: 80 },
    { title: '分支', dataIndex: 'branch', ellipsis: true },
    { title: '状态', dataIndex: 'status', width: 90, render: (s: string) => <Tag color={stColor(s)}>{stText(s)}</Tag> },
    { title: '操作人', dataIndex: 'triggered_by_name', width: 150, ellipsis: true },
    { title: '操作', width: 80, render: (_: unknown, r: ReleaseBuild) => (
      <Button size="small" type="link" onClick={() => setSel(r)}>查看</Button>
    ) },
  ]

  return (
    <Modal open={open} onCancel={onClose} footer={null} width={860} title={serviceName ? `发布历史：${serviceName}` : '发布历史'} destroyOnClose>
      <Space direction="vertical" style={{ width: '100%' }}>
        {builds.length === 0 && !loading
          ? <Empty description="暂无发布记录" />
          : <Table rowKey="id" size="small" loading={loading} dataSource={builds} columns={cols}
              pagination={{ pageSize: 10, showTotal: (t) => `共 ${t} 条` }} />}
        {sel && (
          <div style={{ marginTop: 8 }}>
            <div style={{ fontSize: 12, color: '#8c8c8c', marginBottom: 4 }}>
              {sel.job_name} #{sel.build_number}（{stText(sel.status)}）
            </div>
            <PipelineView buildId={sel.id} jobName={sel.job_name} buildNumber={sel.build_number}
              live={sel.status === 'running' || sel.status === 'queued'} />
          </div>
        )}
      </Space>
    </Modal>
  )
}
