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
import NotificationConfig from './pages/NotificationConfig'
import AIModelConfig from './pages/AIModelConfig'
import PromptTemplates from './pages/PromptTemplates'
import AIConfig from './pages/AIConfig'
import AlertPolicies from './pages/AlertPolicies'
import SelfHealRules from './pages/SelfHealRules'
import ExecutionDetail from './pages/ExecutionDetail'
import BatchOperation from './pages/BatchOperation'
import CustomTasks from './pages/CustomTasks'
import AgentChat from './pages/AgentChat'
import DiagnosticCenter from './pages/DiagnosticCenter'
import FaultDiagnosis from './pages/FaultDiagnosis'
import Permissions from './pages/Permissions'
import ProjectManagement from './pages/ProjectManagement'
import ApprovalFlows from './pages/ApprovalFlows'
import IMGroups from './pages/IMGroups'
import KnowledgeBase from './pages/KnowledgeBase'
import AuditTrace from './pages/AuditTrace'
import StatsDashboard from './pages/StatsDashboard'
import UserManagement from './pages/UserManagement'
import PlatformSettings from './pages/PlatformSettings'

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
      {/* 大屏：独立路由（无侧栏/顶栏框架），内容与仪表盘完全一致 */}
      <Route path="/screen" element={<RequireAuth><Dashboard screen /></RequireAuth>} />
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
        <Route path="alerts" element={<AlertList />} />
        <Route path="approvals" element={<ApprovalList />} />
        <Route path="records" element={<ProcessingRecords />} />
        <Route path="alert-handling" element={<AlertHandling />} />
        <Route path="strategy" element={<RequireRole role="admin"><StrategyConfig /></RequireRole>} />
        <Route path="notify-center" element={<RequireRole role="admin"><NotificationCenter /></RequireRole>} />
        <Route path="user-permission" element={<RequireRole role="admin"><UserPermission /></RequireRole>} />
        <Route path="notifications" element={<RequireRole role="admin"><NotificationConfig /></RequireRole>} />
        <Route path="ai-models" element={<RequireRole role="admin"><AIConfig /></RequireRole>} />
        <Route path="prompts" element={<RequireRole role="admin"><PromptTemplates /></RequireRole>} />
        <Route path="alert-policies" element={<RequireRole role="admin"><AlertPolicies /></RequireRole>} />
        <Route path="self-heal-rules" element={<RequireRole role="admin"><SelfHealRules /></RequireRole>} />
        <Route path="executions/:planId" element={<ExecutionDetail />} />
        <Route path="batch" element={<BatchOperation />} />
        <Route path="custom-tasks" element={<RequireRole role="admin"><CustomTasks /></RequireRole>} />
        <Route path="agent" element={<AgentChat />} />
        <Route path="diagnostic" element={<DiagnosticCenter />} />
        <Route path="fault-diagnosis" element={<FaultDiagnosis />} />
        <Route path="permissions" element={<RequireRole role="admin"><Permissions /></RequireRole>} />
        <Route path="knowledge" element={<KnowledgeBase />} />
        <Route path="audit" element={<AuditTrace />} />
        <Route path="stats" element={<StatsDashboard />} />
        <Route path="users" element={<RequireRole role="admin"><UserManagement /></RequireRole>} />
        <Route path="projects" element={<RequireRole role="admin"><ProjectManagement /></RequireRole>} />
        <Route path="approval-flows" element={<RequireRole role="admin"><ApprovalFlows /></RequireRole>} />
        <Route path="im-groups" element={<RequireRole role="admin"><IMGroups /></RequireRole>} />
        <Route path="settings" element={<RequireRole role="admin"><PlatformSettings /></RequireRole>} />
      </Route>
    </Routes>
  )
}
