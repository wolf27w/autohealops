// 集群内布局（R32-P1）：左侧集群导航（两级）+ 右上内容区（子路由出口）
// 老板 2026-11-11 口径：进集群后左栏整条换成集群导航，顶部「← 返回平台」；
// 工作负载类型（部署/有状态/守护/定时）用左栏底部下拉切换，不占导航层级。
import { useEffect, useMemo, useState } from 'react'
import { Layout, Menu, Tag, Typography, Spin, Button, Space } from 'antd'
import { ReloadOutlined } from '@ant-design/icons'
import { useLocation, useNavigate, useParams, Outlet } from 'react-router-dom'
import { getCluster } from '../../services/api'
import { ClusterNamespaceProvider } from './NamespaceContext'

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
  // 手风琴：只展开当前 key 所在分组
  const [openKeys, setOpenKeys] = useState<string[]>(['app'])

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
    if (p.includes('/pods')) return 'pods'
    if (p.includes('/services')) return 'services'
    if (p.includes('/ingresses')) return 'ingresses'
    if (p.includes('/storageclasses')) return 'storageclasses'
    if (p.includes('/storage')) return 'storage'
    if (p.includes('/configmaps') || p.includes('/configs')) return 'configmaps'
    if (p.includes('/secrets')) return 'secrets'
    if (p.includes('/serviceaccounts')) return 'serviceaccounts'
    if (p.includes('/governance')) return 'governance'
    if (p.includes('/inspection')) return 'inspection'
    return 'overview'
  }, [location.pathname])

  const groupOf = (k: string) =>
    ['configmaps', 'secrets', 'serviceaccounts'].includes(k) ? 'cfg'
      : ['storage', 'storageclasses'].includes(k) ? 'stg'
        : 'app'

  useEffect(() => { setOpenKeys([groupOf(selectedKey)]) }, [selectedKey])

  const base = `/clusters/${clusterId}`
  // 左栏结构按 R35 §2.1（2026-11-13 定稿）：概览 / K8s 节点 / 应用负载 / 配置 / 存储 / 资源治理 / 巡检与诊断
  const items = [
    { key: 'overview', label: '概览', onClick: () => navigate(base) },
    { key: 'nodes', label: 'K8s 节点', onClick: () => navigate(`${base}/nodes`) },
    {
      key: 'app',
      label: '应用负载',
      children: [
        { key: 'workloads', label: '工作负载', onClick: () => navigate(`${base}/workloads/${wlKind}`) },
        { key: 'pods', label: '容器组', onClick: () => navigate(`${base}/pods`) },
        { key: 'services', label: '服务', onClick: () => navigate(`${base}/services`) },
        { key: 'ingresses', label: '应用路由', onClick: () => navigate(`${base}/ingresses`) },
      ],
    },
    {
      key: 'cfg',
      label: '配置',
      children: [
        { key: 'configmaps', label: '配置字典', onClick: () => navigate(`${base}/configmaps`) },
        { key: 'secrets', label: '保密字典', onClick: () => navigate(`${base}/secrets`) },
        { key: 'serviceaccounts', label: '服务账户', onClick: () => navigate(`${base}/serviceaccounts`) },
      ],
    },
    {
      key: 'stg',
      label: '存储',
      children: [
        { key: 'storage', label: '持久卷声明', onClick: () => navigate(`${base}/storage`) },
        { key: 'storageclasses', label: '存储类', onClick: () => navigate(`${base}/storageclasses`) },
      ],
    },
    { key: 'governance', label: '资源治理', onClick: () => navigate(`${base}/governance`) },
    // R33-5：集群内「监控告警」页已下线（与平台仪表盘/大屏重复），保留「巡检与诊断」
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
      <Sider width={200} theme="light" className="cluster-sider" collapsed={collapsed} collapsedWidth={200}
        style={{ borderRight: '1px solid #f0f0f0', overflow: 'auto' }}>
        <Menu
          mode="inline"
          selectedKeys={[selectedKey]}
          openKeys={openKeys}
          onOpenChange={(keys) => setOpenKeys(keys as string[])}
          items={items as never}
          style={{ borderRight: 'none' }}
        />
      </Sider>
      <Content style={{ padding: 16, overflow: 'auto', background: '#fff' }}>
        {/* 命名空间选择跨页联动：默认 default，切一次全站跟着切 */}
        <ClusterNamespaceProvider clusterId={clusterId}>
          <Outlet />
        </ClusterNamespaceProvider>
      </Content>
      </Layout>
    </Layout>
  )
}
