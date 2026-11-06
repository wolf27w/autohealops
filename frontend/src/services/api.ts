// API 调用封装：axios 实例 + 统一拦截 + 全部接口函数（A3：类型化，消除 any）
import axios, { type AxiosRequestConfig } from 'axios'
import { message } from 'antd'
import type {
  AiFeedbackData, Alert, AlertComment, Approval, BatchDecideResult, Cluster, CustomTask, DashboardData, DutyOverride,
  FaultDiagnosisItem, KnowledgeEntry, LogSource, NotificationConfig, NotificationLog, NotifyPolicy, OpResult, PageParams, PageResult, PlatformHealth, Ticket, TicketComment, TicketDetail, TicketStats, User, Workload,
} from '../types'

const api = axios.create({
  baseURL: '/api/v1',
  timeout: 30000,
})

// 请求拦截器：添加 token
api.interceptors.request.use((config) => {
  const token = localStorage.getItem('token')
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

// 响应拦截器：统一错误处理；成功时把 AxiosResponse 解包为 body.data
api.interceptors.response.use(
  (response) => {
    const body = response.data
    // 文件下载（blob）：不做业务码解包
    if (response.config.responseType === 'blob') return body
    if (body && body.code !== 200) {
      message.error(body.message || '请求失败')
      return Promise.reject(new Error(body.message))
    }
    return body.data
  },
  (error) => {
    if (error.response?.status === 401) {
      localStorage.removeItem('token')
      window.location.href = '/login'
    } else {
      message.error(error.response?.data?.detail || error.message || '网络错误')
    }
    return Promise.reject(error)
  },
)

// 类型化门面：拦截器已把 AxiosResponse 解包为 body.data，这里用泛型约束返回类型
const http = {
  get: <T = unknown>(url: string, config?: AxiosRequestConfig) => api.get(url, config) as Promise<T>,
  post: <T = unknown>(url: string, data?: unknown, config?: AxiosRequestConfig) => api.post(url, data, config) as Promise<T>,
  put: <T = unknown>(url: string, data?: unknown, config?: AxiosRequestConfig) => api.put(url, data, config) as Promise<T>,
  patch: <T = unknown>(url: string, data?: unknown, config?: AxiosRequestConfig) => api.patch(url, data, config) as Promise<T>,
  delete: <T = unknown>(url: string, config?: AxiosRequestConfig) => api.delete(url, config) as Promise<T>,
}

type Payload = Record<string, unknown>

// ============ 认证 ============
export const login = (data: { username: string; password: string; auth_type?: string }) =>
  http.post<{ token: string; user: User }>('/auth/login', data)
export const getMe = () => http.get<User>('/auth/me')
export const logout = () => http.post('/auth/logout')

// ============ 集群 ============
export const getClusters = () => http.get<Cluster[]>('/clusters')
export const getCluster = (id: number) => http.get<Cluster>(`/clusters/${id}`)
export const createCluster = (data: Payload) => http.post<Cluster>('/clusters', data)
export const updateCluster = (id: number, data: Payload) => http.put<Cluster>(`/clusters/${id}`, data)
export const deleteCluster = (id: number) => http.delete(`/clusters/${id}`)
export const healthCheckCluster = (id: number) => http.post(`/clusters/${id}/health-check`)
// 一键更新凭据：粘贴新 kubeconfig 覆盖（不删集群不丢历史）
export const replaceClusterKubeconfig = (id: number, kubeconfig: string) => http.put(`/clusters/${id}/kubeconfig`, { kubeconfig })
// 解析 kubeconfig 的 context 列表（批量接入前预览）
export const parseClusterKubeconfig = (kubeconfig: string) => http.post('/clusters/parse-kubeconfig', { kubeconfig })
// 多 context 批量接入
export const importMultiClusters = (data: { kubeconfig: string; contexts: string[]; env_tag: string }) => http.post('/clusters/import-multi', data)
// Agent 接入（M3）：生成令牌 + 安装命令
export const agentOnboardCluster = (data: { name: string; env_tag: string; description?: string }) => http.post('/clusters/agent-onboard', data)
export interface AgentUpgradeResult {
  cluster_id: number; ok: boolean; previous_version?: string; new_version?: string
  old_image?: string; image?: string; reconnected?: boolean; unchanged?: boolean; error?: string
}
export const getAgentImage = () => http.get<{ image: string }>('/clusters/agent/image')
export const agentUpgrade = (clusterId: number, data: { image?: string; wait?: number }) =>
  http.post<AgentUpgradeResult>(`/clusters/${clusterId}/agent/upgrade`, data)
export const agentsBatchUpgrade = (data: { cluster_ids: number[]; image?: string; wait?: number }) =>
  http.post<{ image: string; ok: number; total: number; results: AgentUpgradeResult[] }>('/clusters/agents/upgrade', data)
// 吊销 Agent 令牌
export const revokeClusterAgent = (id: number) => http.post(`/clusters/${id}/agent/revoke`)
export const setupClusterRBAC = (id: number) => http.post(`/clusters/${id}/setup-rbac`)
export const inspectCluster = (id: number) => http.post(`/clusters/${id}/inspect`)
export const getInspectionReports = (id: number, limit = 20) => http.get(`/clusters/${id}/inspection-reports`, { params: { limit } })
export const getClusterInspectProgress = (id: number) => http.get<{ pct: number; step: string }>(`/clusters/${id}/inspect/progress`)
// 下载巡检/诊断报告（Markdown 文件）
export const downloadInspectionReport = (reportId: number) =>
  api.get(`/inspection-reports/${reportId}/download`, { responseType: 'blob' }) as unknown as Promise<Blob>

// ============ 告警 ============
export const getAlerts = (params?: PageParams) => http.get<Alert[]>('/alerts', { params })
// 服务端分页版：with_total=true 返回 {items,total,page,page_size}（告警列表用）；
// group 透传后端（默认不传=后端默认 true 合并同类型同服务）
export const getAlertsPaged = (params?: PageParams) => http.get<PageResult<Alert>>('/alerts', { params: { ...params, with_total: true } })
export const getAlert = (id: number) => http.get<Alert>(`/alerts/${id}`)
export const resolveAlert = (id: number) => http.post(`/alerts/${id}/resolve`)
export const manualResolveAlert = (id: number, data: Payload) => http.post(`/alerts/${id}/manual-resolve`, data)
export const batchResolveAlerts = (alertIds: number[], resolution: string) => http.post('/alerts/batch-resolve', { alert_ids: alertIds, resolution })
export const diagnoseAlert = (id: number) => http.post(`/alerts/${id}/diagnose`)
export const getDiagnosis = (id: number) => http.get(`/alerts/${id}/diagnosis`)
export const getAlertDescribe = (id: number) => http.get(`/alerts/${id}/describe`)
// 告警协作（C1）：认领 / 确认 / 备注
export const assignAlert = (id: number, assignee?: string) => http.post(`/alerts/${id}/assign`, { assignee })
export const ackAlert = (id: number, acknowledged = true) => http.post(`/alerts/${id}/ack`, { acknowledged })
export const getAlertComments = (id: number) => http.get<AlertComment[]>(`/alerts/${id}/comments`)
export const addAlertComment = (id: number, content: string) => http.post(`/alerts/${id}/comments`, { content })

// ============ 任务 ============
export const getTasks = (params?: PageParams) => http.get('/tasks', { params })
export const getTask = (id: number) => http.get(`/tasks/${id}`)
export const acknowledgeTask = (id: number, data?: Payload) => http.post(`/tasks/${id}/acknowledge`, data)
export const startTask = (id: number, data?: Payload) => http.post(`/tasks/${id}/start`, data)
export const resolveTask = (id: number, data?: Payload) => http.post(`/tasks/${id}/resolve`, data)

// ============ 通知 ============
export const getNotificationConfigs = () => http.get<NotificationConfig[]>('/notifications/configs')
export const createNotificationConfig = (data: Payload) => http.post('/notifications/configs', data)
export const updateNotificationConfig = (id: number, data: Payload) => http.put(`/notifications/configs/${id}`, data)
export const deleteNotificationConfig = (id: number) => http.delete(`/notifications/configs/${id}`)
export const testNotificationConfig = (id: number) => http.post(`/notifications/configs/${id}/test`)
export const listFeishuChats = (data: Payload) => http.post<{ chat_id: string; name: string }[]>('/notifications/feishu/chats', data)
export const getNotificationLogs = (params?: PageParams) => http.get<PageResult<NotificationLog>>('/notifications/logs', { params })
export const getNotifyPolicy = () => http.get<NotifyPolicy>('/notifications/policy')
export const updateNotifyPolicy = (data: NotifyPolicy) => http.put('/notifications/policy', data)

// ============ 站内消息 ============
export const getUnreadCount = () => http.get<{ count: number }>('/in-app-messages/unread-count')
export const getInAppMessages = (params?: PageParams) => http.get('/in-app-messages', { params })
export const markInAppRead = (id: number) => http.post(`/in-app-messages/${id}/read`)
export const markAllInAppRead = () => http.post('/in-app-messages/read-all')

// ============ AI 模型 ============
export const getAIModels = () => http.get('/ai/models')
export const createAIModel = (data: Payload) => http.post('/ai/models', data)
export const updateAIModel = (id: number, data: Payload) => http.put(`/ai/models/${id}`, data)
export const deleteAIModel = (id: number) => http.delete(`/ai/models/${id}`)
export const setDefaultModel = (id: number) => http.post(`/ai/models/${id}/default`)
export const getAICallLogs = (params?: PageParams) => http.get('/ai/logs', { params })
export const getAICallLogStats = () => http.get('/ai/logs/stats')
export const getAIDashboard = (range_days = 7) => http.get('/stats/ai-dashboard', { params: { range_days } })
export const getAiFeedback = (alertId: number) => http.get<AiFeedbackData>(`/fault-diagnoses/${alertId}/feedback`)
export const addAiFeedback = (alertId: number, rating: number, comment = '') => http.post(`/fault-diagnoses/${alertId}/feedback`, { rating, comment })

// ============ 提示词模板 ============
export const getPromptTemplates = () => http.get('/ai/prompt-templates')
export const createPromptTemplate = (data: Payload) => http.post('/ai/prompt-templates', data)
export const updatePromptTemplate = (id: number, data: Payload) => http.put(`/ai/prompt-templates/${id}`, data)

// ============ 决策 ============
export const evaluateDecision = (planId: number) => http.post('/decisions/evaluate', null, { params: { plan_id: planId } })
export const getDecisionPolicies = () => http.get('/decisions/policies')
export const createDecisionPolicy = (data: Payload) => http.post('/decisions/policies', data)
export const updateDecisionPolicy = (id: number, data: Payload) => http.put(`/decisions/policies/${id}`, data)

// ============ 审批 ============
export const getApprovals = (params?: PageParams) => http.get<PageResult<Approval>>('/approvals', { params })
export const batchDecideApprovals = (ids: number[], action: 'approve' | 'reject', comment = '') =>
  http.post<BatchDecideResult>('/approvals/batch-decide', { ids, action, comment })
export const addApprover = (id: number, username: string) => http.post<OpResult>(`/approvals/${id}/add-approver`, { username })
export const transferApproval = (id: number, to_username: string) => http.post<OpResult>(`/approvals/${id}/transfer`, { to_username })
export const getApprovalCommentTemplates = () => http.get<string[]>('/approvals/comment-templates')
export const updateApprovalCommentTemplates = (templates: string[]) => http.put('/approvals/comment-templates', { templates })
export const getApproval = (id: number) => http.get<Approval>(`/approvals/${id}`)
export const createApproval = (data: Payload) => http.post('/approvals', data)
export const decideApproval = (id: number, data: Payload) => http.post(`/approvals/${id}/decide`, data)
export const getApprovalFlows = () => http.get('/approval-flows')
export const createApprovalFlow = (data: Payload) => http.post('/approval-flows', data)
export const updateApprovalFlow = (id: number, data: Payload) => http.put(`/approval-flows/${id}`, data)
export const deleteApprovalFlow = (id: number) => http.delete(`/approval-flows/${id}`)
export const resetDefaultApprovalFlow = () => http.post('/approval-flows/reset-default')
export const getIMGroups = () => http.get('/im-groups')
export const createIMGroup = (data: Payload) => http.post('/im-groups', data)
export const updateIMGroup = (id: number, data: Payload) => http.put(`/im-groups/${id}`, data)
export const deleteIMGroup = (id: number) => http.delete(`/im-groups/${id}`)
export const getResourceAlertRules = () => http.get('/resource-alert-rules')
export const createResourceAlertRule = (data: Payload) => http.post('/resource-alert-rules', data)
export const updateResourceAlertRule = (id: number, data: Payload) => http.put(`/resource-alert-rules/${id}`, data)
export const deleteResourceAlertRule = (id: number) => http.delete(`/resource-alert-rules/${id}`)
export const runDbDiagnosis = (connId: number) => http.post(`/db-diagnosis/${connId}/run`)
export const getDbDiagnosisReports = (page = 1, pageSize = 20) => http.get<PageResult<{ id: number; conclusion: string; root_cause: string; suggestion: string; created_at: string }>>('/db-diagnosis/reports', { params: { page, page_size: pageSize } })
export const getDbTrends = (connId: number, metricType: string, hours = 1) => http.get(`/db-diagnosis/${connId}/trends`, { params: { metric_type: metricType, hours } })
export const getDbDiagnosisProgress = (connId: number) => http.get<{ pct: number; step: string; ts: number }>(`/db-diagnosis/${connId}/progress`)
export const getDbLatestMetrics = (connId: number) =>
  http.get<Array<{ metric_type: string; metric_name: string; value: number; detail: string; sampled_at: string }>>(`/db-diagnosis/${connId}/metrics`)

// ============ 执行进度（轮询）============
export const getExecutionStatus = (planId: number, sinceStep: number = 0) =>
  http.get(`/executions/${planId}`, { params: { since_step: sinceStep } })
export const rollbackExecution = (planId: number) => http.post(`/executions/${planId}/rollback`)

// ============ 批量 ============
export const getBatchList = () => http.get('/batch')
export const getBatchDetail = (id: number) => http.get(`/batch/${id}`)
export const createBatch = (data: Payload) => http.post('/batch', data)
export const executeBatch = (id: number) => http.post(`/batch/${id}/execute`)

// ============ 定时任务 ============
export const getCustomTasks = () => http.get<CustomTask[]>('/custom-tasks')
export const createCustomTask = (data: Payload) => http.post('/custom-tasks', data)
export const updateCustomTask = (id: number, data: Payload) => http.put(`/custom-tasks/${id}`, data)
export const deleteCustomTask = (id: number) => http.delete(`/custom-tasks/${id}`)
export const toggleCustomTask = (id: number) => http.post(`/custom-tasks/${id}/toggle`)
export const runCustomTaskNow = (id: number) => http.post(`/custom-tasks/${id}/run-now`)
export const getCustomTaskLogs = (id: number) => http.get(`/custom-tasks/${id}/logs`)

// ============ 资源 ============
export const getResourceOverview = (clusterId: number) => http.get(`/resources/clusters/${clusterId}/overview`)
export const getClusterDetail = (clusterId: number) => http.get(`/resources/clusters/${clusterId}/detail`)
export const getClusterNamespaces = (clusterId: number) => http.get<string[]>(`/resources/clusters/${clusterId}/namespace-list`)
export const getNamespacePods = (clusterId: number, ns: string) => http.get(`/resources/clusters/${clusterId}/namespaces/${ns}/pods`)
export const getPodContainers = (clusterId: number, ns: string, pod: string) => http.get<{ containers: string[] }>(`/resources/clusters/${clusterId}/namespaces/${ns}/pods/${pod}/containers`)
export const getWorkloadEvents = (clusterId: number, params?: PageParams) => http.get(`/resources/clusters/${clusterId}/workload-events`, { params })
export const getNamespaces = (clusterId: number) => http.get(`/resources/clusters/${clusterId}/namespaces`)
export const getNamespaceDeployments = (clusterId: number, ns: string) => http.get(`/resources/clusters/${clusterId}/namespaces/${ns}/deployments`)
export const triggerCollect = (clusterId: number) => http.post(`/resources/clusters/${clusterId}/collect`)

// ============ 知识库 ============
export const searchKnowledge = (q: string, topK: number = 5) => http.get('/knowledge/search', { params: { q, top_k: topK } })
export const getKnowledgeList = () => http.get<KnowledgeEntry[]>('/knowledge')
export const getKnowledgeStats = () => http.get('/knowledge/stats')
export const exportKnowledge = () => http.get('/knowledge/export')
export const importKnowledge = (items: Record<string, unknown>[]) => http.post('/knowledge/import', { items })
export const setKnowledgeStatus = (id: number, is_active: boolean) => http.post(`/knowledge/${id}/status`, { is_active })
export const addKnowledge = (data: Payload) => http.post('/knowledge', data)
export const updateKnowledge = (id: number, data: Payload) => http.put(`/knowledge/${id}`, data)
export const deleteKnowledge = (id: number) => http.delete(`/knowledge/${id}`)
export const dedupKnowledge = () => http.post('/knowledge/dedup')

// ============ 审计 ============
export const getAuditTrace = (traceId: string) => http.get(`/audit/trace/${traceId}`)
export const searchAudit = (params: PageParams) => http.get('/audit/search', { params })
export const verifyAuditChain = () => http.post('/audit/verify-chain')

// ============ 统计 ============

export interface DashboardParams {
  range?: string
  /** 自定义时间段（与 range 二选一；同时提供时后端优先用 start/end） */
  start?: string
  end?: string
  cluster_id?: number
  /** 后端仍支持，但仪表盘按老板口径只按集群维度，不再传项目 */
  project_id?: number
  /** 环境筛选（qa/release/prod），按该环境的集群过滤 */
  env_tag?: string
  /** AI 诊断结果分布窗口：24h / 7d / 30d / follow(跟随上方时间筛选，默认) */
  ai_range?: string
}

export const getDashboard = (params?: DashboardParams) =>
  http.get<DashboardData>('/stats/dashboard', { params })
export const getPlatformHealth = () => http.get<PlatformHealth>('/stats/platform-health')
export const getAIEffectiveness = (range: string = '7d') => http.get('/stats/ai-effectiveness', { params: { range } })
export const getModelCost = (range: string = '7d') => http.get('/stats/model-cost', { params: { range } })
// 报表定时推送（C4）
export const getReportSchedules = () => http.get('/report-schedules')
export const createReportSchedule = (data: Payload) => http.post('/report-schedules', data)
export const updateReportSchedule = (id: number, data: Payload) => http.put(`/report-schedules/${id}`, data)
export const deleteReportSchedule = (id: number) => http.delete(`/report-schedules/${id}`)
export const runReportSchedule = (id: number) => http.post(`/report-schedules/${id}/run`)
export const previewReport = (range_days = 7) => http.get('/report-schedules/preview', { params: { range_days } })

// ============ 用户 ============
export const getUsers = () => http.get<User[]>('/users')
export const createUser = (data: Payload) => http.post('/users', data)
export const updateUser = (id: number, data: Payload) => http.put(`/users/${id}`, data)
export const deleteUser = (id: number) => http.delete(`/users/${id}`)
export const regenerateAPIKey = (id: number) => http.post(`/users/${id}/api-key`)

// ============ 日志查询（ES）============
export const searchLogs = (params: PageParams) => http.get('/logs/search', { params })

// ============ 指标监控 ============
// R15 工作负载级监控（Deployment × 容器，跨 Pod 拼接；只读 Prometheus）
export const getWorkloadMetrics = (clusterId: number, kind: string, namespace: string, name: string, range = '1h') =>
  http.get(`/monitoring/clusters/${clusterId}/workloads/${kind}/${encodeURIComponent(namespace)}/${encodeURIComponent(name)}/metrics`, { params: { range } })
export const getPodMetrics = (clusterId: number, ns: string, pod: string, range = '1h') =>
  http.get(`/resources/clusters/${clusterId}/namespaces/${ns}/pods/${pod}/metrics`, { params: { range } })

export const getPodLogs = (clusterId: number, ns: string, pod: string, container = '', tail = 200, previous = false) =>
  http.get(`/resources/clusters/${clusterId}/namespaces/${ns}/pods/${pod}/logs`, { params: { container, tail, previous } })
// 容器内日志文件（只读）：列目录 / 读取文件内容（下载）
export const listPodFiles = (clusterId: number, ns: string, pod: string, dir: string, container = '') =>
  http.get(`/resources/clusters/${clusterId}/namespaces/${ns}/pods/${pod}/files`, { params: { dir, container } })
export const readPodFile = (clusterId: number, ns: string, pod: string, path: string, container = '', maxBytes?: number) =>
  http.get(`/resources/clusters/${clusterId}/namespaces/${ns}/pods/${pod}/file`, { params: { path, container, max_bytes: maxBytes } })

export const getPodYaml = (clusterId: number, ns: string, pod: string) =>
  http.get(`/resources/clusters/${clusterId}/namespaces/${ns}/pods/${pod}/yaml`)
export const applyPodYaml = (clusterId: number, ns: string, pod: string, yaml: string) =>
  http.put(`/resources/clusters/${clusterId}/namespaces/${ns}/pods/${pod}/yaml`, { yaml })
export const execPod = (clusterId: number, ns: string, pod: string, command: string, container = '') =>
  http.post(`/resources/clusters/${clusterId}/namespaces/${ns}/pods/${pod}/exec`, { command, container })

// ============ 工作负载运维（专项 D）============
export const getWorkloads = (clusterId: number, namespace = '') =>
  http.get<Workload[]>(`/resources/clusters/${clusterId}/workloads`, { params: { namespace } })
export const scaleWorkload = (clusterId: number, data: Payload) =>
  http.post(`/resources/clusters/${clusterId}/workloads/scale`, data)
export interface BatchWorkloadItem { namespace: string; name: string }
export interface BatchWorkloadResult {
  results: { namespace: string; name: string; success: boolean; message: string }[]
  ok: number
  total: number
}
export const batchWorkloadOp = (clusterId: number, data: {
  kind: string; action: 'delete' | 'stop' | 'start' | 'scale' | 'restart'
  items: BatchWorkloadItem[]; replicas?: number | null; confirm?: boolean
}) => http.post<BatchWorkloadResult>(`/resources/clusters/${clusterId}/workloads/batch`, data)
export const rolloutWorkload = (clusterId: number, data: Payload) =>
  http.post(`/resources/clusters/${clusterId}/workloads/rollout`, data)
export const getRolloutHistory = (clusterId: number, ns: string, name: string) =>
  http.get(`/resources/clusters/${clusterId}/workloads/${ns}/${name}/history`)
export const updateWorkloadProbe = (clusterId: number, data: Payload) =>
  http.post(`/resources/clusters/${clusterId}/workloads/probe`, data)
export const recreateWorkload = (clusterId: number, data: Payload) =>
  http.post(`/resources/clusters/${clusterId}/workloads/recreate`, data)
// 工作负载（整个服务）YAML：查看 / 应用
export const getWorkloadYaml = (clusterId: number, kind: string, ns: string, name: string) =>
  http.get(`/resources/clusters/${clusterId}/workloads/${kind}/${ns}/${name}/yaml`)
export const getWorkloadServiceInfo = (clusterId: number, kind: string, ns: string, name: string) =>
  http.get(`/resources/clusters/${clusterId}/workloads/${kind}/${ns}/${name}/service-info`)
export const getWorkloadContainers = (clusterId: number, kind: string, ns: string, name: string) =>
  http.get<{ containers: { name: string; image: string }[] }>(`/resources/clusters/${clusterId}/workloads/${kind}/${ns}/${name}/containers`)
export const applyWorkloadYaml = (clusterId: number, kind: string, ns: string, name: string, yaml: string) =>
  http.put(`/resources/clusters/${clusterId}/workloads/${kind}/${ns}/${name}/yaml`, { yaml })
// 创建/编辑工作负载（name 留空=按 YAML 内 metadata.name 创建）
export const applyWorkload = (clusterId: number, data: Payload) =>
  http.post(`/resources/clusters/${clusterId}/workloads/apply`, data)
// 删除工作负载（deployment/statefulset/daemonset/cronjob/pod）
export const deleteWorkload = (clusterId: number, kind: string, ns: string, name: string) =>
  http.delete(`/resources/clusters/${clusterId}/workloads/${kind}/${ns}/${name}`)
// 集群 Pod 实时列表（含创建时间）：namespace 空=全部
export interface ClusterPod { namespace: string; name: string; phase?: string; ready?: boolean; restart_count?: number; created_at?: string; node_name?: string; pod_ip?: string; containers?: string[]; init_containers?: string[]; status?: string; ready_containers?: number; total_containers?: number }
export const getClusterPods = (clusterId: number, namespace = '') =>
  http.get<{ pods: ClusterPod[] }>(`/resources/clusters/${clusterId}/pods`, { params: { namespace } })
// 单个 Pod 的事件（k8s Event）
export const getPodEvents = (clusterId: number, ns: string, pod: string) =>
  http.get(`/resources/clusters/${clusterId}/namespaces/${ns}/pods/${pod}/events`)
// 工作负载维度的历史事件（平台自存 k8s_events，默认最近 7 天；含其下所有 Pod 的事件）
export interface WorkloadEventRow {
  type: string; reason: string; message: string; count: number
  resource_kind: string; resource_name: string; first_timestamp: string; last_timestamp: string
}
export const getWorkloadEventHistory = (clusterId: number, kind: string, ns: string, name: string, hours = 168) =>
  http.get<{ hours: number; events: WorkloadEventRow[] }>(
    `/resources/clusters/${clusterId}/namespaces/${ns}/workloads/${kind}/${name}/events`, { params: { hours } })
// Pod 详情（含容器组内容）
export const getPodDetail = (clusterId: number, ns: string, pod: string) =>
  http.get(`/resources/clusters/${clusterId}/namespaces/${ns}/pods/${pod}/detail`)
// 工作负载挂载/环境变量编辑
export interface EditMountPayload { volume_name: string; mount_path?: string; sub_path?: string; read_only?: boolean; containers?: string[]; source_type?: string; source_name?: string }
export const editWorkloadMount = (clusterId: number, kind: string, ns: string, name: string, data: EditMountPayload) =>
  http.put(`/resources/clusters/${clusterId}/workloads/${kind}/${ns}/${name}/mounts`, data)
// 单个环境变量新增/编辑
export interface EnvPayload { container: string; name: string; old_name?: string; value?: string; value_from?: { type: 'configMap' | 'secret' | 'field' | 'resource'; name?: string; key?: string; field_path?: string; resource?: string } }
export const upsertWorkloadEnv = (clusterId: number, kind: string, ns: string, name: string, data: EnvPayload) =>
  http.post(`/resources/clusters/${clusterId}/workloads/${kind}/${ns}/${name}/env`, data)
// 批量替换某容器环境变量编辑器整份保存）
export interface EnvEntryRow { name?: string; value?: string; value_from?: EnvPayload['value_from'] }
export const replaceWorkloadEnv = (clusterId: number, kind: string, ns: string, name: string, data: { container: string; envs: EnvEntryRow[] }) =>
  http.put(`/resources/clusters/${clusterId}/workloads/${kind}/${ns}/${name}/env`, data)
export const deleteWorkloadEnv = (clusterId: number, kind: string, ns: string, name: string, params: { container: string; var_name: string }) =>
  http.delete(`/resources/clusters/${clusterId}/workloads/${kind}/${ns}/${name}/env`, { params })
// 工作负载 Pod 模板的挂载配置（volumes + 各容器 volumeMounts）
export const getWorkloadMounts = (clusterId: number, kind: string, ns: string, name: string) =>
  http.get(`/resources/clusters/${clusterId}/workloads/${kind}/${ns}/${name}/mounts`)
// 给工作负载挂载 配置字典/密钥/PVC（mode=volume 挂载为卷；mode=env 注入环境变量 envFrom）
export interface AddMountPayload { mode?: 'volume' | 'env'; source_type: 'configMap' | 'secret' | 'pvc' | 'emptyDir'; source_name?: string; mount_path?: string; sub_path?: string; read_only?: boolean; volume_name?: string; containers?: string[] }
export const addWorkloadMount = (clusterId: number, kind: string, ns: string, name: string, data: AddMountPayload) =>
  http.post(`/resources/clusters/${clusterId}/workloads/${kind}/${ns}/${name}/mounts`, data)
// 移除工作负载挂载/环境变量注入
export const removeWorkloadMount = (clusterId: number, kind: string, ns: string, name: string, params: { mode?: 'volume' | 'env'; volume_name?: string; source_name?: string; container?: string; mount_path?: string }) =>
  http.delete(`/resources/clusters/${clusterId}/workloads/${kind}/${ns}/${name}/mounts`, { params })
// 集群管理其它资源：服务/路由/ConfigMap/Secret/存储(PVC)
export const getMiscResources = (clusterId: number, namespace = '') =>
  http.get(`/resources/clusters/${clusterId}/misc-resources`, { params: { namespace } })
// 配置类对象（ConfigMap/Secret）YAML：查看 / 创建·编辑
export const getObjectYaml = (clusterId: number, kind: string, ns: string, name: string) =>
  http.get(`/resources/clusters/${clusterId}/objects/${kind}/${ns}/${name}/yaml`)
export const applyObjectYaml = (clusterId: number, kind: string, yaml: string) =>
  http.put(`/resources/clusters/${clusterId}/objects/${kind}/yaml`, { yaml })
export const deleteObject = (clusterId: number, kind: string, ns: string, name: string) =>
  http.delete(`/resources/clusters/${clusterId}/objects/${kind}/${ns}/${name}`)
export const getObjectDetail = (clusterId: number, kind: string, ns: string, name: string) =>
  http.get(`/resources/clusters/${clusterId}/objects/${kind}/${ns}/${name}/detail`)
export const updateObjectMetadata = (clusterId: number, kind: string, ns: string, name: string, labels: Record<string, string>, annotations: Record<string, string>) =>
  http.put(`/resources/clusters/${clusterId}/objects/${kind}/${ns}/${name}/metadata`, { labels, annotations })
export const updateObjectData = (clusterId: number, kind: string, ns: string, name: string, data: Record<string, string>, type?: string) =>
  http.put(`/resources/clusters/${clusterId}/objects/${kind}/${ns}/${name}/data`, { data, type })
export const updateObjectSpec = (clusterId: number, kind: string, ns: string, name: string, spec: Record<string, unknown>) =>
  http.put(`/resources/clusters/${clusterId}/objects/${kind}/${ns}/${name}/spec`, { spec })
export const applyObject = (clusterId: number, kind: string, object: Record<string, unknown>) =>
  http.post(`/resources/clusters/${clusterId}/objects/${kind}/apply`, { object })

// ============ 告警策略 ============
export const getAlertPolicies = () => http.get('/alert-policies')
export const createAlertPolicy = (data: Payload) => http.post('/alert-policies', data)
export const updateAlertPolicy = (id: number, data: Payload) => http.put(`/alert-policies/${id}`, data)
export const deleteAlertPolicy = (id: number) => http.delete(`/alert-policies/${id}`)

// ============ 日志告警规则 ============
export const getLogAlertRules = () => http.get('/log-alert-rules')
export const createLogAlertRule = (data: Payload) => http.post('/log-alert-rules', data)
export const updateLogAlertRule = (id: number, data: Payload) => http.put(`/log-alert-rules/${id}`, data)
export const deleteLogAlertRule = (id: number) => http.delete(`/log-alert-rules/${id}`)

// ============ 日志源（ES 多实例） ============
export const getLogSources = () => http.get<LogSource[]>('/log-sources')
export const createLogSource = (data: Payload) => http.post<LogSource>('/log-sources', data)
export const updateLogSource = (id: number, data: Payload) => http.put<LogSource>(`/log-sources/${id}`, data)
export const deleteLogSource = (id: number) => http.delete(`/log-sources/${id}`)
export const testLogSource = (data: Payload) => http.post<{ success: boolean; message: string; status_code: number }>('/log-sources/test', data)
export const testLogSourceById = (id: number) => http.post<{ success: boolean; message: string; status_code: number }>(`/log-sources/${id}/test`)

// ============ 值班排班 ============
export const getOnDuty = () => http.get('/on-duty')
export const createOnDuty = (data: Payload) => http.post('/on-duty', data)
export const updateOnDuty = (id: number, data: Payload) => http.put(`/on-duty/${id}`, data)
export const deleteOnDuty = (id: number) => http.delete(`/on-duty/${id}`)
export const getHolidays = () => http.get('/holidays')
// 值班日历 / 换班代班（C6）
export const getDutyCalendar = (params?: PageParams) => http.get('/on-duty/calendar', { params })
export const getDutyOverrides = (params?: PageParams) => http.get<DutyOverride[]>('/on-duty/overrides', { params })
export const createDutyOverride = (data: Payload) => http.post('/on-duty/overrides', data)
export const deleteDutyOverride = (id: number) => http.delete(`/on-duty/overrides/${id}`)
export const createHoliday = (data: Payload) => http.post('/holidays', data)
export const deleteHoliday = (id: number) => http.delete(`/holidays/${id}`)

// ============ 处理记录（任务+审批合并）============
export const getRepairRecords = (params?: PageParams) => http.get('/repair-records', { params })

export const getSelfHealRules = () => http.get('/self-heal-rules')
export const createSelfHealRule = (data: Payload) => http.post('/self-heal-rules', data)
export const updateSelfHealRule = (id: number, data: Payload) => http.put(`/self-heal-rules/${id}`, data)
export const deleteSelfHealRule = (id: number) => http.delete(`/self-heal-rules/${id}`)

// ============ 系统设置 ============
export const getSettings = () => http.get<Record<string, unknown>>('/settings')
export const updateSettings = (data: Payload) => http.put('/settings', data)
// Embedding 配置连通性测试（可传未保存配置做「先测再存」）
export const testEmbedding = (data?: Payload) => http.post('/settings/embedding/test', data ?? {})
export const changePassword = (data: Payload) => http.post('/auth/change-password', data)
export const getExternalToken = () => http.get('/external/tickets/token')
export const regenerateExternalToken = () => http.post('/external/tickets/token/regenerate')

// ============ AI 诊断助手（会话式 Agent）============
export const createAgentSession = (data: Payload) => http.post('/agent/sessions', data)
// 用已有诊断结果（如 JVM 综合诊断）落一个会话（同目标复用）
export const seedAgentSession = (data: Payload) => http.post<{ id: number; title: string; created: boolean }>('/agent/sessions/seed', data)
export const getAgentSessions = () => http.get('/agent/sessions')
export interface ProactiveScanResult {
  skipped?: boolean; reason?: string; clusters?: number; scanned_workloads?: number
  hits?: number; diagnosed?: number; skipped_open_alert?: number; skipped_cooldown?: number
  sessions?: unknown[]
  items?: Array<{ id: number; cluster_id: number; namespace: string; pod: string; workload?: string; kind?: string; reason: string }>
}
export const getProactiveStatus = () => http.get<Record<string, unknown>>('/agent/proactive/status')
export const runProactiveScan = (data?: { cluster_id?: number; namespace?: string; kinds?: string[] }) =>
  http.post<ProactiveScanResult>('/agent/proactive/scan', data || {})

// ============ 监控数据源（按集群覆盖 Prometheus 地址）============
export interface MonitoringSource { cluster_id: number; cluster_name: string; url: string }
export const getMonitoringSources = () => http.get<MonitoringSource[]>('/monitoring/sources')
export const setMonitoringSource = (cluster_id: number, url: string) =>
  http.put<{ cluster_id: number; url: string }>('/monitoring/sources', { cluster_id, url })
export const getAgentMessages = (id: number) => http.get(`/agent/sessions/${id}/messages`)
export const sendAgentMessage = (id: number, content: string, deep = false) => http.post(`/agent/sessions/${id}/messages`, { content, deep })
export const closeAgentSession = (id: number) => http.post(`/agent/sessions/${id}/close`)
// 会话定时采集开关（针对绑定的容器周期采集信息追加到会话）
export const updateSessionCollect = (id: number, data: Payload) =>
  http.put<{ id: number; collect_enabled: boolean; collect_interval_min: number; collect_items: string; collect_last_at: string | null }>(`/agent/sessions/${id}/collect`, data)
export const deleteAgentSession = (id: number) => http.delete<{ id: number }>(`/agent/sessions/${id}`)

// ============ 诊断中心（任务/报告）============
export const getDiagnosticTasks = () => http.get('/diagnostic-tasks')
export const createDiagnosticTask = (data: Payload) => http.post('/diagnostic-tasks', data)
export const deleteDiagnosticTask = (id: number) => http.delete(`/diagnostic-tasks/${id}`)
export const runDiagnosticTask = (id: number) => http.post(`/diagnostic-tasks/${id}/run`)
export const getDiagnosticReports = (taskId?: number) => http.get('/diagnostic-reports', { params: taskId ? { task_id: taskId } : {} })

// ============ 故障诊断（针对告警的 AI 诊断）============
export const getFaultDiagnoses = (params?: PageParams & { category?: string }) => http.get<FaultDiagnosisItem[]>('/fault-diagnoses', { params })
export const getFaultDiagnosesPaged = (params?: PageParams & { category?: string }) =>
  http.get<PageResult<FaultDiagnosisItem>>('/fault-diagnoses', { params: { ...params, with_total: true } })
export const getFaultDiagnosis = (alertId: number) => http.get<FaultDiagnosisItem>(`/fault-diagnoses/${alertId}`)
// 诊断失败记录 + 诊断质量（A-3 / B1，2026-10-29）
export const getDiagnosisFailures = (params?: { page?: number; page_size?: number; days?: number }) =>
  http.get('/fault-diagnoses/failures', { params })
export const getDiagnosisFailureStats = (days = 7) =>
  http.get('/fault-diagnoses/failures/stats', { params: { days } })
export const getDiagnosisQuality = (days = 7) =>
  http.get('/fault-diagnoses/quality', { params: { days } })

// ============ 数据库连接管理 ============
// R6：agent 库只读查询（平台只发 key+SQL，凭据在 agent 本地）
export const queryDBConnection = (id: number, sql: string) =>
  http.post<{ columns?: string[]; rows?: unknown[][]; truncated?: boolean }>(`/db-connections/${id}/query`, { sql })
// 执行 SQL（DML/DDL，仅 admin；confirm=true 二次确认）
export const execDBConnection = (id: number, sql: string) =>
  http.post<{ columns?: string[]; rows?: unknown[][]; affected_rows?: number; write?: boolean }>(`/db-connections/${id}/exec`, { sql, confirm: true })
// 数据库对象：DDL / 导出 / 导入（MySQL）
export const getObjectDDL = (id: number, kind: string, name: string, db?: string) =>
  http.get<{ ddl?: string }>(`/db-connections/${id}/object-ddl`, { params: { kind, name, db } })
export const exportDBObjects = (id: number, data: Record<string, unknown>) =>
  http.post<{ sql?: string; tables?: number; rows?: number; truncated?: boolean; bytes?: number }>(`/db-connections/${id}/export`, data)
export const importDBObjects = (id: number, data: Record<string, unknown>) =>
  http.post<{ executed?: number; skipped?: number }>(`/db-connections/${id}/import`, data)
// 数据库实例分组
export interface DbGroupRow { id: number; name: string; conn_count: number }
export const listDbGroups = () => http.get<DbGroupRow[]>('/db-connections/groups')
export const createDbGroup = (name: string) => http.post<DbGroupRow>('/db-connections/groups', { name })
export const renameDbGroup = (id: number, name: string) => http.put(`/db-connections/groups/${id}`, { name })
export const deleteDbGroup = (id: number) => http.delete(`/db-connections/groups/${id}`)
// SQL 定时任务（每月跑 SQL → 发邮件）
export interface SqlTaskRow {
  id: number; name: string; db_connection_id: number; sql: string
  cron_expression: string; recipients: string; is_enabled: boolean
  last_run_at?: string; last_status?: string; last_error?: string
}
export const getSqlTaskCounts = () =>
  http.get<{ db_connection_id: number; total: number; enabled: number }[]>('/db-connections/sql-task-counts')
export const listSqlTasks = (connId: number) => http.get<SqlTaskRow[]>(`/db-connections/${connId}/sql-tasks`)
export const createSqlTask = (connId: number, data: Record<string, unknown>) =>
  http.post<SqlTaskRow>(`/db-connections/${connId}/sql-tasks`, data)
export const updateSqlTask = (connId: number, taskId: number, data: Record<string, unknown>) =>
  http.put<SqlTaskRow>(`/db-connections/${connId}/sql-tasks/${taskId}`, data)
export const deleteSqlTask = (connId: number, taskId: number) =>
  http.delete(`/db-connections/${connId}/sql-tasks/${taskId}`)
export const runSqlTask = (connId: number, taskId: number, dryRun = false) =>
  http.post<{ ok?: boolean; error?: string; rows?: number; preview?: unknown[] }>(
    `/db-connections/${connId}/sql-tasks/${taskId}/run`, { dry_run: dryRun })
// SQL 编辑器补全：当前账号可见的 databases + tables（agent 库）
export const getDBSchema = (id: number) =>
  http.get<{ databases?: string[]; tables?: { db: string; name: string; type?: string }[] }>(`/db-connections/${id}/schema`)
// 查询结果导出 CSV（≤5000 行）
export const downloadDBQuery = (id: number, sql: string) =>
  http.post<Blob>(`/db-connections/${id}/query/download`, { sql }, { responseType: 'blob' })
export const getDBConnections = () => http.get('/db-connections')
export const createDBConnection = (data: Payload) => http.post('/db-connections', data)
export const updateDBConnection = (id: number, data: Payload) => http.put(`/db-connections/${id}`, data)
export const deleteDBConnection = (id: number) => http.delete(`/db-connections/${id}`)
export const testDBConnection = (id: number) => http.post(`/db-connections/${id}/test`)
export const getSlowSQL = (connId: number, limit = 50) => http.get(`/db-connections/${connId}/slow-sql`, { params: { limit } })
export const getSlowSQLTrend = (connId: number, hours = 24) => http.get(`/db-connections/${connId}/slow-sql-trend`, { params: { hours } })
export const collectSlowSQL = (connId: number) => http.post(`/db-connections/${connId}/collect`)

// ============ 权限管理（admin）============
export const getUserClusterPerms = (userId: number) => http.get(`/permissions/${userId}/clusters`)
export const addUserClusterPerm = (userId: number, data: Payload) => http.post(`/permissions/${userId}/clusters`, data)
export const deleteUserClusterPerm = (permId: number) => http.delete(`/permissions/clusters/${permId}`)
export const getUserDBPerms = (userId: number) => http.get(`/permissions/${userId}/db`)
export const addUserDBPerm = (userId: number, data: Payload) => http.post(`/permissions/${userId}/db`, data)
export const deleteUserDBPerm = (permId: number) => http.delete(`/permissions/db/${permId}`)
export const getUserProjects = (userId: number) => http.get(`/permissions/${userId}/projects`)

// ============ 项目（应用）管理 ============
export const getProjects = () => http.get('/projects')
export const createProject = (data: Payload) => http.post('/projects', data)
export const updateProject = (id: number, data: Payload) => http.put(`/projects/${id}`, data)
export const deleteProject = (id: number) => http.delete(`/projects/${id}`)
export const getProjectClusterNS = (id: number) => http.get(`/projects/${id}/cluster-ns`)
export const addProjectClusterNS = (id: number, data: Payload) => http.post(`/projects/${id}/cluster-ns`, data)
export const deleteProjectClusterNS = (id: number, itemId: number) => http.delete(`/projects/${id}/cluster-ns/${itemId}`)
export const getProjectDBs = (id: number) => http.get(`/projects/${id}/db`)
export const addProjectDB = (id: number, data: Payload) => http.post(`/projects/${id}/db`, data)
export const deleteProjectDB = (id: number, itemId: number) => http.delete(`/projects/${id}/db/${itemId}`)
export const getProjectUsers = (id: number) => http.get(`/projects/${id}/users`)
export const addProjectUser = (id: number, data: Payload) => http.post(`/projects/${id}/users`, data)
export const addProjectUsers = (id: number, data: Payload) => http.post(`/projects/${id}/users/batch`, data)
export const deleteProjectUser = (id: number, permId: number) => http.delete(`/projects/${id}/users/${permId}`)

// ============ 告警订阅（用户自配）============
export const getSubscriptions = () => http.get('/subscriptions')
export const addSubscription = (data: Payload) => http.post('/subscriptions', data)
export const deleteSubscription = (id: number) => http.delete(`/subscriptions/${id}`)

// ============ 风险评分策略 ============
export const getRiskPolicy = () => http.get('/decisions/risk-policy')
export const updateRiskPolicy = (data: Payload) => http.put('/decisions/risk-policy', data)

// ============ 工单（N1） ============
export const getTickets = (params?: PageParams) => http.get<PageResult<Ticket>>('/tickets', { params })
export const getTicketStats = (year?: number) => http.get<TicketStats>('/tickets/stats', { params: year ? { year } : {} })
export const getTicket = (id: number) => http.get<TicketDetail>(`/tickets/${id}`)
export const createTicket = (data: Payload) => http.post<Ticket>('/tickets', data)
export const createTicketFromAlert = (alertId: number) => http.post<Ticket>(`/tickets/from-alert/${alertId}`)
export const createTicketFromApproval = (approvalId: number) => http.post<Ticket>(`/tickets/from-approval/${approvalId}`)
export const updateTicket = (id: number, data: Payload) => http.patch<Ticket>(`/tickets/${id}`, data)
export const assignTicket = (id: number, assigneeId: number | null) => http.post<Ticket>(`/tickets/${id}/assign`, { assignee_id: assigneeId })
export const addTicketComment = (id: number, content: string) => http.post<TicketComment>(`/tickets/${id}/comments`, { content })
export const deleteTicket = (id: number) => http.delete<{ id: number }>(`/tickets/${id}`)
export const getTicketComments = (id: number) => http.get<TicketComment[]>(`/tickets/${id}/comments`)

// ============ JVM 诊断（P0-R2） ============
export interface JvmAction { action: string; label: string; desc: string; file: boolean }
export interface JvmProcess { pid: number; target: string; is_jcmd: boolean }
export interface JvmDiagResult {
  action: string; command: string; pid: string | null; output: string
  file: boolean; file_path?: string; hint?: string; backend?: string
}

export const getJvmActions = () => http.get<JvmAction[]>('/jvm/actions')
export const getJvmProcesses = (clusterId: number, ns: string, pod: string, container = '') =>
  http.get<{ raw: string; processes: JvmProcess[]; backend?: string; hint?: string }>(`/jvm/clusters/${clusterId}/namespaces/${ns}/pods/${pod}/processes`, { params: { container } })
export const jvmDiagnose = (clusterId: number, ns: string, pod: string, payload: Payload) =>
  http.post<JvmDiagResult>(`/jvm/clusters/${clusterId}/namespaces/${ns}/pods/${pod}/diagnose`, payload, { timeout: 180000 })
// JVM 综合诊断（把已采集的 JVM 证据发 AI 做应用健康诊断）
export interface JvmAiAnalysis { root_cause?: string; suggestions?: string[]; risk_level?: string; confidence?: number; model_used?: string }
export const jvmAiAnalyze = (clusterId: number, ns: string, pod: string, payload: { evidence: string; container?: string; severity?: string }) =>
  http.post<JvmAiAnalysis>(`/jvm/clusters/${clusterId}/namespaces/${ns}/pods/${pod}/ai-analyze`, payload, { timeout: 300000 })
export interface JvmCollectItem { action: string; label: string; ok: boolean; output: string; message?: string }
export interface JvmAiAnalyzeAll { items: JvmCollectItem[]; analysis: JvmAiAnalysis | null }
// 一键：自动跑完所有轻量 JVM 动作 → 汇总证据发 AI 综合诊断
export const jvmAiAnalyzeAll = (clusterId: number, ns: string, pod: string, payload: { container?: string; pid?: string | number; severity?: string }) =>
  http.post<JvmAiAnalyzeAll>(`/jvm/clusters/${clusterId}/namespaces/${ns}/pods/${pod}/ai-analyze-all`, payload, { timeout: 300000 })

export interface JvmParams { xms: string | null; xmx: string | null; xss: string | null; max_metaspace: string | null; others: string[] }
export interface JvmParamsContainer { container: string; image: string; params: JvmParams; command: string[]; args: string[] }
export const getJvmParams = (clusterId: number, ns: string, deployment: string, container = '') =>
  http.get<{ deployment: string; namespace: string; containers: JvmParamsContainer[] }>(
    `/jvm/clusters/${clusterId}/namespaces/${ns}/deployments/${deployment}/params`, { params: { container } })

// R2.3 JVM 堆/GC 指标
export interface JvmMetricItem { metric: Record<string, string>; value: number }
export interface JvmMetrics {
  metrics: Record<string, { label: string; unit: string; items: JvmMetricItem[] }>
  connected: boolean; hint: string
}
export const getJvmMetrics = (clusterId: number, ns: string, pod: string) =>
  http.get<JvmMetrics>(`/jvm/clusters/${clusterId}/jvm-metrics`, { params: { namespace: ns, pod } })

// 命名空间资源限制（LimitRange 默认值 + ResourceQuota 总量；创建才生效）
export interface NsLimitStatus {
  namespace: string; limitrange_installed: boolean; quota_installed: boolean
  limitrange_yaml: string; quota_yaml: string
  others: { limitrange: string[]; resourcequota: string[] }; hint: string
}
export const getNsLimitStatus = (clusterId: number, namespace: string) =>
  http.get<NsLimitStatus>('/resource-governance/ns-limit/status', { params: { cluster_id: clusterId, namespace } })
export const previewNsLimit = (data: Payload) =>
  http.post<Payload>('/resource-governance/ns-limit/preview', data)
export const applyNsLimit = (data: Payload) =>
  http.post<Payload>('/resource-governance/ns-limit/apply', data)
export const deleteNsLimit = (data: Payload) =>
  http.post<Payload>('/resource-governance/ns-limit/delete', data)

export const updateWorkloadImage = (clusterId: number, kind: string, namespace: string, name: string, data: Payload) =>
  http.post<{ container: string; old_image: string; new_image: string }>(`/resources/clusters/${clusterId}/workloads/${kind}/${namespace}/${name}/image`, data)

// 镜像仓库标签（R3）：Docker Registry v2（Harbor/ACR/SWR）
export interface RegistryTags { host: string; repository: string; current_tag: string; tags: string[]; total: number }
export const getRegistryTags = (image: string, limit = 100, ctx?: { cluster_id?: number; namespace?: string; kind?: string; name?: string }) =>
  http.get<RegistryTags>('/resources/registry/tags', { params: { image, limit, ...(ctx || {}) } })

// 集群监控（Prometheus 即时查询）
export interface MonitoringMetric { metric: string; label: string; unit: string; scope: string }
export interface MonitoringItem { metric: Record<string, string>; value: number | null }
export const getMonitoringMetrics = () => http.get<MonitoringMetric[]>('/monitoring/metrics')
export const getClusterMetric = (clusterId: number, metric: string, filters?: { namespace?: string; pod?: string; container?: string }) =>
  http.get<{ metric: string; label: string; unit: string; items: MonitoringItem[] }>(
    `/monitoring/clusters/${clusterId}/metrics/${metric}`, { params: filters })

// 趋势曲线（range）：Netdata 式
export interface RangeSeries { metric: Record<string, string>; values: [number, number][] }
export const getClusterMetricRange = (clusterId: number, metric: string, minutes = 30, step = 60, filters?: { namespace?: string; pod?: string; container?: string }) =>
  http.get<{ metric: string; label: string; unit: string; series: RangeSeries[] }>(
    `/monitoring/clusters/${clusterId}/metrics/${metric}/range`, { params: { minutes, step, ...filters } })
export const getClusterContainers = (clusterId: number, namespace = '') =>
  http.get<{ containers: string[] }>(`/monitoring/clusters/${clusterId}/containers`, { params: { namespace } })
export const getClusterRange = (clusterId: number, promql: string, minutes = 30, step = 60) =>
  http.get<{ series: RangeSeries[] }>(`/monitoring/clusters/${clusterId}/range`, { params: { promql, minutes, step } })

// ============ 告警屏蔽（P2-R11） ============
export interface SilenceMatcher { key: string; op: string; value: string }
export interface AlertSilence {
  id: number; name: string; matchers: SilenceMatcher[]; starts_at: string; ends_at: string
  comment: string; created_by: string; created_at: string; updated_at: string
}
export const getAlertSilences = () => http.get<AlertSilence[]>('/alert-silences')
export const createAlertSilence = (data: Payload) => http.post<AlertSilence>('/alert-silences', data)
export const deleteAlertSilence = (id: number) => http.delete<{ id: number }>(`/alert-silences/${id}`)
export const previewAlertSilence = (data: Payload) => http.post<{ matched: boolean }>('/alert-silences/preview', data)

export const isolatePod = (clusterId: number, ns: string, pod: string, data: Payload = {}) =>
  http.post<{ pod: string; label: string; old_value: string; new_value: string }>(`/resources/clusters/${clusterId}/namespaces/${ns}/pods/${pod}/isolate`, data)
export const unisolatePod = (clusterId: number, ns: string, pod: string) =>
  http.post<{ pod: string; label: string; restored: string }>(`/resources/clusters/${clusterId}/namespaces/${ns}/pods/${pod}/unisolate`, {})
export const getIsolatedPods = (clusterId: number, ns: string) =>
  http.get<{ pods: string[] }>(`/resources/clusters/${clusterId}/namespaces/${ns}/isolated`)

// ============ 主机/虚拟机纳管（N11）============
export interface HostItem {
  id: number; name: string; address: string; ssh_port: number; ssh_user: string
  has_password: boolean; ssh_key_path: string
  process_paths: { name: string; path: string }[]
  enabled: boolean; status: string; last_seen?: string | null
}
export interface HostCheckResult { online: boolean; error: string; paths: { name: string; path: string; running: boolean; pid_count: number; cpu_pct: number; mem_mb: number }[] }
export const getHosts = () => http.get<HostItem[]>('/hosts')
export const createHost = (d: Record<string, unknown>) => http.post<HostItem>('/hosts', d)
export const updateHost = (id: number, d: Record<string, unknown>) => http.put<HostItem>(`/hosts/${id}`, d)
export const deleteHost = (id: number) => http.delete<{ id: number }>(`/hosts/${id}`)
export const checkHost = (id: number) => http.post<{ host: HostItem; check: HostCheckResult }>(`/hosts/${id}/check`)

export interface AggLogPod { pod: string; phase: string; success: boolean; logs: string; error: string }
export const getWorkloadLogs = (clusterId: number, namespace: string, kind: string, name: string, params?: { container?: string; tail?: number; previous?: boolean }) =>
  http.get<{ kind: string; name: string; tail: number; previous: boolean; pods: AggLogPod[] }>(
    `/resources/clusters/${clusterId}/namespaces/${namespace}/workloads/${kind}/${name}/logs`, { params })

// ============ 节点管理（P1-R4） ============
export interface K8sNode {
  name: string; ready: string; schedulable: boolean; roles: string
  kubelet_version?: string; cpu_allocatable?: string; mem_allocatable?: string; pods_allocatable?: string
  pods_count?: number; has_metrics?: boolean; cpu_usage_milli?: number; mem_usage_bytes?: number
  cpu_pct?: number | null; mem_pct?: number | null
  labels?: Record<string, string>
  taints?: string[]
  taints_detail?: NodeTaint[]
}
export interface NodeTaint { key: string; value: string; effect: 'NoSchedule' | 'PreferNoSchedule' | 'NoExecute' | string; time_added?: string }
export interface NodeChangeResult { changed: { op: string; key?: string; value?: string; effect?: string }[]; outputs?: string[] }
export const setNodeLabels = (clusterId: number, node: string, labels: Record<string, string>, force = false) =>
  http.put<NodeChangeResult>(`/resources/clusters/${clusterId}/nodes/${node}/labels`, { labels, force })
export const setNodeTaints = (clusterId: number, node: string, taints: NodeTaint[]) =>
  http.put<NodeChangeResult>(`/resources/clusters/${clusterId}/nodes/${node}/taints`, { taints })
export const isolateNode = (clusterId: number, node: string, data: { reason?: string; taint?: boolean } = {}) =>
  http.post<NodeChangeResult>(`/resources/clusters/${clusterId}/nodes/${node}/isolate`, data)
export const unisolateNode = (clusterId: number, node: string) =>
  http.post<NodeChangeResult>(`/resources/clusters/${clusterId}/nodes/${node}/unisolate`)
export const getNodes = (clusterId: number) => http.get<K8sNode[]>(`/resources/clusters/${clusterId}/nodes`)
export const scheduleWorkload = (clusterId: number, kind: string, namespace: string, name: string, data: { node: string; preview?: boolean }) =>
  http.post<{ preview: boolean; node: string; old_node_selector: Record<string, string>; new_node_selector: Record<string, string> }>(
    `/resources/clusters/${clusterId}/workloads/${kind}/${namespace}/${name}/schedule`, data)
export interface NodeFinding { code: string; level: 'critical' | 'high' | 'warn' | 'info'; title: string; detail?: string; advice?: string }
export interface NodeDiagnosis {
  ok: boolean; node: string; ready: boolean; pressure: string[]
  findings: NodeFinding[]; summary: string
  metrics: {
    mem_pct?: number | null; load1?: number | null; load5?: number | null; processes?: number | null
    net_err_per_s?: number | null; net_drop_per_s?: number | null; time_offset_s?: number | null
    disk?: { label: string; value: number }[]; inode?: { label: string; value: number }[]
  }
}
export const diagnoseNode = (clusterId: number, node: string, faultType = '') =>
  http.get<NodeDiagnosis>(`/resources/clusters/${clusterId}/nodes/${node}/diagnose`, { params: { fault_type: faultType } })

export const cordonNode = (clusterId: number, node: string) => http.post<{ message: string }>(`/resources/clusters/${clusterId}/nodes/${node}/cordon`)
export const uncordonNode = (clusterId: number, node: string) => http.post<{ message: string }>(`/resources/clusters/${clusterId}/nodes/${node}/uncordon`)
export const drainPreview = (clusterId: number, node: string) =>
  http.get<{ success: boolean; output: string; error: string }>(`/resources/clusters/${clusterId}/nodes/${node}/drain-preview`)
export const drainNode = (clusterId: number, node: string) =>
  http.post<{ success: boolean; output: string; error: string }>(`/resources/clusters/${clusterId}/nodes/${node}/drain`, { confirm: true })
// 日志增强（P1-R5 收尾）：Pod 日志下载(gzip)
export const downloadPodLogs = (clusterId: number, ns: string, pod: string,
  params?: { container?: string; tail?: number; previous?: boolean }) =>
  http.get<Blob>(`/resources/clusters/${clusterId}/namespaces/${ns}/pods/${pod}/logs/download`, { params, responseType: 'blob' })
export const batchNodes = (clusterId: number, op: 'cordon' | 'uncordon', names: string[]) =>
  http.post<{ results: { node: string; success: boolean; message: string }[] }>(`/resources/clusters/${clusterId}/nodes/batch`, { op, names })
export interface BalanceNode { name: string; cpu_pct: number | null; mem_pct: number | null; pods_count: number }
export interface BalancePlan {
  threshold: number
  hot_nodes: BalanceNode[]
  cold_nodes: BalanceNode[]
  batches: { node: string; cpu_pct: number | null; mem_pct: number | null; total_candidates: number;
    candidate_pods: { namespace: string; name: string; owner: string; cpu_request_milli: number; mem_request_bytes: number }[] }[]
  suggestion: string
}
export const getNodeBalancePlan = (clusterId: number, threshold = 80) =>
  http.get<BalancePlan>(`/resources/clusters/${clusterId}/nodes/balance-plan`, { params: { threshold } })

export default api
