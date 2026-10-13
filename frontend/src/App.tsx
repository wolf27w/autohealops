// 路由配置
import { Routes, Route, Navigate } from 'react-router-dom'
import MainLayout from './components/MainLayout'
import Login from './pages/Login'
import Dashboard from './pages/Dashboard'
import ClusterList from './pages/ClusterList'
import ClusterDetail from './pages/ClusterDetail'
import ServiceDetail from './pages/ServiceDetail'
import AlertList from './pages/AlertList'
import ApprovalList from './pages/ApprovalList'
import ProcessingRecords from './pages/ProcessingRecords'
import AlertHandling from './pages/AlertHandling'
import StrategyConfig from './pages/StrategyConfig'
import NotificationCenter from './pages/NotificationCenter'
import UserPermission from './pages/UserPermission'
import AIConfig from './pages/AIConfig'
import AlertPolicies from './pages/AlertPolicies'
import SelfHealRules from './pages/SelfHealRules'
import ExecutionDetail from './pages/ExecutionDetail'
import BatchOperation from './pages/BatchOperation'
import CustomTasks from './pages/CustomTasks'
import AgentChat from './pages/AgentChat'
import DiagnosticCenter from './pages/DiagnosticCenter'
import FaultDiagnosis from './pages/FaultDiagnosis'
import KnowledgeBase from './pages/KnowledgeBase'
import AuditTrace from './pages/AuditTrace'
import Reports from './pages/Reports'
import PlatformSettings from './pages/PlatformSettings'
import LogSources from './pages/LogSources'
import Tickets from './pages/Tickets'
import ResourceGovernance from './pages/ResourceGovernance'
import AlertSilences from './pages/AlertSilences'
import NodeManagement from './pages/NodeManagement'

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
    <Routes>
      <Route path="/login" element={<Login />} />
      {/* 大屏：独立路由（无侧栏/顶栏框架），强制深色主题，内容与仪表盘一致 */}
      <Route
        path="/screen"
        element={
          <RequireAuth>
            <Dashboard screen />
          </RequireAuth>
        }
      />
      <Route
        path="/"
        element={
          <RequireAuth>
            <MainLayout />
          </RequireAuth>
        }
      >
        <Route index element={<Navigate to="/dashboard" replace />} />
        <Route path="dashboard" element={<Dashboard />} />
        <Route path="clusters" element={<ClusterList />} />
        <Route path="clusters/:id" element={<ClusterDetail />} />
        <Route path="clusters/:id/workloads/:kind/:namespace/:name" element={<ServiceDetail />} />
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
        <Route path="node-management" element={<RequireRole role="admin"><NodeManagement /></RequireRole>} />
        <Route path="agent" element={<AgentChat />} />
        <Route path="diagnostic" element={<DiagnosticCenter />} />
        <Route path="fault-diagnosis" element={<Navigate to="/fault-diagnosis/app" replace />} />
        <Route path="fault-diagnosis/cluster" element={<FaultDiagnosis category="cluster" />} />
        <Route path="fault-diagnosis/app" element={<FaultDiagnosis category="app" />} />
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
      </Route>
    </Routes>
  )
}
