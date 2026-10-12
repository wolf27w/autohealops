// 统一监控大屏（P1-R7）：多集群健康总览（状态/节点数/最近检查/不可达原因）
import { useEffect, useState } from 'react'
import { Card, Row, Col, Tag, Statistic, Empty } from 'antd'
import { getClusters, getNodes } from '../services/api'
import type { Cluster } from '../types'

export default function MonitorScreen() {
  const [clusters, setClusters] = useState<Cluster[]>([])
  const [nodeCounts, setNodeCounts] = useState<Record<number, number>>({})
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    getClusters().then((d) => {
      const cs = d || []
      setClusters(cs)
      cs.forEach((c) => {
        getNodes(c.id).then((ns) => setNodeCounts((p) => ({ ...p, [c.id]: (ns || []).length }))).catch(() => {})
      })
    }).catch(() => {}).finally(() => setLoading(false))
  }, [])

  const healthy = clusters.filter((c) => c.status === 'healthy').length
  const unreachable = clusters.filter((c) => c.status === 'unreachable').length

  return (
    <div style={{ padding: 16 }}>
      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col span={8}><Card size="small"><Statistic title="集群总数" value={clusters.length} loading={loading} /></Card></Col>
        <Col span={8}><Card size="small"><Statistic title="健康集群" value={healthy} valueStyle={{ color: '#389e0d' }} /></Card></Col>
        <Col span={8}><Card size="small"><Statistic title="不可达" value={unreachable} valueStyle={{ color: '#cf1322' }} /></Card></Col>
      </Row>
      <Row gutter={[16, 16]}>
        {clusters.map((c) => (
          <Col key={c.id} xs={24} md={12} xl={8}>
            <Card size="small" title={c.name}
              extra={<Tag color={c.status === 'healthy' ? 'green' : c.status === 'unreachable' ? 'red' : 'default'}>{c.status}</Tag>}>
              <div>节点数：{nodeCounts[c.id] ?? '-'}</div>
              <div style={{ fontSize: 12, color: '#999' }}>最近检查：{c.last_check_at ? String(c.last_check_at).slice(0, 19) : '-'}</div>
              {c.last_check_error ? <div style={{ color: '#cf1322', fontSize: 12 }}>⚠ {c.last_check_error}</div> : null}
            </Card>
          </Col>
        ))}
        {clusters.length === 0 && <Col span={24}><Empty /></Col>}
      </Row>
    </div>
  )
}
