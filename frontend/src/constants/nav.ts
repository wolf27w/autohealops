// 导航唯一事实源：菜单树 / 角色门槛 / 路径匹配 / 旧地址重定向
// 约束：菜单最多两级（NavGroup → NavLeaf）；叶子 key 必须是真实路由路径
// 详见 docs/P0-NAV-EXECUTION-PLAN-2026-11-13.md

export type Role = 'admin' | 'operator' | 'viewer'

export interface NavLeaf {
  key: string            // 路由路径
  label: string
  icon: string           // 图标名（MainLayout 里映射成组件，本文件不依赖 antd）
  minRole?: Role          // 缺省 = 所有登录用户可见
  requiresGrant?: 'host'  // 授权可见：admin，或被授予主机组权限的用户（由 canSee/visibleNav 按 hostGrant 判定）
}

export interface NavGroup {
  key: string            // g-*，仅分组标识，不是路由
  label: string
  icon: string
  children: NavLeaf[]
}

export const ROLE_RANK: Record<Role, number> = { viewer: 1, operator: 2, admin: 3 }

// 概览单独置顶，不占模块名额
export const OVERVIEW: NavLeaf = { key: '/dashboard', label: '运行态势', icon: 'DashboardOutlined' }

// 资源 / 告警 / 诊断 / 平台 —— 见需求文档第三节
export const NAV: NavGroup[] = [
  {
    key: 'g-resource', label: '资源中心', icon: 'ClusterOutlined',
    children: [
      { key: '/clusters', label: 'K8s 集群', icon: 'ClusterOutlined' },
      { key: '/resources/hosts', label: '节点管理', icon: 'DesktopOutlined', requiresGrant: 'host' },
      { key: '/resources/databases', label: '数据库管理', icon: 'DatabaseOutlined' },
    ],
  },
  {
    key: 'g-alert', label: '告警监控', icon: 'WarningOutlined',
    children: [
      { key: '/alerts/handling', label: '告警处理', icon: 'AlertOutlined' },
      { key: '/alerts/tickets', label: '工单', icon: 'FileTextOutlined' },
      { key: '/alerts/policies', label: '告警策略', icon: 'SafetyOutlined', minRole: 'admin' },
      { key: '/alerts/silences', label: '告警屏蔽', icon: 'StopOutlined', minRole: 'admin' },
      { key: '/alerts/on-duty', label: '值班排班', icon: 'CalendarOutlined' },
      { key: '/alerts/sources', label: '采集与日志源', icon: 'DatabaseOutlined', minRole: 'admin' },
    ],
  },
  {
    key: 'g-diagnose', label: '诊断与自愈', icon: 'MedicineBoxOutlined',
    children: [
      { key: '/diagnose/agent', label: '应用诊断助手', icon: 'MessageOutlined' },
      { key: '/diagnose/strategy', label: '自愈规则与审批', icon: 'SafetyOutlined', minRole: 'admin' },
      { key: '/diagnose/tasks', label: '定时与批量任务', icon: 'ClockCircleOutlined', minRole: 'admin' },
      { key: '/diagnose/release', label: '发布历史', icon: 'RocketOutlined' },
      { key: '/diagnose/executions', label: '执行明细', icon: 'AuditOutlined' },
      { key: '/diagnose/traces', label: 'Agent 运行轨迹', icon: 'NodeIndexOutlined' },
      { key: '/diagnose/eval', label: '诊断质量评估', icon: 'AuditOutlined' },
    ],
  },
  {
    key: 'g-platform', label: '平台', icon: 'SettingOutlined',
    children: [
      { key: '/platform/users', label: '用户与权限', icon: 'UserOutlined', minRole: 'admin' },
      { key: '/platform/ai', label: 'AI 配置与知识库', icon: 'RobotOutlined', minRole: 'admin' },
      { key: '/platform/notifications', label: '通知配置', icon: 'NotificationOutlined', minRole: 'admin' },
      { key: '/platform/audit', label: '审计追溯', icon: 'AuditOutlined' },
      { key: '/platform/reports', label: '报表', icon: 'BarChartOutlined' },
      { key: '/platform/settings', label: '系统设置', icon: 'SettingOutlined', minRole: 'admin' },
    ],
  },
]

/** 拍平所有叶子（全局搜索、最长前缀匹配都用它） */
export function flattenNav(): NavLeaf[] {
  return NAV.flatMap((g) => g.children)
}

/** canSee / visibleNav 的可选判定参数 */
export interface NavSeeOpts {
  /** 是否已授予主机组权限（admin 恒为 true 由其自身判定，无需额外传） */
  hostGrant?: boolean
}

/** 该叶子是否需要主机组授权（admin 或 my_role 非空才可见） */
export function needsHostGrant(leaf: NavLeaf): boolean {
  return leaf.requiresGrant === 'host'
}

export function canSee(role: string, leaf: NavLeaf, opts?: NavSeeOpts): boolean {
  // 主机授权叶子：admin 恒可见，其余按 hostGrant
  if (needsHostGrant(leaf)) {
    return role === 'admin' || !!opts?.hostGrant
  }
  if (!leaf.minRole) return true
  return (ROLE_RANK[role as Role] ?? 0) >= ROLE_RANK[leaf.minRole]
}

/** 按角色过滤菜单：整组为空则不渲染。不传 opts 时主机授权叶子仅 admin 可见（向后兼容） */
export function visibleNav(role: string, opts?: NavSeeOpts): NavGroup[] {
  return NAV
    .map((g) => ({ ...g, children: g.children.filter((n) => canSee(role, n, opts)) }))
    .filter((g) => g.children.length > 0)
}

/** 最长前缀匹配：/alerts/tickets/12 → /alerts/tickets；/clusters/3/pods → /clusters */
export function matchNavKey(pathname: string): string | undefined {
  const keys = [OVERVIEW.key, ...flattenNav().map((n) => n.key)].sort((a, b) => b.length - a.length)
  return keys.find((k) => pathname === k || pathname.startsWith(k + '/'))
}

/** 该路径要求的最低角色（App.tsx 守卫用，替代手写 RequireRole） */
export function minRoleForPath(pathname: string): Role | undefined {
  const k = matchNavKey(pathname)
  return flattenNav().find((n) => n.key === k)?.minRole
}

/** 当前路径应展开的分组 key（手风琴用） */
export function navOpenKeys(pathname: string): string[] {
  const k = matchNavKey(pathname)
  const g = NAV.find((x) => x.children.some((c) => c.key === k))
  return g ? [g.key] : []
}

/** 全局搜索项：label 带上分组名，便于区分同名页面 */
export function searchItems(): { value: string; label: string }[] {
  const out: { value: string; label: string }[] = [
    { value: OVERVIEW.key, label: `概览 / ${OVERVIEW.label}` },
  ]
  for (const g of NAV) {
    for (const n of g.children) out.push({ value: n.key, label: `${g.label} / ${n.label}` })
  }
  return out
}

/** 旧地址 → 新地址（前缀匹配，保留尾部路径与 query，见 App.tsx 的 LegacyRedirect） */
export const LEGACY_REDIRECTS: Array<[string, string]> = [
  // 资源
  ['/host-management', '/resources/hosts'],
  ['/web-terminal', '/terminal'],
  ['/resources/terminal', '/terminal'],
  ['/resource-governance', '/clusters'],
  ['/zombie-services', '/clusters'],
  ['/node-management', '/clusters'],
  // 告警
  ['/alerts', '/alerts/handling'],
  ['/alert-handling', '/alerts/handling'],
  ['/tickets', '/alerts/tickets'],
  ['/alert-silences', '/alerts/silences'],
  ['/notifications', '/platform/notifications'],
  ['/notify-center', '/platform/notifications'],
  ['/alerts/notifications', '/platform/notifications'],
  ['/on-duty', '/alerts/on-duty'],
  ['/log-sources', '/alerts/sources'],
  ['/alert-policies', '/alerts/policies'],
  // 旧的重定向遗留
  ['/approvals', '/alerts/handling'],
  ['/records', '/alerts/handling'],
  ['/batch', '/alerts/handling'],
  // 诊断
  ['/diagnostic', '/resources/databases'],
  ['/fault-diagnosis', '/alerts/handling'],
  ['/fault-diagnosis/cluster', '/alerts/handling'],
  ['/fault-diagnosis/app', '/alerts/handling'],
  ['/agent', '/diagnose/agent'],
  ['/strategy', '/diagnose/strategy'],
  ['/self-heal-rules', '/diagnose/strategy'],
  ['/approval-flows', '/diagnose/strategy'],
  ['/custom-tasks', '/diagnose/tasks'],
  ['/release-history', '/diagnose/release'],
  ['/executions', '/diagnose/executions'],
  // 平台
  ['/user-permission', '/platform/users'],
  ['/users', '/platform/users'],
  ['/projects', '/platform/users'],
  ['/permissions', '/platform/users'],
  ['/ai-models', '/platform/ai'],
  ['/prompts', '/platform/ai?tab=prompts'],
  ['/prompt-suggestions', '/platform/ai?tab=suggestions'],
  ['/knowledge', '/platform/ai?tab=knowledge'],
  ['/im-groups', '/platform/notifications'],
  ['/audit', '/platform/audit'],
  ['/reports', '/platform/reports'],
  ['/stats', '/platform/reports'],
  ['/settings', '/platform/settings'],
]
