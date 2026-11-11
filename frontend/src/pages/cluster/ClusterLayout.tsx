// 集群内布局（R32-P1）：左侧集群导航（两级）+ 右上内容区（子路由出口）
// 老板 2026-11-11 口径：进集群后左栏整条换成集群导航，顶部「← 返回平台」；
// 工作负载类型（部署/有状态/守护/定时）用左栏底部下拉切换，不占导航层级。
import { useEffect, useMemo, useState } from 'react'
import { Layout, Menu, Select, Tag, Typography, Spin } from 'antd'
import { useLocation, useNavigate, useParams, Outlet } from 'react-router-dom'
import { getCluster } from '../../services/api'

// 集群返回里带 version（后端有，公共类型未声明）
interface ClusterInfo { id?: number; name?: string; env_tag?: string; version?: string }

const { Sider, Content } = Layout

const KINDS = [
  { value: 'deployment', label: '部署' },
  { value: 'statefulset', label: '有状态副本集' },
  { value: 'daemonset', label: '守护进程集' },
  { value: 'cronjob', label: '定时任务' },
]

export default function ClusterLayout() {
  const { id } = useParams()
  const clusterId = Number(id)
  const navigate = useNavigate()
  const location = useLocation()
  const [cluster, setCluster] = useState<ClusterInfo | null>(null)
  const [loading, setLoading] = useState(true)
  const [collapsed] = useState(false)

  useEffect(() => {
    getCluster(clusterId)
      .then((c) => setCluster(c as ClusterInfo))
      .catch(() => setCluster(null))
      .finally(() => setLoading(false))
  }, [clusterId])

  // 当前所在的工作负载类型（从 URL 里取，保证刷新/直链一致）
  const wlKind = useMemo(() => {
    const m = location.pathname.match(/\/workloads\/([a-z]+)/)
    return m?.[1] || 'deployment'
  }, [location.pathname])

  const inWorkloads = location.pathname.includes('/workloads')

  const selectedKey = useMemo(() => {
    const p = location.pathname
    if (p.includes('/nodes')) return 'nodes'
    if (p.includes('/workloads')) return 'workloads'
    if (p.includes('/services')) return 'services'
    if (p.includes('/pods')) return 'pods'
    if (p.includes('/storage')) return 'storage'
    if (p.includes('/configs')) return 'configs'
    if (p.includes('/monitoring')) return 'monitoring'
    if (p.includes('/inspection')) return 'inspection'
    return 'overview'
  }, [location.pathname])

  const base = `/clusters/${clusterId}`
  const items = [
    { key: 'overview', label: '概览', onClick: () => navigate(base) },
    { key: 'nodes', label: '节点', onClick: () => navigate(`${base}/nodes`) },
    {
      key: 'app',
      label: '应用负载',
      children: [
        { key: 'workloads', label: '工作负载', onClick: () => navigate(`${base}/workloads/${wlKind}`) },
        { key: 'services', label: '服务', onClick: () => navigate(`${base}/services`) },
        { key: 'pods', label: '容器组', onClick: () => navigate(`${base}/pods`) },
      ],
    },
    { key: 'storage', label: '存储', onClick: () => navigate(`${base}/storage`) },
    { key: 'configs', label: '配置', onClick: () => navigate(`${base}/configs`) },
    { key: 'monitoring', label: '监控告警', onClick: () => navigate(`${base}/monitoring`) },
    { key: 'inspection', label: '巡检与诊断', onClick: () => navigate(`${base}/inspection`) },
  ]

  return (
    <Layout style={{ height: 'calc(100vh - 56px)' }}>
      <Sider width={200} theme="light" collapsed={collapsed} collapsedWidth={200}
        style={{ borderRight: '1px solid #f0f0f0', overflow: 'auto' }}>
        <div
          onClick={() => navigate('/clusters')}
          style={{ padding: '10px 14px 6px', fontSize: 12.5, color: '#8c8c8c', cursor: 'pointer' }}
          title="返回平台"
        >
          ← 返回平台
        </div>
        <div style={{ margin: '0 12px 10px', padding: '8px 10px', background: '#eef5ff', borderRadius: 6 }}>
          {loading ? <Spin size="small" /> : (
            <>
              <div style={{ fontWeight: 600, fontSize: 13.5 }}>{cluster?.name || `集群 ${clusterId}`}</div>
              <div style={{ marginTop: 2, display: 'flex', gap: 6, alignItems: 'center' }}>
                <Tag color="blue" style={{ marginInlineEnd: 0 }}>{cluster?.env_tag || '-'}</Tag>
                <Typography.Text type="secondary" style={{ fontSize: 11 }}>
                  {cluster?.version || ''}
                </Typography.Text>
              </div>
            </>
          )}
        </div>
        <Menu
          mode="inline"
          selectedKeys={[selectedKey]}
          defaultOpenKeys={['app']}
          items={items as never}
          style={{ borderRight: 'none' }}
        />
        {inWorkloads && (
          <div style={{ padding: '10px 12px', borderTop: '1px solid #f0f0f0', marginTop: 8 }}>
            <div style={{ fontSize: 12, color: '#8c8c8c', marginBottom: 6 }}>工作负载类型</div>
            <Select
              size="small"
              style={{ width: '100%' }}
              value={wlKind}
              onChange={(v) => navigate(`${base}/workloads/${v}`)}
              options={KINDS}
            />
          </div>
        )}
      </Sider>
      <Content style={{ padding: 16, overflow: 'auto' }}>
        <Outlet />
      </Content>
    </Layout>
  )
}
