// Pod 详情抽屉：基本信息 + 容器组内容（各容器：镜像/状态/就绪/重启/启动时间）
import { useEffect, useState } from 'react'
import { Drawer, Descriptions, Table, Tag, Space, Empty, Spin, Divider, message } from 'antd'
import { getPodDetail } from '../services/api'
import type { PodTarget } from '../types'

interface ContainerDetail {
  name: string
  image: string
  state: string        // running/waiting/terminated
  ready: boolean
  restart_count: number
  reason: string
  started_at: string
  finished_at: string
  is_init: boolean
}

interface PodDetailData {
  name: string
  namespace: string
  phase: string
  ready: boolean
  restart_count: number
  created_at: string
  node_name: string
  pod_ip: string
  host_ip: string
  containers: ContainerDetail[]
  init_containers: ContainerDetail[]
}

const fmt = (t?: string) => (t ? t.slice(0, 19).replace('T', ' ') : '-')
const stateColor = (s: string) => (s === 'running' ? 'green' : s === 'terminated' ? 'default' : 'orange')

function containerColumns() {
  return [
    { title: '容器', dataIndex: 'name', width: 200, render: (v: string, r: ContainerDetail) => (
      <Space size={4}>{r.is_init && <Tag color="purple">init</Tag>}<span style={{ fontWeight: 600 }}>{v}</span></Space>
    ) },
    { title: '镜像', dataIndex: 'image', ellipsis: true },
    { title: '状态', dataIndex: 'state', width: 130, render: (v: string, r: ContainerDetail) => (
      <Space size={4}><Tag color={stateColor(v)}>{v || '-'}</Tag>{r.reason && <span style={{ color: '#d4380d', fontSize: 12 }}>{r.reason}</span>}</Space>
    ) },
    { title: '就绪', dataIndex: 'ready', width: 70, render: (v: boolean) => (v ? <Tag color="green">是</Tag> : <Tag>否</Tag>) },
    { title: '重启', dataIndex: 'restart_count', width: 70 },
    { title: '启动时间', dataIndex: 'started_at', width: 165, render: fmt },
  ]
}

export default function PodDetailDrawer({ pod, onClose }: { pod: PodTarget | null; onClose: () => void }) {
  const [data, setData] = useState<PodDetailData | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!pod) { setData(null); return }
    setLoading(true)
    getPodDetail(pod.clusterId, pod.namespace, pod.name)
      .then((d) => setData(d as PodDetailData))
      .catch(() => message.error('Pod 详情获取失败'))
      .finally(() => setLoading(false))
  }, [pod])

  return (
    <Drawer title={`Pod 详情：${pod?.name || ''}`} open={!!pod} onClose={onClose} width={900}>
      {loading ? (
        <div style={{ textAlign: 'center', paddingTop: 60 }}><Spin /></div>
      ) : !data ? (
        <Empty description="无数据" />
      ) : (
        <>
          <Descriptions size="small" column={3} bordered>
            <Descriptions.Item label="命名空间">{data.namespace}</Descriptions.Item>
            <Descriptions.Item label="状态">
              <Space size={4}>
                <Tag color={data.phase === 'Running' ? 'green' : 'red'}>{data.phase}</Tag>
                {data.ready ? <Tag color="green">就绪</Tag> : <Tag color="orange">未就绪</Tag>}
              </Space>
            </Descriptions.Item>
            <Descriptions.Item label="节点">{data.node_name || '-'}</Descriptions.Item>
            <Descriptions.Item label="Pod IP">{data.pod_ip || '-'}</Descriptions.Item>
            <Descriptions.Item label="宿主 IP">{data.host_ip || '-'}</Descriptions.Item>
            <Descriptions.Item label="重启次数">{data.restart_count}</Descriptions.Item>
            <Descriptions.Item label="创建时间" span={3}>{fmt(data.created_at)}</Descriptions.Item>
          </Descriptions>

          <Divider orientation="left">容器组（{(data.containers || []).length} 个）</Divider>
          <Table rowKey="name" size="small" pagination={false} dataSource={data.containers}
            columns={containerColumns()} locale={{ emptyText: '无容器' }} />

          {(data.init_containers || []).length > 0 && (
            <>
              <Divider orientation="left">Init 容器（{data.init_containers.length} 个）</Divider>
              <Table rowKey="name" size="small" pagination={false} dataSource={data.init_containers}
                columns={containerColumns()} />
            </>
          )}
        </>
      )}
    </Drawer>
  )
}
