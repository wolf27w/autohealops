// 单个 Pod 的事件弹窗（k8s Event，按最后发生时间倒序）
import { useEffect, useState } from 'react'
import { Modal, Table, Tag, Empty, message } from 'antd'
import { getPodEvents } from '../services/api'
import type { PodTarget } from '../types'

interface PodEvent {
  type: string
  reason: string
  message: string
  count: number
  first_timestamp: string
  last_timestamp: string
  source: string
}

const fmt = (t?: string) => (t ? t.slice(0, 19).replace('T', ' ') : '-')

export default function PodEventsModal({ pod, onClose }: { pod: PodTarget | null; onClose: () => void }) {
  const [events, setEvents] = useState<PodEvent[]>([])
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!pod) return
    setLoading(true)
    getPodEvents(pod.clusterId, pod.namespace, pod.name)
      .then((d) => setEvents((d as { events?: PodEvent[] }).events || []))
      .catch(() => message.error('事件获取失败'))
      .finally(() => setLoading(false))
  }, [pod])

  return (
    <Modal title={`事件：${pod?.name || ''}`} open={!!pod} onCancel={onClose} footer={null} width={920}>
      <Table
        rowKey={(_, i) => String(i)}
        size="small"
        loading={loading}
        dataSource={events}
        pagination={{ pageSize: 10, hideOnSinglePage: true }}
        columns={[
          { title: '类型', dataIndex: 'type', width: 90, render: (v: string) => <Tag color={v === 'Warning' ? 'red' : 'blue'}>{v}</Tag> },
          { title: '原因', dataIndex: 'reason', width: 170 },
          { title: '消息', dataIndex: 'message', render: (v: string) => <span style={{ whiteSpace: 'pre-wrap' }}>{v}</span> },
          { title: '次数', dataIndex: 'count', width: 60 },
          { title: '最后发生', dataIndex: 'last_timestamp', width: 165, render: fmt },
        ]}
        locale={{ emptyText: <Empty description="暂无事件" /> }}
      />
    </Modal>
  )
}
