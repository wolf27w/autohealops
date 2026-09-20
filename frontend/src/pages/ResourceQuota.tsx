// 资源配额页
import { useEffect, useState } from 'react'
import { Card, Select, Row, Col, Statistic, Table, Progress, Button, message } from 'antd'
import { getClusters, getResourceOverview, getNamespaces, triggerCollect } from '../services/api'
import type { Cluster } from '../types'

interface ResourceOverview {
  nodes?: number
  ready_nodes?: number
  cpu_limit?: string | number
}

interface NamespaceQuota {
  namespace: string
  cpu_used: string
  cpu_limit: string
  memory_used: string
  memory_limit: string
}

export default function ResourceQuota() {
  const [clusters, setClusters] = useState<Cluster[]>([])
  const [clusterId, setClusterId] = useState<number>()
  const [overview, setOverview] = useState<ResourceOverview>({})
  const [namespaces, setNamespaces] = useState<NamespaceQuota[]>([])

  useEffect(() => {
    getClusters().then((d) => setClusters(d))
  }, [])

  useEffect(() => {
    if (!clusterId) return
    getResourceOverview(clusterId).then((d) => setOverview(d as ResourceOverview))
    getNamespaces(clusterId).then((d) => setNamespaces(d as NamespaceQuota[]))
  }, [clusterId])

  const parsePercent = (used: string, limit: string) => {
    const u = parseInt(used) || 0
    const l = parseInt(limit) || 1
    return Math.min(100, Math.round((u / l) * 100))
  }

  return (
    <div>
      <Select placeholder="选择集群" style={{ width: 240, marginBottom: 16 }} onChange={setClusterId}
        options={clusters.map((c) => ({ value: c.id, label: c.name }))} />
      {clusterId && (
        <>
          <Row gutter={16}>
            <Col span={8}><Card><Statistic title="节点数" value={overview.nodes || 0} /></Card></Col>
            <Col span={8}><Card><Statistic title="就绪节点" value={overview.ready_nodes || 0} /></Card></Col>
            <Col span={8}><Card><Statistic title="CPU 总量" value={overview.cpu_limit || '0'} /></Card></Col>
          </Row>
          <Button style={{ marginTop: 16 }} onClick={() => triggerCollect(clusterId).then(() => message.success('采集已触发'))}>触发采集</Button>
          <Card title="命名空间配额" style={{ marginTop: 16 }}>
            <Table rowKey="namespace" dataSource={namespaces} pagination={false}
              columns={[
                { title: '命名空间', dataIndex: 'namespace' },
                { title: 'CPU', render: (_, r) => <Progress percent={parsePercent(r.cpu_used, r.cpu_limit)} size="small" /> },
                { title: '内存', render: (_, r) => <Progress percent={parsePercent(r.memory_used, r.memory_limit)} size="small" /> },
              ]} />
          </Card>
        </>
      )}
    </div>
  )
}
