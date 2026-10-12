// 主布局：Sider 菜单 + Header + Content（全屏 + 现代配色）
import { useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { Layout, Menu, Dropdown, Avatar, Space, Badge, Popover, Button, List, Empty, Tabs, theme } from 'antd'
import type { MenuProps } from 'antd'
import {
  DashboardOutlined, ClusterOutlined, AlertOutlined, WarningOutlined,
  NotificationOutlined, RobotOutlined, FileTextOutlined, SafetyOutlined,
  AppstoreOutlined, ClockCircleOutlined, FundOutlined,
  BookOutlined, AuditOutlined, BarChartOutlined, UserOutlined,
  SettingOutlined, LogoutOutlined, DownOutlined, MedicineBoxOutlined,
  ApartmentOutlined, BellOutlined, FullscreenOutlined, FullscreenExitOutlined,
  MenuFoldOutlined, MenuUnfoldOutlined, DatabaseOutlined, MessageOutlined,
  ThunderboltOutlined, ControlOutlined, SunOutlined, MoonOutlined,
} from '@ant-design/icons'
import { Outlet, useNavigate, useLocation } from 'react-router-dom'
import { getUnreadCount, getInAppMessages, markInAppRead, markAllInAppRead } from '../services/api'
import { getCurrentUser, isAdmin } from '../utils/auth'
import { useThemeMode } from '../utils/theme'

const { Sider, Header, Content } = Layout

interface InAppMessage {
  id: number
  title: string
  content?: string
  link?: string
  is_read: boolean
}

// 导航项：一级分组 / 二级菜单 / 三级子项（层级 ≤3）
interface NavItem {
  key: string
  label?: string
  icon?: ReactNode
  children?: NavItem[]
}

// 菜单：6 大模块，层级 ≤3（仅「诊断 → 故障诊断」用到第三级）
const menuItems: NavItem[] = [
  {
    key: 'g-monitor', label: '监控中心', icon: <FundOutlined />,
    children: [
      { key: '/dashboard', icon: <DashboardOutlined />, label: '仪表盘' },
      { key: '/clusters', icon: <ClusterOutlined />, label: '集群管理' },
    ],
  },
  {
    key: 'g-alert', label: '告警与工单', icon: <WarningOutlined />,
    children: [
      { key: '/alert-handling', icon: <AlertOutlined />, label: '告警处理' },
      { key: '/tickets', icon: <FileTextOutlined />, label: '工单' },
    ],
  },
  {
    key: 'g-diag', label: '智能诊断', icon: <MedicineBoxOutlined />,
    children: [
      {
        key: 'g-diag-fault', label: '故障诊断', icon: <ApartmentOutlined />,
        children: [
          { key: '/fault-diagnosis/cluster', label: '集群故障' },
          { key: '/fault-diagnosis/app', label: '应用故障' },
          { key: '/diagnostic', label: '数据库故障' },
        ],
      },
      { key: '/agent', icon: <MessageOutlined />, label: '应用诊断助手' },
    ],
  },
  {
    key: 'g-auto', label: '自动化运维', icon: <ThunderboltOutlined />,
    children: [
      { key: '/strategy', icon: <SafetyOutlined />, label: '策略配置' },
      { key: '/custom-tasks', icon: <ClockCircleOutlined />, label: '定时任务' },
      { key: '/resource-governance', icon: <SettingOutlined />, label: '资源治理' },
    ],
  },
  {
    key: 'g-config', label: '知识配置', icon: <AppstoreOutlined />,
    children: [
      { key: '/ai-models', icon: <RobotOutlined />, label: 'AI 配置' },
      { key: '/knowledge', icon: <BookOutlined />, label: '知识库' },
      { key: '/log-sources', icon: <DatabaseOutlined />, label: '日志源' },
      { key: '/notify-center', icon: <NotificationOutlined />, label: '通知配置' },
    ],
  },
  {
    key: 'g-system', label: '系统管理', icon: <ControlOutlined />,
    children: [
      { key: '/user-permission', icon: <UserOutlined />, label: '用户与权限' },
      { key: '/reports', icon: <BarChartOutlined />, label: '报表' },
      { key: '/audit', icon: <AuditOutlined />, label: '审计追溯' },
      { key: '/settings', icon: <SettingOutlined />, label: '系统设置' },
    ],
  },
]

// admin 专属菜单项（viewer/operator 不可见；App.tsx 路由同样有 RequireRole 守卫）
const ADMIN_ONLY_KEYS = new Set([
  '/strategy', '/notify-center', '/user-permission', '/log-sources',
  '/ai-models', '/custom-tasks', '/settings', '/resource-governance',
])

// 按角色过滤菜单：admin 全见；非 admin 去掉 admin 专属项（递归处理三级；空分组一并移除）
function filterMenuItems(items: NavItem[], admin: boolean): MenuProps['items'] {
  if (admin) return items as MenuProps['items']
  const filter = (nodes: NavItem[]): NavItem[] =>
    (nodes || [])
      .map((item) => {
        if (!item) return null
        if (item.children && item.children.length) {
          const children = filter(item.children)
          if (!children.length) return null
          return { ...item, children }
        }
        return ADMIN_ONLY_KEYS.has(String(item.key)) ? null : item
      })
      .filter(Boolean) as NavItem[]
  return filter(items) as MenuProps['items']
}

export default function MainLayout() {
  const { token } = theme.useToken()
  const { mode, toggle } = useThemeMode()
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

  // 顶级分组 key（手风琴互斥单位）
  const TOP_KEYS = menuItems.map((g) => g.key)
  // 每个顶级分组下、需要与顶级一起展开的二级子菜单 key
  const SUB_KEYS: Record<string, string[]> = { 'g-diag': ['g-diag-fault'] }
  // 按当前路由计算应展开的 submenu keys（顶级分组 + 祖先二级），支持三级
  const openKeysForPath = (p: string): string[] => {
    for (const g of menuItems) {
      const walk = (nodes: NavItem[], trail: string[]): string[] | null => {
        for (const n of nodes || []) {
          if (n.children && n.children.length) {
            const r = walk(n.children, [...trail, n.key])
            if (r) return r
          } else {
            const k = n.key
            if (k && (p === k || p.startsWith(k + '/'))) return [...trail, g.key]
          }
        }
        return null
      }
      const r = walk(g.children || [], [])
      if (r) return r
    }
    return []
  }

  // 站内消息中心（铃铛 + 未读 + 列表 + 已读）
  const [unread, setUnread] = useState(0)
  const [openKeys, setOpenKeys] = useState<string[]>(() => openKeysForPath(location.pathname))
  const handleOpenChange = (keys: string[]) => {
    // 手风琴只在「顶级分组」间互斥；二级子菜单自由展开
    const latest = keys.find((k) => !openKeys.includes(k))
    if (latest && TOP_KEYS.includes(latest)) {
      const subs = SUB_KEYS[latest] || []
      setOpenKeys([latest, ...keys.filter((k) => subs.includes(k))])
    } else {
      setOpenKeys(keys)
    }
  }
  useEffect(() => {
    if (collapsed) return
    const keys = openKeysForPath(location.pathname)
    if (keys.length) setOpenKeys(keys)
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
          <div style={{ width: 30, height: 30, borderRadius: 9, background: 'linear-gradient(135deg,#34d399,#059669)', display: 'grid', placeItems: 'center', boxShadow: '0 6px 16px rgba(16,185,129,0.35)', flex: 'none' }}>
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#04231a" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
              <circle cx="12" cy="5" r="2.2" /><circle cx="5" cy="19" r="2.2" /><circle cx="19" cy="19" r="2.2" />
              <path d="M12 7.2v4M12 11.2 6.6 16.9M12 11.2l5.4 5.7" />
            </svg>
          </div>
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
      <Layout style={{ background: token.colorBgLayout }}>
        <Header style={{ padding: '0 18px', display: 'flex', justifyContent: 'space-between', alignItems: 'center', zIndex: 1, height: 56, lineHeight: '56px' }}>
          <Space size={12}>
            <Button type="text" onClick={() => handleCollapse(!collapsed)}
              icon={collapsed ? <MenuUnfoldOutlined style={{ color: token.colorText, fontSize: 16 }} /> : <MenuFoldOutlined style={{ color: token.colorText, fontSize: 16 }} />} />
            <div style={{ fontSize: 16, fontWeight: 700, color: token.colorText }}>API-OPS 自愈平台</div>
            <span style={{ fontSize: 11, color: token.colorTextSecondary }} title="前端构建/启动时间戳">build {__BUILD_TS__.slice(5, 16).replace('T', ' ')}</span>
          </Space>
          <Space size={16}>
            <Button type="text" icon={<BarChartOutlined />} onClick={() => window.open('/screen', '_blank')}>大屏</Button>
            <Button
              type="text"
              icon={mode === 'dark' ? <SunOutlined /> : <MoonOutlined />}
              onClick={toggle}
              title={mode === 'dark' ? '切换浅色主题' : '切换深色主题'}
            />
            <Button type="text" icon={fullscreen ? <FullscreenExitOutlined /> : <FullscreenOutlined />} onClick={toggleFullscreen} />
            <Popover content={messagePopover} trigger="click" placement="bottomRight" onOpenChange={handleOpenMsg}>
              <Badge count={unread} size="small" offset={[2, -2]}>
                <Button type="text" icon={<BellOutlined style={{ fontSize: 18 }} />} />
              </Badge>
            </Popover>
            <Dropdown menu={userMenu}>
              <Space style={{ cursor: 'pointer' }}>
                <Avatar size="small" style={{ background: 'linear-gradient(135deg,#34d399,#0d9488)' }} icon={<UserOutlined />} />
                <span style={{ color: token.colorText }}>{user.username}（{user.role}）</span>
                <DownOutlined style={{ color: token.colorTextSecondary, fontSize: 12 }} />
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
