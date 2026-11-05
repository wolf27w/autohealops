// 单个 Pod 的事件弹窗：实时事件 + 平台自存的历史事件（k8s 默认只留 1 小时）
import { useEffect, useState } from 'react'
import { Modal, Table, Tag, Empty, message, Alert } from 'antd'
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
  const [history, setHistory] = useState<PodEvent[]>([])
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!pod) return
    setLoading(true)
    setEvents([])
    setHistory([])
    getPodEvents(pod.clusterId, pod.namespace, pod.name)
      .then((d) => {
        const r = d as { events?: PodEvent[]; history?: PodEvent[] }
        setEvents(r.events || [])
        setHistory(r.history || [])
      })
      .catch(() => message.error('事件获取失败'))
      .finally(() => setLoading(false))
  }, [pod])

  // 合并实时 + 历史（历史里已包含采集到的实时事件，按 原因+首次时间 去重）
  const seen = new Set<string>()
  const rows: PodEvent[] = []
  for (const e of [...events.map((x) => ({ ...x, source: '实时' })), ...history]) {
    const k = `${e.reason}|${e.first_timestamp}|${(e.message || '').slice(0, 60)}`
    if (seen.has(k)) continue
    seen.add(k)
    rows.push({ ...e, source: e.source === '实时' ? '实时' : '历史' })
  }
  rows.sort((a, b) => String(b.last_timestamp || '').localeCompare(String(a.last_timestamp || '')))

  return (
    <Modal title={`事件：${pod?.name || ''}`} open={!!pod} onCancel={onClose} footer={null} width={1000}>
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 10 }}
        message="k8s 默认只保留 1 小时事件（--event-ttl）；「历史」是平台自存的事件，保留 7 天，容器重启/重建后仍可回溯。"
      />
      <Table
        rowKey={(_, i) => String(i)}
        size="small"
        loading={loading}
        dataSource={rows}
        pagination={{ pageSize: 10, hideOnSinglePage: true }}
        columns={[
          { title: '类型', dataIndex: 'type', width: 90, render: (v: string) => <Tag color={v === 'Warning' ? 'red' : 'blue'}>{v}</Tag> },
          { title: '原因', dataIndex: 'reason', width: 160 },
          { title: '消息', dataIndex: 'message', render: (v: string) => <span style={{ whiteSpace: 'pre-wrap' }}>{v}</span> },
          { title: '次数', dataIndex: 'count', width: 60 },
          { title: '首次', dataIndex: 'first_timestamp', width: 160, render: fmt },
          { title: '最后', dataIndex: 'last_timestamp', width: 160, render: fmt },
          { title: '来源', dataIndex: 'source', width: 70, render: (v: string) => <Tag color={v === '历史' ? 'default' : 'green'}>{v}</Tag> },
        ]}
        locale={{ emptyText: <Empty description="暂无事件" /> }}
      />
    </Modal>
  )
}
