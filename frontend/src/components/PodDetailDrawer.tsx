// Pod 详情抽屉：基本信息 + 容器组（镜像/状态/就绪/重启/挂载）+ 数据卷 + 所属工作负载(Deployment/StatefulSet)挂载配置
import { useEffect, useState } from 'react'
import { Drawer, Descriptions, Table, Tag, Space, Empty, Spin, Divider, Typography, Collapse, message } from 'antd'
import { getPodDetail, getWorkloadMounts } from '../services/api'
import type { PodTarget } from '../types'

interface VolumeMountInfo {
  name: string
  mount_path: string
  sub_path?: string
  read_only?: boolean
  sub_path_expr?: string
  propagation?: string
}

interface VolumeInfo {
  name: string
  type: string
  source: string
  read_only?: boolean
}

interface ContainerMounts {
  name: string
  mounts: VolumeMountInfo[]
}

interface WorkloadMounts {
  volumes: VolumeInfo[]
  containers: ContainerMounts[]
  init_containers: ContainerMounts[]
}

interface ContainerDetail {
  name: string
  image: string
  state: string // running/waiting/terminated
  ready: boolean
  restart_count: number
  reason: string
  started_at: string
  finished_at: string
  is_init: boolean
  mounts?: VolumeMountInfo[]
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
  volumes?: VolumeInfo[]
  owner_kind?: string
  owner_name?: string
  owner_namespace?: string
}

const fmt = (t?: string) => (t ? t.slice(0, 19).replace('T', ' ') : '-')
const stateColor = (s: string) => (s === 'running' ? 'green' : s === 'terminated' ? 'default' : 'orange')

// owner Kind → 后端内部 kind（仅支持的三种工作负载）
const ownerKindToInternal = (k?: string): string => {
  switch (k) {
    case 'Deployment': return 'deployment'
    case 'StatefulSet': return 'statefulset'
    case 'DaemonSet': return 'daemonset'
    default: return ''
  }
}

// 容器挂载表（卷名 / 挂载路径 / 子路径 / 只读 / 传播）
function MountTable({ mounts }: { mounts?: VolumeMountInfo[] }) {
  if (!mounts || mounts.length === 0) return <Typography.Text type="secondary">无挂载</Typography.Text>
  return (
    <Table rowKey={(r) => `${r.name}-${r.mount_path}`} size="small" pagination={false} dataSource={mounts}
      columns={[
        { title: '卷名', dataIndex: 'name', width: 180 },
        { title: '挂载路径', dataIndex: 'mount_path' },
        { title: '子路径', dataIndex: 'sub_path', width: 160, render: (v: string) => v || '-' },
        { title: '只读', dataIndex: 'read_only', width: 70, render: (v: boolean) => (v ? <Tag color="orange">只读</Tag> : '-') },
        { title: '传播', dataIndex: 'propagation', width: 130, render: (v: string) => v || '-' },
      ]} />
  )
}

// 数据卷表（卷名 / 类型 / 来源 / 只读）
function VolumeTable({ volumes }: { volumes?: VolumeInfo[] }) {
  if (!volumes || volumes.length === 0) return <Typography.Text type="secondary">无数据卷</Typography.Text>
  return (
    <Table rowKey="name" size="small" pagination={false} dataSource={volumes}
      columns={[
        { title: '卷名', dataIndex: 'name', width: 200 },
        { title: '类型', dataIndex: 'type', width: 180, render: (v: string) => (v ? <Tag>{v}</Tag> : '-') },
        { title: '来源', dataIndex: 'source', render: (v: string) => v || '-' },
        { title: '只读', dataIndex: 'read_only', width: 70, render: (v: boolean) => (v ? <Tag color="orange">只读</Tag> : '-') },
      ]} />
  )
}

// 容器挂载列表（每个容器一块：容器名 + 其挂载表）
function ContainerMountsList({ list }: { list?: ContainerMounts[] }) {
  const items = (list || []).filter((c) => (c.mounts || []).length > 0)
  if (items.length === 0) return <Typography.Text type="secondary">无容器挂载</Typography.Text>
  return (
    <Collapse
      size="small"
      defaultActiveKey={items.map((c) => c.name)}
      items={items.map((c) => ({
        key: c.name,
        label: <span style={{ fontWeight: 600 }}>{c.name}</span>,
        children: <MountTable mounts={c.mounts} />,
      }))}
    />
  )
}

function containerColumns() {
  return [
    { title: '容器', dataIndex: 'name', width: 180, render: (v: string, r: ContainerDetail) => (
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
  const [wl, setWl] = useState<WorkloadMounts | null>(null)
  const [wlLoading, setWlLoading] = useState(false)

  useEffect(() => {
    if (!pod) { setData(null); setWl(null); return }
    setLoading(true)
    getPodDetail(pod.clusterId, pod.namespace, pod.name)
      .then((d) => setData(d as PodDetailData))
      .catch(() => message.error('Pod 详情获取失败'))
      .finally(() => setLoading(false))
  }, [pod])

  // 所属工作负载（Deployment/StatefulSet/DaemonSet）的挂载配置
  useEffect(() => {
    const kind = ownerKindToInternal(data?.owner_kind)
    if (!pod || !kind || !data?.owner_name) { setWl(null); return }
    setWlLoading(true)
    getWorkloadMounts(pod.clusterId, kind, data.owner_namespace || pod.namespace, data.owner_name)
      .then((d) => setWl(d as WorkloadMounts))
      .catch(() => setWl(null))
      .finally(() => setWlLoading(false))
  }, [pod, data?.owner_kind, data?.owner_name, data?.owner_namespace]) // eslint-disable-line react-hooks/exhaustive-deps

  const ownerKind = ownerKindToInternal(data?.owner_kind)

  return (
    <Drawer title={`Pod 详情：${pod?.name || ''}`} open={!!pod} onClose={onClose} width={980}>
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
            <Descriptions.Item label="所属工作负载">
              {ownerKind ? `${data.owner_kind} / ${data.owner_name}` : '-'}
            </Descriptions.Item>
            <Descriptions.Item label="节点">{data.node_name || '-'}</Descriptions.Item>
            <Descriptions.Item label="Pod IP">{data.pod_ip || '-'}</Descriptions.Item>
            <Descriptions.Item label="宿主 IP">{data.host_ip || '-'}</Descriptions.Item>
            <Descriptions.Item label="重启次数">{data.restart_count}</Descriptions.Item>
            <Descriptions.Item label="创建时间" span={2}>{fmt(data.created_at)}</Descriptions.Item>
          </Descriptions>

          <Divider orientation="left">容器组（{(data.containers || []).length} 个）</Divider>
          <Table rowKey="name" size="small" pagination={false} dataSource={data.containers}
            columns={containerColumns()} locale={{ emptyText: '无容器' }}
            expandable={{
              rowExpandable: (r) => (r.mounts || []).length > 0,
              expandedRowRender: (r) => <MountTable mounts={r.mounts} />,
            }} />

          {(data.init_containers || []).length > 0 && (
            <>
              <Divider orientation="left">Init 容器（{data.init_containers.length} 个）</Divider>
              <Table rowKey="name" size="small" pagination={false} dataSource={data.init_containers}
                columns={containerColumns()}
                expandable={{
                  rowExpandable: (r) => (r.mounts || []).length > 0,
                  expandedRowRender: (r) => <MountTable mounts={r.mounts} />,
                }} />
            </>
          )}

          <Divider orientation="left">数据卷（{(data.volumes || []).length} 个）</Divider>
          <VolumeTable volumes={data.volumes} />

          {ownerKind && (
            <>
              <Divider orientation="left">
                工作负载挂载配置{data.owner_kind ? `（${data.owner_kind} / ${data.owner_name}）` : ''}
              </Divider>
              {wlLoading ? (
                <div style={{ textAlign: 'center', padding: '20px 0' }}><Spin /></div>
              ) : !wl ? (
                <Typography.Text type="secondary">挂载配置获取失败或不可用</Typography.Text>
              ) : (
                <>
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>数据卷（volumes）</Typography.Text>
                  <div style={{ margin: '6px 0 14px' }}><VolumeTable volumes={wl.volumes} /></div>
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>容器挂载（volumeMounts）</Typography.Text>
                  <div style={{ marginTop: 6 }}><ContainerMountsList list={wl.containers} /></div>
                  {(wl.init_containers || []).some((c) => (c.mounts || []).length > 0) && (
                    <div style={{ marginTop: 10 }}>
                      <Typography.Text type="secondary" style={{ fontSize: 12 }}>Init 容器挂载</Typography.Text>
                      <div style={{ marginTop: 6 }}><ContainerMountsList list={wl.init_containers} /></div>
                    </div>
                  )}
                </>
              )}
            </>
          )}
        </>
      )}
    </Drawer>
  )
}
