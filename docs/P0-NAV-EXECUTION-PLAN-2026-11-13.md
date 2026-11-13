# P0 骨架执行清单（R34 · 待 review 后开工）

> 对应 `docs/RESOURCE-CENTER-LAYOUT-REQUIREMENTS-2026-11-13.md` 第九节 P0。
> **本文只出清单，不动代码。** review 通过后再落地。
> 目标：把所有页面的**入口和 URL** 一次性摆正；页面内部逻辑 P0 一律不改。

---

## 一、P0 范围（3 改 + 2 新建）

| 文件 | 动作 | 说明 |
|---|---|---|
| `src/constants/nav.ts` | **新建** | 菜单树 + 角色门槛 + 路径匹配 + 旧地址重定向，唯一事实源 |
| `src/components/MainLayout.tsx` | 改 | 删本地 `menuItems` / `ADMIN_ONLY_KEYS` / `flattenMenu` / `filterMenuItems`，全部改为从 nav.ts 取；`selectedKeys` 改最长前缀匹配 |
| `src/App.tsx` | 改 | 路由前缀归位 + 旧地址统一由 `LEGACY_REDIRECTS` 生成；角色守卫改读 nav.ts |
| `src/pages/PlatformAI.tsx` | **新建（胶水页）** | Tabs 拼装：模型配置 / 提示词模板 / 运行成本 / 知识库 / 提示词建议。约 25 行 |
| `src/pages/DiagnoseStrategy.tsx` | **新建（胶水页）** | Tabs 拼装：自愈规则 / 审批流程。约 20 行 |

**明确不做**：不新建数据库页面（P1）、不删 `AlertHandling` 的诊断 Tab（P2）、不合并僵尸服务（P2）、不动 `/clusters/:id/*` 内部、不动后端。

---

## 二、三个决策（review 重点）

### 决策 1：K8s 集群**不**迁到 `/resources/clusters`

- 理由：`/clusters` 前缀在代码里 **115 处**引用（`ClusterList`、`Dashboard`、`ServiceDetail`、各种 Modal 跳转），且 `ClusterLayout` 是**顶层路由**（不套 `MainLayout`），改前缀要连带动 20+ 文件。
- 做法：菜单项「K8s 集群」key 直接指向 `/clusters`，详情仍是 `/clusters/:id/*`。
- **代价**：URL 前缀不完全统一（K8s 是例外）。收益 > 代价，**建议接受**。

### 决策 2：旧地址用统一的 `LegacyRedirect` 组件，不逐条写 `<Navigate>`

- 现在 `App.tsx` 里已有 20+ 条硬编码 `<Navigate>`，分散且难维护。
- P0 改为：`LEGACY_REDIRECTS` 数组 + 一个 `LegacyRedirect` 组件（保留路径尾部与 query）。**一条规则覆盖全部**，以后加模块不用再补重定向。

### 决策 3：`/alerts` 前缀与旧路由 `/alerts` 冲突

- 旧 `/alerts` 是「跳告警处理」的重定向。
- 新前缀也是 `/alerts/*`。React Router v6 排序下 `/alerts` 精确匹配优先，`/alerts/handling` 不会被它吃掉。
- 做法：保留 `/alerts → /alerts/handling` 一条精确重定向，其余走新路由。

---

## 三、`src/constants/nav.ts` 完整内容（可直接落）

```ts
// 导航唯一事实源：菜单树 / 角色门槛 / 路径匹配 / 旧地址重定向
// 约束：菜单最多两级（NavGroup → NavLeaf）；叶子 key 必须是真实路由路径

export type Role = 'admin' | 'operator' | 'viewer'

export interface NavLeaf {
  key: string          // 路由路径
  label: string
  icon: string         // 图标名（MainLayout 里映射成组件，nav.ts 不依赖 antd）
  minRole?: Role        // 缺省 = 所有登录用户可见
  requiresGrant?: 'host' // 授权可见：admin，或被授予主机组权限的用户（P2 实现；P0 只按 minRole 判定）
}

export interface NavGroup {
  key: string          // g-*，仅分组标识，不是路由
  label: string
  icon: string
  children: NavLeaf[]
}

export const ROLE_RANK: Record<Role, number> = { viewer: 1, operator: 2, admin: 3 }

// 概览单独置顶，不占模块名额
export const OVERVIEW: NavLeaf = { key: '/dashboard', label: '运行态势', icon: 'DashboardOutlined' }

export const NAV: NavGroup[] = [
  {
    key: 'g-resource', label: '资源中心', icon: 'ClusterOutlined',
    children: [
      { key: '/clusters', label: 'K8s 集群', icon: 'ClusterOutlined' },
      { key: '/resources/hosts', label: '主机与虚拟机', icon: 'DesktopOutlined', minRole: 'admin', requiresGrant: 'host' },
      { key: '/resources/databases', label: '数据库', icon: 'DatabaseOutlined' },
      { key: '/resources/terminal', label: '堡垒机终端', icon: 'CodeOutlined', minRole: 'admin', requiresGrant: 'host' },
      { key: '/resources/governance', label: '资源治理', icon: 'ControlOutlined', minRole: 'admin' },
    ],
  },
  {
    key: 'g-alert', label: '告警监控', icon: 'WarningOutlined',
    children: [
      { key: '/alerts/handling', label: '告警处理', icon: 'AlertOutlined' },
      { key: '/alerts/tickets', label: '工单', icon: 'FileTextOutlined' },
      { key: '/alerts/policies', label: '告警策略', icon: 'SafetyOutlined', minRole: 'admin' },
      { key: '/alerts/silences', label: '告警屏蔽', icon: 'StopOutlined', minRole: 'admin' },
      { key: '/alerts/notifications', label: '通知配置', icon: 'NotificationOutlined', minRole: 'admin' },
      { key: '/alerts/on-duty', label: '值班排班', icon: 'CalendarOutlined' },
      { key: '/alerts/sources', label: '采集与日志源', icon: 'DatabaseOutlined', minRole: 'admin' },
    ],
  },
  {
    key: 'g-diagnose', label: '诊断与自愈', icon: 'MedicineBoxOutlined',
    children: [
      { key: '/diagnose/fault', label: '故障诊断', icon: 'ApartmentOutlined' },
      { key: '/diagnose/agent', label: '应用诊断助手', icon: 'MessageOutlined' },
      { key: '/diagnose/strategy', label: '自愈规则与审批', icon: 'SafetyOutlined', minRole: 'admin' },
      { key: '/diagnose/tasks', label: '定时与批量任务', icon: 'ClockCircleOutlined', minRole: 'admin' },
      { key: '/diagnose/release', label: '发布历史', icon: 'RocketOutlined' },
      { key: '/diagnose/executions', label: '执行与审批记录', icon: 'AuditOutlined' },
    ],
  },
  {
    key: 'g-platform', label: '平台', icon: 'SettingOutlined',
    children: [
      { key: '/platform/users', label: '用户与权限', icon: 'UserOutlined', minRole: 'admin' },
      { key: '/platform/ai', label: 'AI 配置与知识库', icon: 'RobotOutlined', minRole: 'admin' },
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

export function canSee(role: string, leaf: NavLeaf): boolean {
  if (!leaf.minRole) return true
  return (ROLE_RANK[role as Role] ?? 0) >= ROLE_RANK[leaf.minRole]
}

/** 按角色过滤菜单：整组为空则不渲染 */
export function visibleNav(role: string): NavGroup[] {
  return NAV
    .map((g) => ({ ...g, children: g.children.filter((n) => canSee(role, n)) }))
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
export function openKeysForPath(pathname: string): string[] {
  const k = matchNavKey(pathname)
  const g = NAV.find((x) => x.children.some((c) => c.key === k))
  return g ? [g.key] : []
}

/** 旧地址 → 新地址（前缀匹配，保尾部路径与 query，见 LegacyRedirect） */
export const LEGACY_REDIRECTS: Array<[string, string]> = [
  // 资源
  ['/host-management', '/resources/hosts'],
  ['/web-terminal', '/resources/terminal'],
  ['/resource-governance', '/resources/governance'],
  ['/zombie-services', '/resources/governance'],
  ['/node-management', '/clusters'],
  // 告警
  ['/alerts', '/alerts/handling'],
  ['/alert-handling', '/alerts/handling'],
  ['/tickets', '/alerts/tickets'],
  ['/alert-silences', '/alerts/silences'],
  ['/notifications', '/alerts/notifications'],
  ['/notify-center', '/alerts/notifications'],
  ['/on-duty', '/alerts/on-duty'],
  ['/log-sources', '/alerts/sources'],
  ['/alert-policies', '/alerts/policies'],
  // 旧的重定向遗留
  ['/approvals', '/alerts/handling'],
  ['/records', '/alerts/handling'],
  ['/batch', '/alerts/handling'],
  // 诊断
  ['/diagnostic', '/diagnose/fault?tab=db'],
  ['/fault-diagnosis', '/diagnose/fault'],
  ['/fault-diagnosis/cluster', '/diagnose/fault?tab=cluster'],
  ['/fault-diagnosis/app', '/diagnose/fault?tab=app'],
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
  ['/im-groups', '/alerts/notifications'],
  ['/audit', '/platform/audit'],
  ['/reports', '/platform/reports'],
  ['/stats', '/platform/reports'],
  ['/settings', '/platform/settings'],
]
```

> 说明：`/clusters`、`/dashboard`、`/host-sessions/:id/replay`、`/screen`、`/login` **不在**重定向表里（保持原路径）。

---

## 四、`src/App.tsx` 改动

### 4.1 新增 `LegacyRedirect`（保尾部路径 + query）

```tsx
import { useLocation } from 'react-router-dom'
import { LEGACY_REDIRECTS } from './constants/nav'

function LegacyRedirect({ to }: { to: string }) {
  const loc = useLocation()
  const from = LEGACY_REDIRECTS
    .map(([f]) => f)
    .sort((a, b) => b.length - a.length)
    .find((f) => loc.pathname === f || loc.pathname.startsWith(f + '/')) || ''
  const rest = from ? loc.pathname.slice(from.length) : ''
  const [path, query] = to.split('?')
  const search = query ? `?${query}` : loc.search
  return <Navigate to={`${path}${rest}${search}`} replace />
}
```

### 4.2 角色守卫改由 nav.ts 派生（不再逐条硬编码）

```tsx
import { minRoleForPath } from './constants/nav'

// 页面级守卫：按路径查最低角色（唯一事实源 = nav.ts）
const guard = (path: string, el: JSX.Element) =>
  minRoleForPath(path) === 'admin' ? <RequireRole role="admin">{el}</RequireRole> : el
```

### 4.3 路由表（新路径 → 现有组件）

```tsx
<Route index element={<Navigate to="/dashboard" replace />} />
<Route path="dashboard" element={<Dashboard />} />

{/* ===== 资源中心 ===== */}
<Route path="clusters" element={<ClusterList />} />
<Route path="resources/hosts" element={<HostManagement />} />
<Route path="resources/databases" element={<DiagnosticCenter />} />   {/* P1 换成 DbResourceList */}
<Route path="resources/terminal" element={<WebTerminal />} />
<Route path="resources/terminal/:hostId" element={<WebTerminal />} />
<Route path="resources/governance" element={guard('/resources/governance', <ResourceGovernance />)} />
<Route path="host-sessions/:id/replay" element={<HostSessionReplay />} />  {/* 保留原路径（直链） */}

{/* ===== 告警监控 ===== */}
<Route path="alerts/handling" element={<AlertHandling />} />
<Route path="alerts/tickets" element={<Tickets />} />
<Route path="alerts/policies" element={guard('/alerts/policies', <AlertPolicies />)} />
<Route path="alerts/silences" element={guard('/alerts/silences', <AlertSilences />)} />
<Route path="alerts/notifications" element={guard('/alerts/notifications', <NotificationCenter />)} />
<Route path="alerts/on-duty" element={<OnDuty />} />
<Route path="alerts/sources" element={guard('/alerts/sources', <LogSources />)} />

{/* ===== 诊断与自愈 ===== */}
<Route path="diagnose/fault" element={<FaultDiagnosisHub />} />
<Route path="diagnose/agent" element={<AgentChat />} />
<Route path="diagnose/strategy" element={guard('/diagnose/strategy', <DiagnoseStrategy />)} />
<Route path="diagnose/tasks" element={guard('/diagnose/tasks', <CustomTasks />)} />
<Route path="diagnose/release" element={<ReleaseHistory />} />
<Route path="diagnose/executions" element={<ApprovalList />} />          {/* P2 换执行记录列表 */}
<Route path="diagnose/executions/:planId" element={<ExecutionDetail />} />

{/* ===== 平台 ===== */}
<Route path="platform/users" element={guard('/platform/users', <UserPermission />)} />
<Route path="platform/ai" element={guard('/platform/ai', <PlatformAI />)} />
<Route path="platform/audit" element={<AuditTrace />} />
<Route path="platform/reports" element={<Reports />} />
<Route path="platform/settings" element={guard('/platform/settings', <PlatformSettings />)} />

{/* ===== 旧地址兼容（一条规则覆盖全部） ===== */}
{LEGACY_REDIRECTS.map(([from, to]) => (
  <Route key={from} path={from.replace(/^\//, '')} element={<LegacyRedirect to={to} />} />
))}
```

### 4.4 需要删掉的旧路由行

`dashboard`、`clusters`、`host-sessions/:id/replay` 保留；其余 **42 条**旧 `path` 全部删除，由 §4.3 的重定向 map 取代。
同时删掉 `lazy` 里已不再直接路由的引用（`StrategyConfig` 暂留，P2 删）。

---

## 五、`src/components/MainLayout.tsx` 改动点（逐处）

| 位置 | 现状 | 改为 |
|---|---|---|
| 第 42~96 行 `menuItems` | 本地 6 组 22 项 | **删除**，用 `visibleNav(user.role)` |
| 第 98~108 行 `flattenMenu` | 本地实现 | **删除**，用 `flattenNav()` |
| 第 111~126 行 `ADMIN_ONLY_KEYS` + `filterMenuItems` | 手写 admin 白名单 | **删除**，`minRole` 已在 nav.ts |
| 第 147 行 `filteredMenuItems` | — | `const navItems = visibleNav(user.role ?? 'viewer')` |
| 第 157 行 `TOP_KEYS` | `menuItems.map(...)` | `NAV.map(g => g.key)` |
| 第 161 行 `openKeysForPath` | 本地递归 | 用 nav.ts 的 `openKeysForPath` |
| 第 276 行 搜索项 | `flattenMenu(menuItems)` | `flattenNav()`，label 前加分组名（如「告警监控 / 工单」） |
| 第 317 行 `selectedKeys={[location.pathname]}` | 精确匹配，详情页不高亮 | `selectedKeys={[matchNavKey(location.pathname) ?? '']}` |
| 第 318 行 `items={filteredMenuItems}` | — | `[OVERVIEW 单项, ...navItems]`（概览置顶，可直接点） |
| 图标 | 内联 JSX 导入 | 顶部加 `ICON_MAP: Record<string, ReactNode>` 映射 nav.ts 里的字符串图标名 |

**注意**：`Menu` 的 `items` 顶层混排 leaf（概览）+ submenu（4 个分组）是 antd 支持的；折叠态下概览只显示图标，与现有交互一致。

---

## 六、两个胶水页

### `src/pages/PlatformAI.tsx`

```tsx
// AI 配置与知识库（合并页）：模型配置 / 提示词模板 / 运行成本 / 知识库 / 提示词建议
import { Tabs } from 'antd'
import { useSearchParams } from 'react-router-dom'
import AIModelConfig from './AIModelConfig'
import PromptTemplates from './PromptTemplates'
import AICallLogs from './AICallLogs'
import KnowledgeBase from './KnowledgeBase'
import PromptSuggestions from './PromptSuggestions'

export default function PlatformAI() {
  const [sp, setSp] = useSearchParams()
  const tab = sp.get('tab') || 'models'
  return (
    <Tabs activeKey={tab} onChange={(k) => setSp({ tab: k }, { replace: true })} destroyInactiveTabPane
      items={[
        { key: 'models', label: '模型配置', children: <AIModelConfig /> },
        { key: 'prompts', label: '提示词模板', children: <PromptTemplates /> },
        { key: 'logs', label: '运行/成本看板', children: <AICallLogs /> },
        { key: 'knowledge', label: '知识库', children: <KnowledgeBase /> },
        { key: 'suggestions', label: '提示词建议', children: <PromptSuggestions /> },
      ]} />
  )
}
```

### `src/pages/DiagnoseStrategy.tsx`

```tsx
// 自愈规则与审批（合并页）：自愈规则 / 审批流程（告警策略已移到 /alerts/policies）
import { Tabs } from 'antd'
import SelfHealRules from './SelfHealRules'
import ApprovalFlows from './ApprovalFlows'

export default function DiagnoseStrategy() {
  return (
    <Tabs defaultActiveKey="self-heal" destroyInactiveTabPane
      items={[
        { key: 'self-heal', label: '自愈规则', children: <SelfHealRules /> },
        { key: 'approval', label: '审批流程', children: <ApprovalFlows /> },
      ]} />
  )
}
```

> `StrategyConfig.tsx`（原三合一）P0 保留不路由，P2 删除。

---

## 七、P0 验收清单

| # | 验收项 | 方法 |
|---|---|---|
| 1 | 编译 | `npx tsc -b` 0 错 |
| 2 | 静态检查 | `npx eslint src` 0 error |
| 3 | 构建 | `npm run build` 通过 |
| 4 | 菜单项逐一可达 | CDP 点开 24 项（1 概览 + 4 组 23 叶子），每项渲染出主标题/表格，无白屏、无 console error |
| 5 | 旧地址重定向 | 41 条逐个访问，落到新页面；`/web-terminal/7` → `/resources/terminal/7` 参数不丢 |
| 6 | 菜单高亮 | 进 `/alerts/tickets/12` 这类详情，左侧仍高亮「工单」 |
| 7 | 折叠态 | 折叠后概览 + 4 组图标正常，展开恢复 |
| 8 | 全局搜索 | 搜「工单」能跳 `/alerts/tickets`；搜项显示「分组 / 页面」 |
| 9 | 三角色 | admin 全见；operator/viewer 看不到 9 个 `minRole: 'admin'` 项；直接输 URL 越权被弹回 `/dashboard` |
| 10 | 无重复入口 | 菜单里「告警处理」只有一处；「故障诊断」只有 `/diagnose/fault` 一处 |

---

## 八、风险与注意

1. **`api.ts` 里的同名路径不要动**：`'/tickets'`、`'/knowledge'`、`'/audit'` 等在 `api.ts` 出现是 HTTP 接口路径，只改**路由**。改动时按文件白名单来（只碰 `App.tsx` / `MainLayout.tsx` / `nav.ts` / 2 个新页）。
2. **`Dashboard.tsx`、`ClusterDetail.tsx`、`WebTerminal.tsx`、`HostManagement.tsx`、`FaultDiagnosis.tsx` 里有指向旧路径的 `navigate()`**（共 8 处左右）：P0 靠重定向兜底仍可用，但会在地址栏闪一下旧路径。**建议 P0 顺手改这 8 处**（都是字符串替换，且这些文件只有这几行改动，冲突面可控）。
3. **`RequireRole` 失败跳 `/dashboard`**：保持不变，避免行为变化。
4. **`ClusterLayout` 的「← 返回平台」**：硬编码跳 `/clusters`，不动。
5. **删除 `ADMIN_ONLY_KEYS` 时**要确认 `minRole` 覆盖了原来全部 12 个 key（`/strategy` `/notify-center` `/user-permission` `/log-sources` `/ai-models` `/custom-tasks` `/settings` `/prompt-suggestions` `/resource-governance` `/alert-silences` `/node-management` `/host-management`）。**注意**：原 `/host-management` 在 admin 白名单里，新方案是全员可见 —— 这是一处**权限放宽**，需你确认；若要保持原样，把 `{ key: '/resources/hosts', ... minRole: 'admin' }`。
6. **P1 落位后要回收的两处临时代码**：`/resources/databases → DiagnosticCenter`、`/diagnose/executions → ApprovalList`，已在代码注释里标注。

---

## 九、工时

| 任务 | 工时 |
|---|---|
| `nav.ts`（含 41 条重定向表） | 0.3d |
| `App.tsx` 路由重组 + `LegacyRedirect` | 0.3d |
| `MainLayout.tsx` 菜单接入 + 图标映射 | 0.2d |
| 2 个胶水页 + 8 处 `navigate` 旧路径替换 | 0.2d |
| 验收（tsc/eslint/build + CDP 24 项 + 41 条重定向 + 三角色） | 0.3d |
| **合计** | **约 1.3 人日** |

> 不含「授权后可见」（P2，0.5d，见第十节）。

---

## 十、需要你拍板

1. ~~`/resources/hosts` 是否保持 admin 专属~~ → **已确认（2026-11-13 12:04）：admin 专属 + 可按用户单独授权**。
   - P0 落地形态：两项菜单 `minRole: 'admin'`（静态判定，行为与现在一致，不阻塞骨架）。
   - 「授权后 operator 可见」放 **P2** 实现（0.5d）：nav 加 `requiresGrant: 'host'` 运行时判定 + 登录后拉一次 `GET /host-groups`（后端 `/hosts`、终端、SFTP、回放的主机级鉴权**已全部实现**，只缺菜单入口）。详见需求文档 7.8。
2. 其余按本文执行。
