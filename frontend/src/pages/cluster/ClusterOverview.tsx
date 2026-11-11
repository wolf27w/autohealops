// 集群概览（R32-P1）：进集群后的默认页。只做汇总，不重复各子页的列表。
import { useEffect, useMemo, useState } from 'react'
import { Card, Col, Row, Statistic, Table, Tag, Typography, Progress, Empty } from 'antd'
import { getCluster, getClusterDetail } from '../../services/api'

interface NSRow {
  namespace: string
  deployments?: unknown[]
  statefulsets?: unknown[]
  daemonsets?: unknown[]
  cronjobs?: unknown[]
  pods?: unknown[]
}

export default function ClusterOverview({ clusterId }: { clusterId: number }) {
  const [namespaces, setNamespaces] = useState<NSRow[]>([])
  const [cluster, setCluster] = useState<{ name?: string; env_tag?: string; version?: string; status?: string } | null>(null)

  useEffect(() => {
    getCluster(clusterId).then((c) => setCluster(c as never)).catch(() => setCluster(null))
    getClusterDetail(clusterId).then((d) => setNamespaces((d || []) as NSRow[])).catch(() => setNamespaces([]))
  }, [clusterId])

  const sum = useMemo(() => {
    const acc = { deployments: 0, statefulsets: 0, daemonsets: 0, cronjobs: 0, pods: 0 }
    for (const ns of namespaces) {
      acc.deployments += (ns.deployments || []).length
      acc.statefulsets += (ns.statefulsets || []).length
      acc.daemonsets += (ns.daemonsets || []).length
      acc.cronjobs += (ns.cronjobs || []).length
      acc.pods += (ns.pods || []).length
    }
    return acc
  }, [namespaces])

  return (
    <div>
      <Typography.Title level={4} style={{ marginTop: 0 }}>
        {cluster?.name || `集群 ${clusterId}`}
        {cluster?.env_tag && <Tag color="blue" style={{ marginLeft: 10 }}>{cluster.env_tag}</Tag>}
        {cluster?.version && <Typography.Text type="secondary" style={{ marginLeft: 8, fontSize: 12 }}>{cluster.version}</Typography.Text>}
      </Typography.Title>

      <Row gutter={12} style={{ marginBottom: 12 }}>
        <Col flex="1"><Card size="small"><Statistic title="命名空间" value={namespaces.length} /></Card></Col>
        <Col flex="1"><Card size="small"><Statistic title="部署" value={sum.deployments} /></Card></Col>
        <Col flex="1"><Card size="small"><Statistic title="有状态副本集" value={sum.statefulsets} /></Card></Col>
        <Col flex="1"><Card size="small"><Statistic title="守护进程集" value={sum.daemonsets} /></Card></Col>
        <Col flex="1"><Card size="small"><Statistic title="定时任务" value={sum.cronjobs} /></Card></Col>
        <Col flex="1"><Card size="small"><Statistic title="容器组" value={sum.pods} /></Card></Col>
      </Row>

      <Card size="small" title="命名空间工作负载分布">
        {namespaces.length === 0 ? <Empty description="暂无数据（可能采集未完成）" /> : (
          <Table
            rowKey="namespace"
            size="small"
            pagination={false}
            dataSource={namespaces}
            columns={[
              { title: '命名空间', dataIndex: 'namespace' },
              { title: '部署', render: (_, r: NSRow) => (r.deployments || []).length },
              { title: '有状态', render: (_, r: NSRow) => (r.statefulsets || []).length },
              { title: '守护', render: (_, r: NSRow) => (r.daemonsets || []).length },
              { title: '定时', render: (_, r: NSRow) => (r.cronjobs || []).length },
              { title: '容器组', render: (_, r: NSRow) => (r.pods || []).length },
              {
                title: '负载占比', width: 160,
                render: (_, r: NSRow) => {
                  const n = (r.deployments || []).length + (r.statefulsets || []).length + (r.daemonsets || []).length
                  const pct = sum.deployments + sum.statefulsets + sum.daemonsets
                  return <Progress percent={pct ? Math.round((n / pct) * 100) : 0} size="small" showInfo={false} />
                },
              },
            ]}
          />
        )}
      </Card>
    </div>
  )
}
