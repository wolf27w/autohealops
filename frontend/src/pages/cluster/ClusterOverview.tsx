// 集群概览（R32-P1）：参照 KubeSphere 集群「概览」——集群信息 + 资源使用率 + 节点状态 + 资源统计。
// 数据源：getCluster / getNodes / getResourceOverview / getClusterDetail / getMiscResources（均已存在，不新增接口）。
import { useEffect, useMemo, useState } from 'react'
import { Card, Col, Row, Statistic, Tag, Typography, Progress, Spin } from 'antd'
import { useNavigate } from 'react-router-dom'
import { getCluster, getNodes, getResourceOverview, getClusterDetail, getMiscResources, type K8sNode } from '../../services/api'

interface NSRow {
  namespace: string
  deployments?: unknown[]
  statefulsets?: unknown[]
  daemonsets?: unknown[]
  cronjobs?: unknown[]
  pods?: unknown[]
}

function parseCores(s?: string): number | null {
  if (!s) return null
  const t = String(s).trim()
  if (t.endsWith('m')) return Number(t.slice(0, -1)) / 1000
  const n = Number(t)
  return Number.isFinite(n) ? n : null
}

function parseBytes(s?: string): number | null {
  if (!s) return null
  const t = String(s).trim().toUpperCase()
  const m = t.match(/^([\d.]+)\s*(MI|GI|TI|M|G|T|KI|K)?/i)
  if (!m) return null
  const v = Number(m[1])
  const unit = (m[2] || '').toUpperCase()
  const mul: Record<string, number> = { '': 1, K: 1024, KI: 1024, M: 1024 ** 2, MI: 1024 ** 2, G: 1024 ** 3, GI: 1024 ** 3, T: 1024 ** 4, TI: 1024 ** 4 }
  return Math.round(v * (mul[unit] ?? 1))
}

function fmtBytes(b: number | null): string {
  if (b == null || Number.isNaN(b)) return '-'
  if (b >= 1024 ** 4) return (b / 1024 ** 4).toFixed(1) + ' Ti'
  if (b >= 1024 ** 3) return (b / 1024 ** 3).toFixed(1) + ' Gi'
  if (b >= 1024 ** 2) return (b / 1024 ** 2).toFixed(1) + ' Mi'
  return b + ' B'
}

export default function ClusterOverview({ clusterId }: { clusterId: number }) {
  const navigate = useNavigate()
  const [cluster, setCluster] = useState<{ name?: string; env_tag?: string; version?: string; status?: string } | null>(null)
  const [nodes, setNodes] = useState<K8sNode[]>([])
  const [overview, setOverview] = useState<{ nodes?: number; ready_nodes?: number; cpu_limit?: string; memory_limit?: string } | null>(null)
  const [namespaces, setNamespaces] = useState<NSRow[]>([])
  const [misc, setMisc] = useState<{ services?: unknown[]; ingresses?: unknown[]; configmaps?: unknown[]; secrets?: unknown[]; pvcs?: unknown[] }>({})
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let alive = true
    setLoading(true)
    // 只等 3 个快接口，页面立即渲染；节点列表通常 0.3s，生产集群可能 5~9s（要 list 全集群 Pod），
    // 不能让它把整页卡住，所以单独异步补。
    Promise.allSettled([
      getCluster(clusterId),
      getResourceOverview(clusterId),
      getClusterDetail(clusterId),
    ]).then(([c, o, d]) => {
      if (!alive) return
      if (c.status === 'fulfilled') setCluster(c.value as never)
      if (o.status === 'fulfilled') setOverview(o.value as never)
      if (d.status === 'fulfilled') setNamespaces((d.value || []) as NSRow[])
    }).finally(() => { if (alive) setLoading(false) })

    // 节点（含用量 / Pod 数）：独立加载，慢也不挡首屏
    getNodes(clusterId)
      .then((v) => { if (alive) setNodes((v || []) as K8sNode[]) })
      .catch(() => {})

    // 杂项资源（服务/路由/CM/Secret/PVC 计数）单独异步：它要全命名空间 list，慢的时候会拖住整页
    getMiscResources(clusterId)
      .then((m) => { if (alive) setMisc((m || {}) as never) })
      .catch(() => { /* 计数拿不到不影响主视图 */ })

    // 节点/快照 10s 刷新
    // 页面不可见时跳过轮询（避免后台标签页持续打远端集群）
    const t = setInterval(() => {
      if (document.hidden) return
      getNodes(clusterId).then((v) => setNodes((v || []) as K8sNode[])).catch(() => {})
    }, 10000)
    return () => { alive = false; clearInterval(t) }
  }, [clusterId])

  const readyNodes = useMemo(() => nodes.filter((n) => n.ready === 'Ready').length, [nodes])
  const cpuTotal = parseCores(overview?.cpu_limit)
  const memTotal = parseBytes(overview?.memory_limit)
  const cpuUsed = useMemo(() => {
    const v = nodes.reduce((a, n) => a + (n.cpu_usage_milli || 0), 0)
    return v ? v / 1000 : null
  }, [nodes])
  const memUsed = useMemo(() => {
    const v = nodes.reduce((a, n) => a + (n.mem_usage_bytes || 0), 0)
    return v || null
  }, [nodes])
  const cpuPct = cpuTotal && cpuUsed != null ? Math.round((cpuUsed / cpuTotal) * 100) : null
  const memPct = memTotal && memUsed != null ? Math.round((memUsed / memTotal) * 100) : null

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

  const stats = [
    { label: '节点', value: nodes.length, path: 'nodes' },
    { label: '命名空间', value: namespaces.length, path: '' },
    { label: '部署', value: sum.deployments, path: 'workloads/deployment' },
    { label: '有状态副本集', value: sum.statefulsets, path: 'workloads/statefulset' },
    { label: '守护进程集', value: sum.daemonsets, path: 'workloads/daemonset' },
    { label: '定时任务', value: sum.cronjobs, path: 'workloads/cronjob' },
    { label: '服务', value: (misc.services || []).length, path: 'services' },
    { label: '应用路由', value: (misc.ingresses || []).length, path: 'services' },
    { label: '容器组', value: sum.pods, path: 'pods' },
    { label: 'PVC', value: (misc.pvcs || []).length, path: 'storage' },
    { label: 'ConfigMap', value: (misc.configmaps || []).length, path: 'configs' },
    { label: 'Secret', value: (misc.secrets || []).length, path: 'configs' },
  ]

  if (loading) return <Spin style={{ display: 'block', marginTop: 80 }} />

  return (
    <div>
      <Typography.Title level={4} style={{ marginTop: 0, marginBottom: 4 }}>
        {cluster?.name || `集群 ${clusterId}`}
        {cluster?.env_tag && <Tag color="blue" style={{ marginLeft: 10 }}>{cluster.env_tag}</Tag>}
        {cluster?.status && <Tag color={cluster.status === 'healthy' ? 'green' : 'default'} style={{ marginLeft: 6 }}>{cluster.status}</Tag>}
      </Typography.Title>
      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        Kubernetes {cluster?.version || '-'}
      </Typography.Text>

      {/* 资源使用率（KubeSphere 风格三卡） */}
      <Row gutter={12} style={{ marginTop: 16 }}>
        <Col flex="1">
          <Card size="small">
            <Statistic title="节点" value={readyNodes} suffix={`/ ${nodes.length}`} />
            <Progress percent={nodes.length ? Math.round((readyNodes / nodes.length) * 100) : 0} showInfo={false} />
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>就绪 / 总数</Typography.Text>
          </Card>
        </Col>
        <Col flex="1">
          <Card size="small">
            <Statistic title="CPU" value={cpuPct ?? '-'} suffix={cpuPct != null ? '%' : ''} />
            <Progress percent={cpuPct ?? 0} showInfo={false} status={cpuPct != null && cpuPct > 85 ? 'exception' : 'normal'} />
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {cpuUsed != null ? `${cpuUsed.toFixed(1)} 核` : '-'} / {cpuTotal != null ? `${cpuTotal} 核` : '-'}
            </Typography.Text>
          </Card>
        </Col>
        <Col flex="1">
          <Card size="small">
            <Statistic title="内存" value={memPct ?? '-'} suffix={memPct != null ? '%' : ''} />
            <Progress percent={memPct ?? 0} showInfo={false} status={memPct != null && memPct > 85 ? 'exception' : 'normal'} />
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {fmtBytes(memUsed)} / {fmtBytes(memTotal)}
            </Typography.Text>
          </Card>
        </Col>
        <Col flex="1">
          <Card size="small">
            <Statistic title="容器组" value={sum.pods} />
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>全部命名空间</Typography.Text>
          </Card>
        </Col>
      </Row>

      {/* 集群资源总量（可分配量） */}
      <Card size="small" title="集群资源总量（可分配）" style={{ marginTop: 12 }}>
        <Row gutter={12}>
          <Col span={8}>
            <Statistic title="CPU" value={cpuTotal != null ? `${cpuTotal} 核` : '-'} />
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              已用 {cpuUsed != null ? cpuUsed.toFixed(1) : '-'} 核
            </Typography.Text>
          </Col>
          <Col span={8}>
            <Statistic title="内存" value={memTotal != null ? fmtBytes(memTotal) : '-'} />
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              已用 {fmtBytes(memUsed)}
            </Typography.Text>
          </Col>
          <Col span={8}>
            <Statistic title="Pod" value={overview?.nodes ?? '-'} />
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              已用 {overview?.ready_nodes ?? '-'}
            </Typography.Text>
          </Col>
        </Row>
      </Card>

      {/* 资源统计 */}
      <Card size="small" title="资源统计" style={{ marginTop: 12 }}>
        <Row gutter={[8, 12]}>
          {stats.map((s) => (
            <Col key={s.label} span={4}>
              <a
                style={{ display: 'block', padding: '10px 12px', border: '1px solid #f0f0f0', borderRadius: 6, textAlign: 'center' }}
                onClick={() => s.path && navigate(`/clusters/${clusterId}/${s.path}`)}
              >
                <div style={{ fontSize: 22, fontWeight: 600 }}>{s.value}</div>
                <div style={{ fontSize: 12, color: '#8c8c8c' }}>{s.label}</div>
              </a>
            </Col>
          ))}
        </Row>
      </Card>

    </div>
  )
}
