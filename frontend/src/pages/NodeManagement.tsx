// 节点管理（P1-R4）：节点列表 + cordon/uncordon
import { useEffect, useState } from 'react'
import { Card, Table, Button, Space, Select, Tag, message, Popconfirm } from 'antd'
import { ReloadOutlined } from '@ant-design/icons'
import { getClusters, getNodes, cordonNode, uncordonNode } from '../services/api'
import type { K8sNode } from '../services/api'
import type { Cluster } from '../types'

export default function NodeManagement() {
  const [clusters, setClusters] = useState<Cluster[]>([])
  const [clusterId, setClusterId] = useState<number | undefined>(undefined)
  const [nodes, setNodes] = useState<K8sNode[]>([])
  const [loading, setLoading] = useState(false)

  useEffect(() => { getClusters().then((d) => setClusters(d || [])).catch(() => {}) }, [])

  const load = () => {
    if (!clusterId) { setNodes([]); return }
    setLoading(true)
    getNodes(clusterId).then((d) => setNodes(d || [])).catch(() => {}).finally(() => setLoading(false))
  }
  useEffect(() => { load() }, [clusterId]) // eslint-disable-line react-hooks/exhaustive-deps

  const act = async (node: string, op: 'cordon' | 'uncordon') => {
    if (!clusterId) return
    if (op === 'cordon') { await cordonNode(clusterId, node); message.success(`已 cordon ${node}`) }
    else { await uncordonNode(clusterId, node); message.success(`已 uncordon ${node}`) }
    load()
  }

  return (
    <div style={{ padding: 16 }}>
      <Card size="small" title="节点管理"
        extra={<Space>
          <Select allowClear placeholder="选择集群" style={{ width: 200 }} value={clusterId}
            onChange={setClusterId} options={clusters.map((c) => ({ value: c.id, label: c.name }))} />
          <Button icon={<ReloadOutlined />} onClick={load}>刷新</Button>
        </Space>}>
        <Table rowKey="name" size="small" loading={loading} dataSource={nodes} pagination={false}
          columns={[
            { title: '节点', dataIndex: 'name' },
            { title: '状态', dataIndex: 'ready', width: 110, render: (v) => <Tag color={v === 'Ready' ? 'green' : 'red'}>{v}</Tag> },
            { title: '可调度', dataIndex: 'schedulable', width: 120, render: (v) => v ? <Tag color="green">是</Tag> : <Tag color="orange">否（已 cordon）</Tag> },
            { title: '角色', dataIndex: 'roles', width: 200, render: (v) => v || '-' },
            {
              title: '操作', width: 150, render: (_, r) => (
                <Space>
                  {r.schedulable
                    ? <Popconfirm title={`cordon ${r.name}？（禁止调度新 Pod）`} onConfirm={() => act(r.name, 'cordon')}><a>cordon</a></Popconfirm>
                    : <Popconfirm title={`uncordon ${r.name}？（恢复调度）`} onConfirm={() => act(r.name, 'uncordon')}><a style={{ color: '#389e0d' }}>uncordon</a></Popconfirm>}
                </Space>
              )
            },
          ]} />
      </Card>
    </div>
  )
}
