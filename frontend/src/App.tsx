// 路由配置（R34-P0：URL 前缀归位 · 资源/告警/诊断/平台）
// 菜单树、角色门槛、旧地址重定向统一在 src/constants/nav.ts
import { Fragment, lazy, Suspense, useEffect, useState } from 'react'
import { Routes, Route, Navigate, useLocation, useNavigate } from 'react-router-dom'
import { Spin, message } from 'antd'
import { isAdmin } from './utils/auth'
import { getHostGroups } from './services/api'
import MainLayout from './components/MainLayout'
import { GlobalRefreshProvider } from './components/GlobalRefreshContext'
import { LEGACY_REDIRECTS, minRoleForPath } from './constants/nav'

// 页面按路由懒加载（M5 打包优化）：首屏只加载壳 + 当前页，其余按需加载
const Login = lazy(() => import('./pages/Login'))
const Dashboard = lazy(() => import('./pages/Dashboard'))
const ClusterList = lazy(() => import('./pages/ClusterList'))
// R32-P1：集群内布局与子页面
const ClusterLayout = lazy(() => import('./pages/cluster/ClusterLayout'))
const ClusterOverviewRoute = lazy(() => import('./pages/cluster/adapters').then(m => ({ default: m.ClusterOverviewRoute })))
const NodesRoute = lazy(() => import('./pages/cluster/adapters').then(m => ({ default: m.NodesRoute })))
const WorkloadsRoute = lazy(() => import('./pages/cluster/adapters').then(m => ({ default: m.WorkloadsRoute })))
const WorkloadDetail = lazy(() => import('./pages/cluster/WorkloadDetail'))
const PodsRoute = lazy(() => import('./pages/cluster/adapters').then(m => ({ default: m.PodsRoute })))
const ServicesRoute = lazy(() => import('./pages/cluster/adapters').then(m => ({ default: m.ServicesRoute })))
const IngressListRoute = lazy(() => import('./pages/cluster/adapters').then(m => ({ default: m.IngressListRoute })))
const StorageRoute = lazy(() => import('./pages/cluster/adapters').then(m => ({ default: m.StorageRoute })))
const ConfigsRoute = lazy(() => import('./pages/cluster/adapters').then(m => ({ default: m.ConfigsRoute })))
const ConfigMapsRoute = lazy(() => import('./pages/cluster/adapters').then(m => ({ default: m.ConfigMapsRoute })))
const SecretsRoute = lazy(() => import('./pages/cluster/adapters').then(m => ({ default: m.SecretsRoute })))
const StorageClassesRoute = lazy(() => import('./pages/cluster/adapters').then(m => ({ default: m.StorageClassesRoute })))
const InspectionRoute = lazy(() => import('./pages/cluster/adapters').then(m => ({ default: m.InspectionRoute })))
const GovernanceRoute = lazy(() => import('./pages/cluster/adapters').then(m => ({ default: m.GovernanceRoute })))
const ServiceAccountsRoute = lazy(() => import('./pages/cluster/adapters').then(m => ({ default: m.ServiceAccountsRoute })))
// 资源中心
const HostManagement = lazy(() => import('./pages/HostManagement'))
const WebTerminal = lazy(() => import('./pages/WebTerminal'))
const HostSessionReplay = lazy(() => import('./pages/HostSessionReplay'))
const DbResourceList = lazy(() => import('./pages/DbResourceList'))
const DbDetail = lazy(() => import('./pages/DbDetail'))
const SqlTerminal = lazy(() => import('./pages/SqlTerminal'))
// 告警监控
const AlertHandling = lazy(() => import('./pages/AlertHandling'))
const Tickets = lazy(() => import('./pages/Tickets'))
const AlertPolicies = lazy(() => import('./pages/AlertPolicies'))
const AlertSilences = lazy(() => import('./pages/AlertSilences'))
const NotificationCenter = lazy(() => import('./pages/NotificationCenter'))
const OnDuty = lazy(() => import('./pages/OnDuty'))
const LogSources = lazy(() => import('./pages/LogSources'))
// 诊断与自愈
const AgentChat = lazy(() => import('./pages/AgentChat'))
const DiagnoseStrategy = lazy(() => import('./pages/DiagnoseStrategy'))
const CustomTasks = lazy(() => import('./pages/CustomTasks'))
const ReleaseHistory = lazy(() => import('./pages/ReleaseHistory'))
const ApprovalList = lazy(() => import('./pages/ApprovalList'))          // TODO(P2)：换成执行记录列表
const ExecutionDetail = lazy(() => import('./pages/ExecutionDetail'))
// 平台
const UserPermission = lazy(() => import('./pages/UserPermission'))
const PlatformAI = lazy(() => import('./pages/PlatformAI'))
const AuditTrace = lazy(() => import('./pages/AuditTrace'))
const Reports = lazy(() => import('./pages/Reports'))
const PlatformSettings = lazy(() => import('./pages/PlatformSettings'))

function RequireAuth({ children }: { children: JSX.Element }) {
  const token = localStorage.getItem('token')
  return token ? children : <Navigate to="/login" replace />
}

// 角色守卫：非指定角色跳转首页（admin 专属页面防越权访问）
function RequireRole({ role, children }: { role: string; children: JSX.Element }) {
  const token = localStorage.getItem('token')
  if (!token) return <Navigate to="/login" replace />
  let user: { role?: string } = {}
  try {
    user = JSON.parse(localStorage.getItem('user') || '{}')
  } catch {
    user = {}
  }
  if (user.role !== role) return <Navigate to="/dashboard" replace />
  return children
}

// 页面级守卫：最低角色统一从 nav.ts 查（不再手写每一条）
function guard(path: string, el: JSX.Element) {
  return minRoleForPath(path) === 'admin' ? <RequireRole role="admin">{el}</RequireRole> : el
}

// 主机授权守卫：独立整屏页（/terminal）不在 MainLayout 内，需自行判定主机组授权
function RequireHostGrant({ children }: { children: JSX.Element }) {
  const [ok, setOk] = useState<boolean | null>(isAdmin() ? true : null)
  const navigate = useNavigate()
  useEffect(() => {
    if (isAdmin()) { setOk(true); return }
    getHostGroups()
      .then((d) => {
        const groups = (d as { groups?: Array<{ my_role?: string }> })?.groups || []
        setOk(groups.some((g) => !!g.my_role))
      })
      .catch(() => setOk(false))
  }, [])
  useEffect(() => {
    if (ok === false) {
      message.warning('无该主机组权限，请联系管理员授权')
      navigate('/dashboard', { replace: true })
    }
  }, [ok, navigate])
  if (ok === null) return <div style={{ display: 'flex', justifyContent: 'center', paddingTop: 120 }}><Spin size="large" /></div>
  return ok ? children : null
}

// 旧地址重定向：替换前缀、保留尾部路径与 query（如 /web-terminal → /terminal）
function LegacyRedirect({ from, to }: { from: string; to: string }) {
  const loc = useLocation()
  const rest = loc.pathname.slice(from.length)
  const [path, query] = to.split('?')
  return <Navigate to={`${path}${rest}${query ? `?${query}` : loc.search}`} replace />
}

// 需要保留尾部路径（带参数）的旧前缀；其余旧地址只做精确匹配，避免吞掉未知路径
const LEGACY_PARAM_PREFIXES = new Set(['/web-terminal', '/executions'])

export default function App() {
  return (
    <Suspense fallback={<div style={{ display: 'flex', justifyContent: 'center', paddingTop: 120 }}><Spin size="large" /></div>}>
    <Routes>
      <Route path="/login" element={<Login />} />
      {/* 大屏：独立路由（无侧栏/顶栏框架），加载自研监控大屏（docs/monitor-bigscreen.html → frontend/public 同源服务） */}
      <Route
        path="/screen"
        element={
          <RequireAuth>
            <iframe src="/monitor-bigscreen.html?v=14" title="监控大屏"
              style={{ position: 'fixed', inset: 0, width: '100vw', height: '100vh', border: 0 }} />
          </RequireAuth>
        }
      />
      {/* 独立整屏终端：新标签页打开，不套平台侧栏（节点管理→Web 终端、数据库管理→SQL 终端） */}
      <Route path="/terminal" element={<RequireAuth><RequireHostGrant><WebTerminal standalone /></RequireHostGrant></RequireAuth>} />
      {/* 兼容旧地址 /web-terminal/:id、/resources/terminal/:id（重定向后仍带 host 路径段） */}
      <Route path="/terminal/:hostId" element={<RequireAuth><RequireHostGrant><WebTerminal standalone /></RequireHostGrant></RequireAuth>} />
      <Route path="/sql-terminal" element={<RequireAuth><SqlTerminal /></RequireAuth>} />
      <Route
        path="/"
        element={
          <RequireAuth>
            <GlobalRefreshProvider>
              <MainLayout />
            </GlobalRefreshProvider>
          </RequireAuth>
        }
      >
        <Route index element={<Navigate to="/dashboard" replace />} />
        <Route path="dashboard" element={<Dashboard />} />

        {/* ===== 资源中心 ===== */}
        <Route path="clusters" element={<ClusterList />} />
        <Route path="resources/hosts" element={guard('/resources/hosts', <HostManagement />)} />
        <Route path="resources/databases" element={<DbResourceList />} />
        <Route path="resources/databases/:id" element={<DbDetail />} />
        <Route path="host-sessions/:id/replay" element={<HostSessionReplay />} />

        {/* ===== 告警监控 ===== */}
        <Route path="alerts/handling" element={<AlertHandling />} />
        <Route path="alerts/tickets" element={<Tickets />} />
        <Route path="alerts/policies" element={guard('/alerts/policies', <AlertPolicies />)} />
        <Route path="alerts/silences" element={guard('/alerts/silences', <AlertSilences />)} />
        <Route path="alerts/notifications" element={guard('/alerts/notifications', <NotificationCenter />)} />
        <Route path="alerts/on-duty" element={<OnDuty />} />
        <Route path="alerts/sources" element={guard('/alerts/sources', <LogSources />)} />

        {/* ===== 诊断与自愈 ===== */}
        <Route path="diagnose/agent" element={<AgentChat />} />
        <Route path="diagnose/strategy" element={guard('/diagnose/strategy', <DiagnoseStrategy />)} />
        <Route path="diagnose/tasks" element={guard('/diagnose/tasks', <CustomTasks />)} />
        <Route path="diagnose/release" element={<ReleaseHistory />} />
        <Route path="diagnose/executions" element={<ApprovalList />} />
        <Route path="diagnose/executions/:planId" element={<ExecutionDetail />} />

        {/* ===== 平台 ===== */}
        <Route path="platform/users" element={guard('/platform/users', <UserPermission />)} />
        <Route path="platform/ai" element={guard('/platform/ai', <PlatformAI />)} />
        <Route path="platform/audit" element={<AuditTrace />} />
        <Route path="platform/reports" element={<Reports />} />
        <Route path="platform/settings" element={guard('/platform/settings', <PlatformSettings />)} />

        {/* ===== 旧地址兼容（一条规则覆盖全部，见 constants/nav.ts） ===== */}
        {LEGACY_REDIRECTS.map(([from, to]) => {
          const base = from.replace(/^\//, '')
          return (
            <Fragment key={from}>
              <Route path={base} element={<LegacyRedirect from={from} to={to} />} />
              {LEGACY_PARAM_PREFIXES.has(from) && (
                <Route path={`${base}/*`} element={<LegacyRedirect from={from} to={to} />} />
              )}
            </Fragment>
          )
        })}
      </Route>

      {/* R32-P1：点进集群 = 独立整页（不套平台 MainLayout），自带顶部返回条 + 集群左导航 */}
      <Route
        path="/clusters/:id"
        element={
          <RequireAuth>
            <ClusterLayout />
          </RequireAuth>
        }
      >
        <Route index element={<ClusterOverviewRoute />} />
        <Route path="nodes" element={<NodesRoute />} />
        <Route path="workloads/:kind" element={<WorkloadsRoute />} />
        <Route path="workloads/:kind/:namespace/:name" element={<WorkloadDetail />} />
        <Route path="pods" element={<PodsRoute />} />
        <Route path="services" element={<ServicesRoute />} />
        <Route path="ingresses" element={<IngressListRoute />} />
        <Route path="storage" element={<StorageRoute />} />
        <Route path="storageclasses" element={<StorageClassesRoute />} />
        <Route path="configs" element={<ConfigsRoute />} />
        <Route path="governance" element={<GovernanceRoute />} />
        <Route path="configmaps" element={<ConfigMapsRoute />} />
        <Route path="secrets" element={<SecretsRoute />} />
        <Route path="serviceaccounts" element={<ServiceAccountsRoute />} />
        <Route path="inspection" element={<InspectionRoute />} />
      </Route>
    </Routes>
    </Suspense>
  )
}
