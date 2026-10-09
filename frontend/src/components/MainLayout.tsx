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
  MenuFoldOutlined, MenuUnfoldOutlined, DatabaseOutlined, MessageOutlined,
} from '@ant-design/icons'
import { Outlet, useNavigate, useLocation } from 'react-router-dom'
import { getUnreadCount, getInAppMessages, markInAppRead, markAllInAppRead } from '../services/api'
import { getCurrentUser, isAdmin } from '../utils/auth'

const { Sider, Header, Content } = Layout

interface InAppMessage {
  id: number
  title: string
  content?: string
  link?: string
  is_read: boolean
}

// 菜单：可折叠分组（配合手风琴逻辑：同时只展开一个）
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
      { key: '/tickets', icon: <FileTextOutlined />, label: '工单' },
    ],
  },
  {
    label: '策略配置', key: 'g-policy',
    children: [
      { key: '/strategy', icon: <SafetyOutlined />, label: '策略配置' },
      { key: '/log-sources', icon: <DatabaseOutlined />, label: '日志源' },
      { key: '/ai-models', icon: <RobotOutlined />, label: 'AI配置' },
      { key: '/knowledge', icon: <BookOutlined />, label: '知识库' },
    ],
  },
  {
    label: '诊断', key: 'g-diag',
    children: [
      { key: '/fault-diagnosis/cluster', icon: <ApartmentOutlined />, label: '集群故障诊断' },
      { key: '/fault-diagnosis/app', icon: <ExperimentOutlined />, label: '应用故障诊断' },
      { key: '/diagnostic', icon: <MedicineBoxOutlined />, label: '数据库故障诊断' },
      { key: '/agent', icon: <MessageOutlined />, label: '应用诊断助手' },
    ],
  },
  {
    label: '系统', key: 'g-system',
    children: [
      { key: '/notify-center', icon: <NotificationOutlined />, label: '通知配置' },
      { key: '/user-permission', icon: <UserOutlined />, label: '用户与权限' },
      { key: '/custom-tasks', icon: <ClockCircleOutlined />, label: '定时任务' },
      { key: '/reports', icon: <BarChartOutlined />, label: '报表' },
      { key: '/audit', icon: <AuditOutlined />, label: '审计追溯' },
      { key: '/settings', icon: <SettingOutlined />, label: '系统设置' },
    ],
  },
]

// admin 专属菜单项（viewer/operator 不可见；App.tsx 路由同样有 RequireRole 守卫）
const ADMIN_ONLY_KEYS = new Set([
  '/strategy', '/notify-center', '/user-permission', '/log-sources',
  '/ai-models', '/custom-tasks', '/settings',
])

// 按角色过滤菜单：admin 全见；非 admin 去掉 admin 专属项（空分组一并移除）
function filterMenuItems(items: MenuProps['items'], admin: boolean): MenuProps['items'] {
  if (admin) return items
  return (items || [])
    .map((item) => {
      if (!item || !('children' in item) || !item.children) return item
      const children = (item.children as NonNullable<MenuProps['items']>).filter(
        (c) => c && !ADMIN_ONLY_KEYS.has(String(c.key)),
      )
      if (children.length === 0) return null
      return { ...item, children }
    })
    .filter(Boolean) as MenuProps['items']
}

export default function MainLayout() {
  // 版本标记：方便确认浏览器加载的是哪一版
document.title = `API-OPS 自愈平台 · build ${__BUILD_TS__.slice(5, 16).replace('T', ' ')}`
  // eslint-disable-next-line no-console
  console.info('[API-OPS] frontend build:', __BUILD_TS__)
  const navigate = useNavigate()
  const location = useLocation()
  // 安全读取当前用户（JSON.parse 写坏不崩溃）；按角色过滤菜单
  const user = getCurrentUser()
  const admin = isAdmin()
  const filteredMenuItems = filterMenuItems(menuItems, admin)

  // 左侧菜单折叠（默认收起，记忆折叠状态）
  const [collapsed, setCollapsed] = useState(() => localStorage.getItem('sider_collapsed') === '1')
  const handleCollapse = (v: boolean) => {
    setCollapsed(v)
    localStorage.setItem('sider_collapsed', v ? '1' : '0')
  }
  // 分组折叠（手风琴）：同一时刻只展开一个分组；按当前路由自动展开所属分组
  const groupOfPath = (p: string) => {
    for (const g of (menuItems || [])) {
      const ch = (g as { children?: { key?: string }[] })?.children || []
      for (const c of ch) {
        const k = String(c.key || '')
        if (k && (p === k || p.startsWith(k + '/'))) return String((g as { key?: string })?.key || '')
      }
    }
    return ''
  }
  // 站内消息中心（铃铛 + 未读 + 列表 + 已读）
  const [unread, setUnread] = useState(0)
  const [openKeys, setOpenKeys] = useState<string[]>(() => {
    const g = groupOfPath(location.pathname)
    return g ? [g] : []
  })
  const handleOpenChange = (keys: string[]) => {
    // 手风琴：只保留最新展开的那个分组
    const latest = keys.find((k) => !openKeys.includes(k))
    setOpenKeys(latest ? [latest] : [])
  }
  useEffect(() => {
    if (collapsed) return
    const g = groupOfPath(location.pathname)
    if (g) setOpenKeys([g])
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [location.pathname, collapsed])
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
      <Sider width={236} theme="dark" collapsible collapsed={collapsed} onCollapse={handleCollapse}>
        <div style={{ height: 56, display: 'flex', alignItems: 'center', justifyContent: collapsed ? 'center' : 'flex-start', gap: 10, padding: collapsed ? 0 : '0 16px', borderBottom: '1px solid rgba(255,255,255,0.06)' }}>
          <div style={{ width: 30, height: 30, borderRadius: 9, background: 'linear-gradient(135deg,#34d399,#059669)', display: 'grid', placeItems: 'center', color: '#04231a', fontWeight: 800, fontSize: 15, boxShadow: '0 6px 16px rgba(16,185,129,0.35)', flex: 'none' }}>吴</div>
          {!collapsed && (
            <div style={{ lineHeight: 1.15 }}>
              <b style={{ color: '#fff', fontSize: 15, letterSpacing: 0.4 }}>API-OPS</b>
              <small style={{ display: 'block', color: '#6f8f80', fontSize: 9.5, letterSpacing: 2 }}>自愈运维平台</small>
            </div>
          )}
        </div>
        <div style={{ flex: 1, overflow: 'auto', paddingBottom: 8 }}>
          <Menu
            theme="dark"
            mode="inline"
            selectedKeys={[location.pathname]}
            items={filteredMenuItems}
            inlineIndent={18}
            onClick={({ key }) => navigate(key)}
            {...(collapsed ? {} : { openKeys, onOpenChange: handleOpenChange })}
            style={{ borderRight: 'none', paddingTop: 6 }}
          />
        </div>
      </Sider>
      <Layout style={{ background: '#f5f6fa' }}>
        <Header style={{ padding: '0 18px', display: 'flex', justifyContent: 'space-between', alignItems: 'center', zIndex: 1, height: 56, lineHeight: '56px' }}>
          <Space size={12}>
            <Button type="text" onClick={() => handleCollapse(!collapsed)}
              icon={collapsed ? <MenuUnfoldOutlined style={{ color: '#0f2018', fontSize: 16 }} /> : <MenuFoldOutlined style={{ color: '#0f2018', fontSize: 16 }} />} />
            <div style={{ fontSize: 16, fontWeight: 700, color: '#0f2018' }}>API-OPS 自愈平台</div>
            <span style={{ fontSize: 11, color: '#8aa196' }} title="前端构建/启动时间戳">build {__BUILD_TS__.slice(5, 16).replace('T', ' ')}</span>
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
                <Avatar size="small" style={{ background: 'linear-gradient(135deg,#34d399,#0d9488)' }} icon={<UserOutlined />} />
                <span style={{ color: '#0f2018' }}>{user.username}（{user.role}）</span>
                <DownOutlined style={{ color: '#999', fontSize: 12 }} />
              </Space>
            </Dropdown>
          </Space>
        </Header>
        <Content style={{ padding: 14, overflow: 'auto', minHeight: 0 }}>
          <Outlet />
        </Content>
      </Layout>
    </Layout>
  )
}
