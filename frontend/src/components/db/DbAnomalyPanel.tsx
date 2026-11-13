// 数据库异常面板（详情页 Tab）：展示采集到的异常点（级别/指标/当前值/阈值/说明/建议）
import { useEffect, useState } from 'react'
import { Card, Table, Tag, Empty, message } from 'antd'
import { getDBAnomalies } from '../../services/api'

// 异常点结构（后端返回，字段可选容错）
interface Anomaly {
  level: string
  metric: string
  metric_name: string
  current: number | string
  threshold: number | string
  message: string
  suggestion: string
}

const LEVEL_LABEL: Record<string, string> = { high: '高', medium: '中', low: '低' }
const LEVEL_COLOR: Record<string, string> = { high: 'red', medium: 'orange', low: 'blue' }

export default function DbAnomalyPanel({ connId }: { connId: number }) {
  const [rows, setRows] = useState<Anomaly[]>([])
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!connId) return
    setLoading(true)
    getDBAnomalies(connId)
      .then((d) => setRows((d || []) as Anomaly[]))
      .catch(() => { setRows([]); message.error('异常查询失败') })
      .finally(() => setLoading(false))
  }, [connId])

  const columns = [
    { title: '级别', dataIndex: 'level', width: 80,
      render: (v: string) => <Tag color={LEVEL_COLOR[v] || 'default'}>{LEVEL_LABEL[v] || v || '-'}</Tag> },
    { title: '指标', dataIndex: 'metric_name', width: 200, render: (v: string) => v || '-' },
    { title: '当前值', dataIndex: 'current', width: 120, render: (v: number | string) => <b style={{ color: '#cf1322' }}>{v ?? '-'}</b> },
    { title: '阈值', dataIndex: 'threshold', width: 120, render: (v: number | string) => (v === '' || v === null || v === undefined ? '-' : String(v)) },
    { title: '说明', dataIndex: 'message', ellipsis: true, render: (v: string) => v || '-' },
    { title: '建议', dataIndex: 'suggestion', ellipsis: true, render: (v: string) => v || '-' },
  ]

  return (
    <Card size="small" title={`异常点（${rows.length}）`}>
      {rows.length
        ? <Table rowKey={(r: Anomaly, i?: number) => `${r.metric_name}-${i}`} size="small" dataSource={rows}
            columns={columns} loading={loading} pagination={false} scroll={{ x: 'max-content' }} />
        : (loading ? <Table rowKey="metric_name" size="small" dataSource={[]} columns={columns} loading pagination={false} />
           : <Empty description="暂无异常" />)}
    </Card>
  )
}
