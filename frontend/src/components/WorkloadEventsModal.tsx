// 工作负载历史事件：平台自存 k8s_events（k8s 默认只留 1 小时，Pod 重建后旧事件消失）
import { useEffect, useState } from 'react'
import { Modal, Table, Tag, Empty, Select, Space, message } from 'antd'
import { getWorkloadEventHistory, type WorkloadEventRow } from '../services/api'

const fmt = (t?: string) => (t ? t.slice(0, 19).replace('T', ' ') : '-')

export default function WorkloadEventsModal({ target, onClose }: {
  target: { clusterId: number; kind: string; namespace: string; name: string } | null
  onClose: () => void
}) {
  const [rows, setRows] = useState<WorkloadEventRow[]>([])
  const [hours, setHours] = useState(168)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!target) return
    setLoading(true)
    getWorkloadEventHistory(target.clusterId, target.kind, target.namespace, target.name, hours)
      .then((d) => setRows(d?.events || []))
      .catch(() => message.error('历史事件获取失败'))
      .finally(() => setLoading(false))
  }, [target, hours])

  return (
    <Modal
      title={`历史事件：${target?.namespace || ''}/${target?.name || ''}`}
      open={!!target}
      onCancel={onClose}
      footer={null}
      width={1080}
      destroyOnClose
    >
      <Space style={{ marginBottom: 10 }}>
        <span style={{ fontSize: 12, color: '#8c8c8c' }}>时间范围</span>
        <Select
          size="small"
          value={hours}
          style={{ width: 130 }}
          onChange={setHours}
          options={[
            { value: 1, label: '最近 1 小时' }, { value: 24, label: '最近 24 小时' },
            { value: 72, label: '最近 3 天' }, { value: 168, label: '最近 7 天' },
          ]}
        />
        <span style={{ fontSize: 12, color: '#8c8c8c' }}>
          含该工作负载自身及其所有 Pod（含已重建的旧 Pod）的事件
        </span>
      </Space>
      <Table
        rowKey={(_, i) => String(i)}
        size="small"
        loading={loading}
        dataSource={rows}
        pagination={{ pageSize: 10, hideOnSinglePage: true }}
        columns={[
          { title: '类型', dataIndex: 'type', width: 90, render: (v: string) => <Tag color={v === 'Warning' ? 'red' : 'blue'}>{v}</Tag> },
          { title: '资源', width: 300, render: (_: unknown, r: WorkloadEventRow) => (
            <span style={{ fontSize: 12 }}>{r.resource_kind} / {r.resource_name}</span>
          ) },
          { title: '原因', dataIndex: 'reason', width: 150 },
          { title: '消息', dataIndex: 'message', render: (v: string) => <span style={{ whiteSpace: 'pre-wrap' }}>{v}</span> },
          { title: '次数', dataIndex: 'count', width: 80 },
          { title: '最后发生', dataIndex: 'last_timestamp', width: 165, render: fmt },
        ]}
        locale={{ emptyText: <Empty description="该时间段没有事件" /> }}
      />
    </Modal>
  )
}
