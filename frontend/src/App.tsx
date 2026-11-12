// 路由配置
import { lazy, Suspense } from 'react'
import { Routes, Route, Navigate } from 'react-router-dom'
import { Spin } from 'antd'
import MainLayout from './components/MainLayout'
import { GlobalRefreshProvider } from './components/GlobalRefreshContext'

// 页面按路由懒加载（M5 打包优化）：首屏只加载壳 + 当前页，其余按需加载
const Login = lazy(() => import('./pages/Login'))
const Dashboard = lazy(() => import('./pages/Dashboard'))
const ClusterList = lazy(() => import('./pages/ClusterList'))
const ClusterDetail = lazy(() => import('./pages/ClusterDetail'))
const ServiceDetail = lazy(() => import('./pages/ServiceDetail'))
// R32-P1：集群内布局与子页面
const ClusterLayout = lazy(() => import('./pages/cluster/ClusterLayout'))
const ClusterOverviewRoute = lazy(() => import('./pages/cluster/adapters').then(m => ({ default: m.ClusterOverviewRoute })))
const NodesRoute = lazy(() => import('./pages/cluster/adapters').then(m => ({ default: m.NodesRoute })))
const WorkloadsRoute = lazy(() => import('./pages/cluster/adapters').then(m => ({ default: m.WorkloadsRoute })))
const WorkloadDetail = lazy(() => import('./pages/cluster/WorkloadDetail'))
const PodsRoute = lazy(() => import('./pages/cluster/adapters').then(m => ({ default: m.PodsRoute })))
const ServicesRoute = lazy(() => import('./pages/cluster/adapters').then(m => ({ default: m.ServicesRoute })))
const StorageRoute = lazy(() => import('./pages/cluster/adapters').then(m => ({ default: m.StorageRoute })))
const ConfigsRoute = lazy(() => import('./pages/cluster/adapters').then(m => ({ default: m.ConfigsRoute })))
const InspectionRoute = lazy(() => import('./pages/cluster/adapters').then(m => ({ default: m.InspectionRoute })))
const MonitoringRoute = lazy(() => import('./pages/cluster/adapters').then(m => ({ default: m.MonitoringRoute })))
const AlertList = lazy(() => import('./pages/AlertList'))
const ApprovalList = lazy(() => import('./pages/ApprovalList'))
const ProcessingRecords = lazy(() => import('./pages/ProcessingRecords'))
const AlertHandling = lazy(() => import('./pages/AlertHandling'))
const StrategyConfig = lazy(() => import('./pages/StrategyConfig'))
const NotificationCenter = lazy(() => import('./pages/NotificationCenter'))
const UserPermission = lazy(() => import('./pages/UserPermission'))
const AIConfig = lazy(() => import('./pages/AIConfig'))
const AlertPolicies = lazy(() => import('./pages/AlertPolicies'))
const SelfHealRules = lazy(() => import('./pages/SelfHealRules'))
const ExecutionDetail = lazy(() => import('./pages/ExecutionDetail'))
const BatchOperation = lazy(() => import('./pages/BatchOperation'))
const CustomTasks = lazy(() => import('./pages/CustomTasks'))
const AgentChat = lazy(() => import('./pages/AgentChat'))
const FaultDiagnosisHub = lazy(() => import('./pages/FaultDiagnosisHub'))
const KnowledgeBase = lazy(() => import('./pages/KnowledgeBase'))
const AuditTrace = lazy(() => import('./pages/AuditTrace'))
const Reports = lazy(() => import('./pages/Reports'))
const PlatformSettings = lazy(() => import('./pages/PlatformSettings'))
const ReleaseHistory = lazy(() => import('./pages/ReleaseHistory'))
const LogSources = lazy(() => import('./pages/LogSources'))
const Tickets = lazy(() => import('./pages/Tickets'))
const ResourceGovernance = lazy(() => import('./pages/ResourceGovernance'))
const AlertSilences = lazy(() => import('./pages/AlertSilences'))
const NodeManagement = lazy(() => import('./pages/NodeManagement'))
const HostManagement = lazy(() => import('./pages/HostManagement'))
const WebTerminal = lazy(() => import('./pages/WebTerminal'))
const HostSessionReplay = lazy(() => import('./pages/HostSessionReplay'))
const PromptSuggestions = lazy(() => import('./pages/PromptSuggestions'))
const ZombieServices = lazy(() => import('./pages/ZombieServices'))
const OnDuty = lazy(() => import('./pages/OnDuty'))

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
        <Route path="clusters" element={<ClusterList />} />
        <Route path="alerts" element={<Navigate to="/alert-handling" replace />} />
        <Route path="tickets" element={<Tickets />} />
        <Route path="approvals" element={<Navigate to="/alert-handling" replace />} />
        <Route path="records" element={<Navigate to="/alert-handling" replace />} />
        <Route path="alert-handling" element={<AlertHandling />} />
        <Route path="strategy" element={<RequireRole role="admin"><StrategyConfig /></RequireRole>} />
        <Route path="notify-center" element={<RequireRole role="admin"><NotificationCenter /></RequireRole>} />
        <Route path="user-permission" element={<RequireRole role="admin"><UserPermission /></RequireRole>} />
        <Route path="notifications" element={<Navigate to="/notify-center" replace />} />
        <Route path="ai-models" element={<RequireRole role="admin"><AIConfig /></RequireRole>} />
        <Route path="prompts" element={<Navigate to="/ai-models" replace />} />
        <Route path="alert-policies" element={<Navigate to="/strategy" replace />} />
        <Route path="log-sources" element={<RequireRole role="admin"><LogSources /></RequireRole>} />
        <Route path="self-heal-rules" element={<Navigate to="/strategy" replace />} />
        <Route path="executions/:planId" element={<ExecutionDetail />} />
        <Route path="batch" element={<Navigate to="/alert-handling" replace />} />
        <Route path="custom-tasks" element={<RequireRole role="admin"><CustomTasks /></RequireRole>} />
        <Route path="resource-governance" element={<RequireRole role="admin"><ResourceGovernance /></RequireRole>} />
        <Route path="alert-silences" element={<RequireRole role="admin"><AlertSilences /></RequireRole>} />
        <Route path="on-duty" element={<OnDuty />} />
        <Route path="node-management" element={<RequireRole role="admin"><NodeManagement /></RequireRole>} />
        <Route path="host-management" element={<HostManagement />} />
        <Route path="web-terminal" element={<WebTerminal />} />
        <Route path="web-terminal/:hostId" element={<WebTerminal />} />
        <Route path="host-sessions/:id/replay" element={<HostSessionReplay />} />
        <Route path="prompt-suggestions" element={<RequireRole role="admin"><PromptSuggestions /></RequireRole>} />
        <Route path="zombie-services" element={<ZombieServices />} />
        <Route path="agent" element={<AgentChat />} />
        <Route path="diagnostic" element={<Navigate to="/fault-diagnosis?tab=db" replace />} />
        <Route path="fault-diagnosis" element={<FaultDiagnosisHub />} />
        <Route path="fault-diagnosis/cluster" element={<Navigate to="/fault-diagnosis?tab=cluster" replace />} />
        <Route path="fault-diagnosis/app" element={<Navigate to="/fault-diagnosis?tab=app" replace />} />
        <Route path="permissions" element={<Navigate to="/user-permission" replace />} />
        <Route path="knowledge" element={<KnowledgeBase />} />
        <Route path="audit" element={<AuditTrace />} />
        <Route path="reports" element={<Reports />} />
        <Route path="stats" element={<Navigate to="/reports" replace />} />
        <Route path="users" element={<Navigate to="/user-permission" replace />} />
        <Route path="projects" element={<Navigate to="/user-permission" replace />} />
        <Route path="approval-flows" element={<Navigate to="/strategy" replace />} />
        <Route path="im-groups" element={<Navigate to="/notify-center" replace />} />
        <Route path="settings" element={<RequireRole role="admin"><PlatformSettings /></RequireRole>} />
        <Route path="release-history" element={<ReleaseHistory />} />
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
        <Route path="storage" element={<StorageRoute />} />
        <Route path="configs" element={<ConfigsRoute />} />
        <Route path="monitoring" element={<MonitoringRoute />} />
        <Route path="inspection" element={<InspectionRoute />} />
      </Route>
    </Routes>
    </Suspense>
  )
}
