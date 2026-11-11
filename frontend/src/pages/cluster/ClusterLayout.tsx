// 集群内布局（R32-P1）：左侧集群导航（两级）+ 右上内容区（子路由出口）
// 老板 2026-11-11 口径：进集群后左栏整条换成集群导航，顶部「← 返回平台」；
// 工作负载类型（部署/有状态/守护/定时）用左栏底部下拉切换，不占导航层级。
import { useEffect, useMemo, useState } from 'react'
import { Layout, Menu, Tag, Typography, Spin, Button, Space } from 'antd'
import { ReloadOutlined } from '@ant-design/icons'
import { useLocation, useNavigate, useParams, Outlet } from 'react-router-dom'
import { getCluster } from '../../services/api'

// 集群返回里带 version（后端有，公共类型未声明）
interface ClusterInfo { id?: number; name?: string; env_tag?: string; version?: string }

const { Sider, Content, Header } = Layout

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

  const reload = () => {
    // 顶栏刷新：触发当前路由重新挂载（简化实现：reload 一次）
    window.location.reload()
  }

  return (
    <Layout style={{ height: '100vh', background: '#fff' }}>
      <Header style={{ height: 52, lineHeight: '52px', padding: '0 18px', background: '#fff',
                       borderBottom: '1px solid #f0f0f0', display: 'flex', alignItems: 'center', gap: 12 }}>
        <Button type="text" size="small" onClick={() => navigate('/clusters')} style={{ color: '#8c8c8c' }}>
          ← 返回平台
        </Button>
        <Typography.Text strong style={{ fontSize: 15 }}>
          {loading ? '加载中…' : (cluster?.name || `集群 ${clusterId}`)}
        </Typography.Text>
        {cluster?.env_tag && <Tag color="blue" style={{ marginInlineEnd: 0 }}>{cluster.env_tag}</Tag>}
        {cluster?.version && <Typography.Text type="secondary" style={{ fontSize: 11 }}>{cluster.version}</Typography.Text>}
        <Space style={{ marginLeft: 'auto' }}>
          <Button size="small" icon={<ReloadOutlined />} onClick={reload}>刷新</Button>
        </Space>
      </Header>
      <Layout>
      <Sider width={200} theme="light" collapsed={collapsed} collapsedWidth={200}
        style={{ borderRight: '1px solid #f0f0f0', overflow: 'auto' }}>
        <Menu
          mode="inline"
          selectedKeys={[selectedKey]}
          defaultOpenKeys={['app']}
          items={items as never}
          style={{ borderRight: 'none' }}
        />
      </Sider>
      <Content style={{ padding: 16, overflow: 'auto', background: '#fff' }}>
        <Outlet />
      </Content>
      </Layout>
    </Layout>
  )
}
