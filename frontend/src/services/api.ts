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
export const getPodContainers = (clusterId: number, ns: string, pod: string) => http.get<string[]>(`/resources/clusters/${clusterId}/namespaces/${ns}/pods/${pod}/containers`)
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
export const applyWorkloadYaml = (clusterId: number, kind: string, ns: string, name: string, yaml: string) =>
  http.put(`/resources/clusters/${clusterId}/workloads/${kind}/${ns}/${name}/yaml`, { yaml })
// 创建/编辑工作负载（name 留空=按 YAML 内 metadata.name 创建）
export const applyWorkload = (clusterId: number, data: Payload) =>
  http.post(`/resources/clusters/${clusterId}/workloads/apply`, data)
// 删除工作负载（deployment/statefulset/daemonset/cronjob/pod）
export const deleteWorkload = (clusterId: number, kind: string, ns: string, name: string) =>
  http.delete(`/resources/clusters/${clusterId}/workloads/${kind}/${ns}/${name}`)
// 集群 Pod 实时列表（含创建时间）：namespace 空=全部
export const getClusterPods = (clusterId: number, namespace = '') =>
  http.get(`/resources/clusters/${clusterId}/pods`, { params: { namespace } })
// 单个 Pod 的事件（k8s Event）
export const getPodEvents = (clusterId: number, ns: string, pod: string) =>
  http.get(`/resources/clusters/${clusterId}/namespaces/${ns}/pods/${pod}/events`)
// Pod 详情（含容器组内容）
export const getPodDetail = (clusterId: number, ns: string, pod: string) =>
  http.get(`/resources/clusters/${clusterId}/namespaces/${ns}/pods/${pod}/detail`)
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
export const getAgentSessions = () => http.get('/agent/sessions')
export const getAgentMessages = (id: number) => http.get(`/agent/sessions/${id}/messages`)
export const sendAgentMessage = (id: number, content: string, deep = false) => http.post(`/agent/sessions/${id}/messages`, { content, deep })
export const closeAgentSession = (id: number) => http.post(`/agent/sessions/${id}/close`)

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

// ============ 数据库连接管理 ============
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
  file: boolean; file_path?: string; hint?: string
}

export const getJvmActions = () => http.get<JvmAction[]>('/jvm/actions')
export const getJvmProcesses = (clusterId: number, ns: string, pod: string, container = '') =>
  http.get<{ raw: string; processes: JvmProcess[] }>(`/jvm/clusters/${clusterId}/namespaces/${ns}/pods/${pod}/processes`, { params: { container } })
export const jvmDiagnose = (clusterId: number, ns: string, pod: string, payload: Payload) =>
  http.post<JvmDiagResult>(`/jvm/clusters/${clusterId}/namespaces/${ns}/pods/${pod}/diagnose`, payload)

export default api
