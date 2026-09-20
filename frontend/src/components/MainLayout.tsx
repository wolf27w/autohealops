// 主布局：Sider 菜单 + Header + Content（全屏 + 现代配色）
import { useEffect, useState } from 'react'
import { Layout, Menu, Dropdown, Avatar, Space, Badge, Popover, Button, List, Empty, Tabs } from 'antd'
import type { MenuProps } from 'antd'
import {
  DashboardOutlined, ClusterOutlined, AlertOutlined, UnorderedListOutlined,
  NotificationOutlined, RobotOutlined, FileTextOutlined, SafetyOutlined,
  AppstoreOutlined, ClockCircleOutlined, CheckCircleOutlined,
  BookOutlined, AuditOutlined, BarChartOutlined, UserOutlined,
  SettingOutlined, LogoutOutlined, DownOutlined, ExperimentOutlined, MedicineBoxOutlined, KeyOutlined,
  ApartmentOutlined, BellOutlined, FullscreenOutlined, FullscreenExitOutlined,
  MenuFoldOutlined, MenuUnfoldOutlined,
} from '@ant-design/icons'
import { Outlet, useNavigate, useLocation } from 'react-router-dom'
import { getUnreadCount, getInAppMessages, markInAppRead, markAllInAppRead } from '../services/api'

const { Sider, Header, Content } = Layout

interface InAppMessage {
  id: number
  title: string
  content?: string
  link?: string
  is_read: boolean
}

const menuItems: MenuProps['items'] = [
  {
    label: '监控', key: 'g-monitor',
    children: [
      { key: '/dashboard', icon: <DashboardOutlined />, label: '仪表盘' },
      { key: '/clusters', icon: <ClusterOutlined />, label: '集群管理' },
    ],
  },
  {
    label: '告警处理', key: 'g-alert',
    children: [
      { key: '/alert-handling', icon: <AlertOutlined />, label: '告警处理' },
    ],
  },
  {
    label: '策略配置', key: 'g-policy',
    children: [
      { key: '/strategy', icon: <SafetyOutlined />, label: '策略配置' },
      { key: '/ai-models', icon: <RobotOutlined />, label: 'AI配置' },
      { key: '/knowledge', icon: <BookOutlined />, label: '知识库' },
    ],
  },
  {
    label: '诊断', key: 'g-diag',
    children: [
      { key: '/agent', icon: <ExperimentOutlined />, label: '应用诊断' },
      { key: '/diagnostic', icon: <MedicineBoxOutlined />, label: '数据库诊断' },
    ],
  },
  {
    label: '系统', key: 'g-system',
    children: [
      { key: '/notify-center', icon: <NotificationOutlined />, label: '通知配置' },
      { key: '/user-permission', icon: <UserOutlined />, label: '用户与权限' },
      { key: '/custom-tasks', icon: <ClockCircleOutlined />, label: '定时任务' },
      { key: '/stats', icon: <BarChartOutlined />, label: '统计报表' },
      { key: '/audit', icon: <AuditOutlined />, label: '审计追溯' },
      { key: '/settings', icon: <SettingOutlined />, label: '系统设置' },
    ],
  },
]

export default function MainLayout() {
  // 版本标记：方便确认浏览器加载的是哪一版
document.title = `API-OPS 自愈平台 · build ${__BUILD_TS__.slice(5, 16).replace('T', ' ')}`
  // eslint-disable-next-line no-console
  console.info('[API-OPS] frontend build:', __BUILD_TS__)
  const navigate = useNavigate()
  const location = useLocation()
  const user = JSON.parse(localStorage.getItem('user') || '{}')

  // 左侧菜单折叠（默认收起，记忆折叠状态）
  const [collapsed, setCollapsed] = useState(() => localStorage.getItem('sider_collapsed') === '1')
  const handleCollapse = (v: boolean) => {
    setCollapsed(v)
    localStorage.setItem('sider_collapsed', v ? '1' : '0')
  }
  // 分组展开状态（菜单伸缩：整栏折叠 + 分组展开/收起，记忆状态）
  const ALL_GROUP_KEYS = ['g-monitor', 'g-alert', 'g-policy', 'g-diag', 'g-system']
  const [openKeys, setOpenKeys] = useState<string[]>(() => {
    const saved = localStorage.getItem('sider_open_keys')
    return saved ? (JSON.parse(saved) as string[]) : ALL_GROUP_KEYS
  })
  const handleOpenChange = (keys: string[]) => {
    setOpenKeys(keys)
    localStorage.setItem('sider_open_keys', JSON.stringify(keys))
  }

  // 站内消息中心（铃铛 + 未读 + 列表 + 已读）
  const [unread, setUnread] = useState(0)
  const [messages, setMessages] = useState<InAppMessage[]>([])
  const [msgTab, setMsgTab] = useState<'unread' | 'all'>('unread')

  const loadUnread = () => {
    getUnreadCount().then((d) => setUnread(d?.count || 0)).catch(() => {})
  }
  const loadMessages = (unreadOnly: boolean) => {
    getInAppMessages({ unread_only: unreadOnly }).then((d) => setMessages((d || []) as InAppMessage[])).catch(() => {})
  }
  useEffect(() => {
    loadUnread()
    loadMessages(true)
    const timer = setInterval(loadUnread, 30000)  // 30s 轮询未读数
    return () => clearInterval(timer)
  }, [])

  const handleOpenMsg = (open: boolean) => {
    if (open) loadMessages(msgTab === 'unread')
  }

  const messagePopover = (
    <div style={{ width: 360 }}>
      <Tabs
        size="small"
        activeKey={msgTab}
        onChange={(k) => {
          setMsgTab(k as 'unread' | 'all')
          loadMessages(k === 'unread')
        }}
        items={[
          { key: 'unread', label: `未读${unread ? ` (${unread})` : ''}` },
          { key: 'all', label: '全部' },
        ]}
      />
      <List
        size="small"
        dataSource={messages}
        locale={{ emptyText: <Empty description="暂无消息" /> }}
        style={{ maxHeight: 360, overflow: 'auto' }}
        renderItem={(m: InAppMessage) => (
          <List.Item
            style={{ cursor: 'pointer', opacity: m.is_read ? 0.6 : 1 }}
            onClick={async () => {
              if (!m.is_read) {
                await markInAppRead(m.id)
                loadUnread()
                loadMessages(msgTab === 'unread')
              }
              if (m.link) navigate(m.link)
            }}
          >
            <List.Item.Meta
              title={<span style={{ fontSize: 13 }}>{m.title}{!m.is_read && <Badge color="red" style={{ marginLeft: 6 }} />}</span>}
              description={<span style={{ fontSize: 12 }}>{m.content?.slice(0, 80)}</span>}
            />
          </List.Item>
        )}
      />
      {messages.length > 0 && (
        <div style={{ textAlign: 'center', paddingTop: 8, borderTop: '1px solid #f0f0f0' }}>
          <Button type="link" size="small" onClick={async () => { await markAllInAppRead(); loadUnread(); loadMessages(msgTab === 'unread') }}>
            全部已读
          </Button>
        </div>
      )}
    </div>
  )

  const handleLogout = () => {
    localStorage.removeItem('token')
    localStorage.removeItem('user')
    navigate('/login')
  }

  const [fullscreen, setFullscreen] = useState(false)
  useEffect(() => {
    const onFs = () => setFullscreen(!!document.fullscreenElement)
    document.addEventListener('fullscreenchange', onFs)
    return () => document.removeEventListener('fullscreenchange', onFs)
  }, [])
  const toggleFullscreen = () => {
    if (document.fullscreenElement) document.exitFullscreen()
    else document.documentElement.requestFullscreen?.()
  }

  const userMenu = {
    items: [
      { key: 'logout', icon: <LogoutOutlined />, label: '退出登录', onClick: handleLogout },
    ],
  }

  return (
    <Layout style={{ height: '100vh', overflow: 'hidden' }}>
      <Sider width={220} theme="dark" collapsible collapsed={collapsed} onCollapse={handleCollapse}
        style={{ background: '#0f1a2e' }}>
        <div style={{ height: 56, display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#fff', fontSize: 17, fontWeight: 600, letterSpacing: 1, borderBottom: '1px solid rgba(255,255,255,0.08)' }}>
          API-OPS 平台
        </div>
        <Menu
          theme="dark"
          mode="inline"
          selectedKeys={[location.pathname]}
          items={menuItems}
          inlineIndent={18}
          onClick={({ key }) => navigate(key)}
          {...(collapsed ? {} : { openKeys, onOpenChange: handleOpenChange })}
          style={{ background: 'transparent', borderRight: 'none', paddingTop: 4 }}
        />
      </Sider>
      <Layout style={{ background: '#f5f6fa' }}>
        <Header style={{ background: '#fff', padding: '0 24px', display: 'flex', justifyContent: 'space-between', alignItems: 'center', boxShadow: '0 1px 4px rgba(0,21,41,0.06)', zIndex: 1, height: 56, lineHeight: '56px' }}>
          <Space size={12}>
            <Button type="text" onClick={() => handleCollapse(!collapsed)}
              icon={collapsed ? <MenuUnfoldOutlined style={{ color: '#1f2d3d', fontSize: 16 }} /> : <MenuFoldOutlined style={{ color: '#1f2d3d', fontSize: 16 }} />} />
            <div style={{ fontSize: 16, fontWeight: 500, color: '#1f2d3d' }}>API-OPS 自愈平台</div>
            <span style={{ fontSize: 11, color: '#bbb' }} title="前端构建/启动时间戳">build {__BUILD_TS__.slice(5, 16).replace('T', ' ')}</span>
          </Space>
          <Space size={16}>
            <Button type="text" icon={<BarChartOutlined />} onClick={() => window.open('/screen', '_blank')}>大屏</Button>
            <Button type="text" icon={fullscreen ? <FullscreenExitOutlined /> : <FullscreenOutlined />} onClick={toggleFullscreen} />
            <Popover content={messagePopover} trigger="click" placement="bottomRight" onOpenChange={handleOpenMsg}>
              <Badge count={unread} size="small" offset={[2, -2]}>
                <Button type="text" icon={<BellOutlined style={{ fontSize: 18 }} />} />
              </Badge>
            </Popover>
            <Dropdown menu={userMenu}>
              <Space style={{ cursor: 'pointer' }}>
                <Avatar size="small" style={{ background: '#1677ff' }} icon={<UserOutlined />} />
                <span style={{ color: '#333' }}>{user.username}（{user.role}）</span>
                <DownOutlined style={{ color: '#999', fontSize: 12 }} />
              </Space>
            </Dropdown>
          </Space>
        </Header>
        <Content style={{ padding: 12, overflow: 'auto', minHeight: 0 }}>
          <Outlet />
        </Content>
      </Layout>
    </Layout>
  )
}
