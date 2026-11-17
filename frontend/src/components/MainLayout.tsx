// 主布局：Sider 菜单 + Header + Content（全屏 + 现代配色）
import { useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { Layout, Menu, Dropdown, Avatar, Space, Badge, Popover, Button, List, Empty, Tabs, theme, AutoComplete, Input, message } from 'antd'
import type { MenuProps } from 'antd'
import {
  DashboardOutlined, ClusterOutlined, AlertOutlined, WarningOutlined,
  NotificationOutlined, RobotOutlined, FileTextOutlined, SafetyOutlined,
  ClockCircleOutlined, BarChartOutlined, UserOutlined,
  SettingOutlined, LogoutOutlined, DownOutlined, MedicineBoxOutlined,
  ApartmentOutlined, BellOutlined, FullscreenOutlined, FullscreenExitOutlined,
  MenuFoldOutlined, MenuUnfoldOutlined, DatabaseOutlined, MessageOutlined,
  ControlOutlined, SunOutlined, MoonOutlined, StopOutlined, DesktopOutlined, CalendarOutlined,
  CodeOutlined, RocketOutlined, AuditOutlined,
  SearchOutlined, ReloadOutlined,
} from '@ant-design/icons'
import { Outlet, useNavigate, useLocation } from 'react-router-dom'
import { getUnreadCount, getInAppMessages, markInAppRead, markAllInAppRead, getHostGroups } from '../services/api'
import { getCurrentUser, isAdmin } from '../utils/auth'
import { useThemeMode } from '../utils/theme'
import { useGlobalRefresh } from './GlobalRefreshContext'
import {
  NAV, OVERVIEW, visibleNav, matchNavKey, navOpenKeys, searchItems, flattenNav, needsHostGrant,
} from '../constants/nav'
import type { NavGroup } from '../constants/nav'

const { Sider, Header, Content } = Layout

interface InAppMessage {
  id: number
  title: string
  content?: string
  link?: string
  is_read: boolean
}

// 菜单图标：nav.ts 只存图标名，这里统一映射成组件（nav.ts 不依赖 antd）
const ICONS: Record<string, ReactNode> = {
  DashboardOutlined: <DashboardOutlined />,
  ClusterOutlined: <ClusterOutlined />,
  DesktopOutlined: <DesktopOutlined />,
  DatabaseOutlined: <DatabaseOutlined />,
  CodeOutlined: <CodeOutlined />,
  ControlOutlined: <ControlOutlined />,
  WarningOutlined: <WarningOutlined />,
  AlertOutlined: <AlertOutlined />,
  FileTextOutlined: <FileTextOutlined />,
  SafetyOutlined: <SafetyOutlined />,
  StopOutlined: <StopOutlined />,
  NotificationOutlined: <NotificationOutlined />,
  CalendarOutlined: <CalendarOutlined />,
  MedicineBoxOutlined: <MedicineBoxOutlined />,
  ApartmentOutlined: <ApartmentOutlined />,
  MessageOutlined: <MessageOutlined />,
  ClockCircleOutlined: <ClockCircleOutlined />,
  RocketOutlined: <RocketOutlined />,
  AuditOutlined: <AuditOutlined />,
  UserOutlined: <UserOutlined />,
  RobotOutlined: <RobotOutlined />,
  BarChartOutlined: <BarChartOutlined />,
  SettingOutlined: <SettingOutlined />,
}

const iconOf = (name: string): ReactNode => ICONS[name] ?? <ClusterOutlined />

// 菜单树 → antd Menu items（概览置顶 + 分组两级）
function toMenuItems(groups: NavGroup[]): MenuProps['items'] {
  return [
    { key: OVERVIEW.key, icon: iconOf(OVERVIEW.icon), label: OVERVIEW.label },
    ...groups.map((g) => ({
      key: g.key,
      icon: iconOf(g.icon),
      label: g.label,
      children: g.children.map((n) => ({ key: n.key, icon: iconOf(n.icon), label: n.label })),
    })),
  ] as MenuProps['items']
}

export default function MainLayout() {
  const { token } = theme.useToken()
  const { mode, toggle } = useThemeMode()
  const { refresh } = useGlobalRefresh()
  // 版本标记：方便确认浏览器加载的是哪一版
  document.title = `AutoHealOps 自愈平台 · build ${__BUILD_TS__.slice(5, 16).replace('T', ' ')}`
  // eslint-disable-next-line no-console
  console.info('[AutoHealOps] frontend build:', __BUILD_TS__)
  const navigate = useNavigate()
  const location = useLocation()
  // 安全读取当前用户（JSON.parse 写坏不崩溃）；按角色过滤菜单
  const user = getCurrentUser()
  const [hostGrant, setHostGrant] = useState<boolean | null>(isAdmin() ? true : null)
  useEffect(() => {
    // admin 恒可见，无需请求；其余用户拉一次主机组列表，看是否被授予任意组角色
    if (isAdmin()) return
    getHostGroups()
      .then((d) => setHostGrant(!!(d?.groups ?? []).some((g) => g.my_role)))
      .catch(() => setHostGrant(false)) // 请求失败按「无授权」处理，不阻塞渲染
  }, [])
  const navItems = visibleNav(user.role || 'viewer', { hostGrant: !!hostGrant })

  // 直链拦截：命中「需要主机授权」页面且无授权 → 弹回首页（只提示一次，避免循环）
  const grantWarnedPath = useRef<string | null>(null)
  useEffect(() => {
    if (hostGrant !== false) return // 未加载完 / 已授权 / admin 不拦截
    const leaf = flattenNav().find((n) => n.key === matchNavKey(location.pathname))
    if (leaf && needsHostGrant(leaf)) {
      if (grantWarnedPath.current !== location.pathname) {
        grantWarnedPath.current = location.pathname
        message.warning('无该主机组权限，请联系管理员授权')
      }
      navigate('/dashboard', { replace: true })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hostGrant, location.pathname])

  // 左侧菜单折叠（默认收起，记忆折叠状态）
  const [collapsed, setCollapsed] = useState(() => localStorage.getItem('sider_collapsed') === '1')
  const handleCollapse = (v: boolean) => {
    setCollapsed(v)
    localStorage.setItem('sider_collapsed', v ? '1' : '0')
  }

  // 顶级分组 key（手风琴互斥单位）
  const TOP_KEYS = NAV.map((g) => g.key)
  // 每个顶级分组下、需要与顶级一起展开的二级子菜单 key（当前菜单只有两级，留空）
  const SUB_KEYS: Record<string, string[]> = {}
  // 按当前路由计算应展开的分组（来自 nav.ts，两级菜单）
  const openKeysForPath = navOpenKeys

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
  // 全局搜索（拍平菜单叶子项，label 带分组名）
  const searchOptions = searchItems()
  const [searchVal, setSearchVal] = useState('')
  const [searchOpen, setSearchOpen] = useState(false)
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
      {/* trigger={null}：底部那根折叠条与顶部的折叠按钮重复，去掉底部的（2026-10-21 老板要求） */}
      <Sider width={236} theme="light" collapsible trigger={null} collapsed={collapsed} onCollapse={handleCollapse}>
        <div style={{ height: 56, display: 'flex', alignItems: 'center', justifyContent: collapsed ? 'center' : 'flex-start', gap: 10, padding: collapsed ? 0 : '0 16px', borderBottom: `1px solid ${token.colorBorderSecondary}` }}>
          <div style={{ width: 30, height: 30, borderRadius: 6, background: token.colorPrimary, display: 'grid', placeItems: 'center', flex: 'none' }}>
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#ffffff" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
              <circle cx="12" cy="5" r="2.2" /><circle cx="5" cy="19" r="2.2" /><circle cx="19" cy="19" r="2.2" />
              <path d="M12 7.2v4M12 11.2 6.6 16.9M12 11.2l5.4 5.7" />
            </svg>
          </div>
          {!collapsed && (
            <div style={{ lineHeight: 1.15 }}>
              <b style={{ color: token.colorText, fontSize: 15, letterSpacing: 0.4 }}>AutoHealOps</b>
              <small style={{ display: 'block', color: token.colorTextTertiary, fontSize: 9.5, letterSpacing: 2 }}>自愈运维平台</small>
            </div>
          )}
        </div>
        <div style={{ flex: 1, overflow: 'auto', paddingBottom: 8 }}>
          <Menu
            theme="light"
            mode="inline"
            selectedKeys={[matchNavKey(location.pathname) ?? location.pathname]}
            items={toMenuItems(navItems)}
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
            <div style={{ fontSize: 16, fontWeight: 700, color: token.colorText }}>AutoHealOps 自愈平台</div>
            <AutoComplete
              value={searchVal}
              options={searchOptions}
              onChange={(v) => setSearchVal(v)}
              onSearch={(v) => { setSearchVal(v); setSearchOpen(true) }}
              onSelect={(k: string) => { setSearchVal(''); setSearchOpen(false); navigate(k) }}
              open={searchOpen && !!searchVal}
              onBlur={() => setTimeout(() => setSearchOpen(false), 150)}
              style={{ width: 240 }}
              placeholder="全局搜索…"
              filterOption={(input, option) => String(option?.label ?? '').toLowerCase().includes(input.toLowerCase())}
            >
              <Input prefix={<SearchOutlined style={{ color: token.colorTextSecondary }} />} allowClear />
            </AutoComplete>
          </Space>
          <Space size={16}>
            <Button type="text" icon={<ReloadOutlined />} onClick={refresh} title="刷新当前页数据" />
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
