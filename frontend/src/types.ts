// 全栈共享核心类型定义（A3 工程化：消除 any）
// 与 api-server 模型字段对齐，仅覆盖前端实际使用的字段。

// ============ 通用 ============
export interface ApiResponse<T = unknown> {
  code: number
  message: string
  data: T
}

export interface OpResult {
  ok: boolean
  reason?: string
  approvers?: string[]
}

export interface BatchDecideResult {
  ok: number
  skipped: number
}

export interface PageParams {
  page?: number
  page_size?: number
  /** 告警列表是否按「同类型同服务」合并（后端默认 true；false 保留逐条） */
  group?: boolean
  [key: string]: unknown
}

export interface PageResult<T> {
  items: T[]
  total: number
  page: number
  page_size: number
}

// ============ 集群 ============
export interface Cluster {
  id: number
  name: string
  status: string // healthy / unreachable / unknown
  rbac_mode?: string
  inspect_enabled?: boolean
  maintenance_window?: string
  last_check_at?: string | null
  last_check_error?: string | null
  updated_at?: string
  created_at?: string
}

// ============ 告警 ============
export interface Alert {
  /** 关联审批状态（approved/rejected/pending），用于展示「已审核/已驳回」 */
  approval_status?: string
  id: number
  cluster_id: number
  resource_type: string // pod / node
  resource_name: string
  deployment_name?: string
  namespace: string
  fault_type: string
  severity: string // critical / high / medium / low
  status: string // pending / awaiting_approval / ticketed / resolved / self_healed
  message: string
  manual_required: boolean
  manual_reason: string
  event_count: number
  /** 合并后的同类型同服务告警条数（未合并=1） */
  group_count?: number
  /** 合并组内全部告警 id（含最新，按 id desc；未合并=[id]） */
  group_ids?: number[]
  occurred_at?: string | null
  detected_at: string
  resolved_at?: string | null
  resolved_by: string
  assigned_to: string
  assigned_at?: string | null
  acknowledged: boolean
  acknowledged_by: string
  acknowledged_at?: string | null
  cluster_name?: string
  [key: string]: unknown
}

export interface AlertComment {
  id: number
  alert_id: number
  username: string
  content: string
  created_at: string
}

// ============ 审批 ============
export interface Approval {
  id: number
  plan_id?: number | null
  alert_id?: number | null
  kind?: string // manual_assist / risk 等
  required_level?: number
  status: string
  approved_by?: string
  execute_mode?: string
  scheduled_at?: string
  created_at?: string
  decided_at?: string | number | null
  alert_fault_type?: string
  alert_resource?: string
  alert_namespace?: string
  alert_severity?: string
  alert_message?: string
  // 详情展开字段（get_approval）
  alert?: { fault_type: string; resource_name: string; namespace: string; severity: string; message: string }
  plan_summary?: string
  [key: string]: unknown
}

// ============ 知识库 ============
export interface KnowledgeEntry {
  id: number
  fault_type: string
  root_cause: string
  repair_steps: string
  execution_steps?: string
  category?: string
  is_active?: boolean
  usage_count?: number
  success_count?: number
  created_at?: string
  [key: string]: unknown
}

// ============ 用户 / 权限 ============
export interface User {
  id: number
  username: string
  role: string
  [key: string]: unknown
}

// ============ 通知 ============
export interface NotificationConfig {
  id: number
  name: string
  channel_type: string // dingtalk / wechat / feishu
  webhook_url: string
  secret: string
  encrypt_key?: string
  is_active: boolean
  scene: string
  namespaces: string
  sources?: string[] // 告警来源过滤 pod/node/log/db，空=全部
  transport_mode: string
  feedback_mode: string
  send_mode?: string // webhook / app（飞书应用机器人）
  app_id?: string
  app_secret?: string
  receive_id?: string
  receive_id_type?: string
  stream_config: string
  im_template: string
  created_at?: string
  [key: string]: unknown
}

// 统一告警通知策略（N8）：全平台一份，适用范围 = 全部告警
export interface SilenceWindow {
  enabled: boolean
  start: string // "HH:mm"
  end: string // "HH:mm"，支持跨天
  days: string // "1,2,3,4,5"，1=周一...7=周日，空=每天
}

export interface NotifyPolicy {
  enabled: boolean
  severity_routes: Record<string, string[]>
  silence_windows: SilenceWindow[]
}

export interface NotificationLog {
  id: number
  config_id?: number
  alert_id?: number
  channel?: string
  receiver?: string
  status?: string
  error?: string
  sent_at?: string
}

// ============ 工作负载 ============
export interface Workload {
  kind: string
  name: string
  namespace: string
  replicas: number
  ready_replicas: number
  [key: string]: unknown
}

export interface Pod {
  namespace: string
  name: string
  status: string
  phase?: string
  ready?: boolean
  restart_count?: number
  cpu_usage?: number
  memory_usage?: number
  [key: string]: unknown
}

// ============ 大屏 / 统计 ============
export interface ClusterHealthDetail {
  id: number
  name: string
  status: string
  version?: string
  rbac_mode?: string
  namespaces: number
  pods: number
  workloads: number
  node_total: number
  node_ready: number
  cpu_pct: number | null
  mem_pct: number | null
  alert_total: number
  alert_pending: number
  alert_self_healed: number
  alert_resolved: number
  alert_awaiting: number
  fault_top: Array<{ fault_type: string; count: number }>
  last_snapshot: string
}

export interface DashboardData {
  core: {
    cluster_total: number
    cluster_healthy: number
    alert_pending: number
    approval_pending: number
    alert_total: number
    alert_healed: number
    heal_rate: number
    plan_total: number
    plan_completed: number
    /** 处理中：在跑状态(pending/executing) 且关联告警仍活跃的自愈计划数 */
    plan_running: number
    /** 僵尸计划：在跑状态但关联告警已结束（后台收尾会清成 cancelled） */
    plan_stale: number
    plan_success_rate: number
    mttr_minutes: number
    today_resolved: number
    manual_pending: number
  }
  ai_runtime: { today_calls: number; today_cost: number; today_tokens: number; avg_latency_ms: number }
  trend_24h: Array<{ hour: string; alerts: number; healed: number }>
  status_dist: Array<{ status: string; count: number }>
  cluster_details: ClusterHealthDetail[]
  recent_alerts: Array<Record<string, unknown>>
  flow: Array<Record<string, unknown>>
  fault_top?: Array<{ fault_type: string; count: number }>
  health: { api_server: boolean; database: boolean; redis: boolean }
  clusters: DashboardCluster[]
  // ===== v2 新增区块（向后兼容，可选） =====
  health_score?: HealthScore
  kpi?: DashboardKpi
  alert_trend?: AlertTrendPoint[]
  ai_result_dist?: AiResultDistItem[]
  realtime_alerts?: RealtimeAlert[]
  abnormal_pods?: AbnormalPod[]
  db_health?: DbHealthItem[]
  action_stat?: ActionStat
  action_timeline?: ActionTimelineItem[]
  cluster_waterline?: ClusterWaterline[]
  [key: string]: unknown
}

// ============ 仪表盘 v2 ============
export interface HealthScore {
  total: number
  cluster_score: number
  alert_score: number
  db_score: number
}

export interface DashboardKpi {
  cluster_total: number
  alert_pending: number
  /** 自动处理（最近 24 小时）：自愈 ∪ 有成功执行动作的告警（去重） */
  auto_processed_24h: number
  manual_pending: number
  approval_pending: number
  approval_decided_today: number
  db_abnormal: number
  heal_rate: number
  mttr_minutes: number
  /** 处理中：自愈计划在跑且关联告警仍活跃（仪表盘/大屏同源） */
  plan_running: number
  /** 僵尸计划：在跑状态但关联告警已结束（后台收尾会清成 cancelled） */
  plan_stale: number
}

export interface AlertTrendPoint {
  bucket: string
  new: number
  resolved: number
  self_healed: number
}

export type AiResultKey = 'self_heal' | 'approval' | 'manual' | 'failed'

export interface AiResultDistItem {
  key: AiResultKey
  count?: number
  value?: number
}

export interface RealtimeAlert {
  id: number
  cluster_name: string
  resource_name: string
  namespace: string
  fault_type: string
  severity: string
  status: string
  detected_at: string
}

export interface AbnormalPod {
  cluster_name: string
  namespace: string
  pod_name: string
  alert_count?: number
  done_count?: number
  last_at?: string
  faults?: string
  fault_type?: string
  severity?: string
  status?: string
  detected_at: string
}

export interface DbHealthItem {
  id: number
  name: string
  db_type: string
  status: string
  conn_used: number
  conn_max: number
  usage_pct: number
  qps: number
  tps: number
  cache_hit: number
  sampled_at: string
}

export interface ActionStat {
  self_heal: number
  restart: number
  scale: number
  rollback: number
}

export interface ActionTimelineItem {
  time: string
  action: string
  target: string
  status: string
}

export interface ClusterWaterline {
  /** 负载均衡（Service type=LoadBalancer）数量 */
  lb_count?: number
  /** 服务总数 */
  service_total?: number
  cluster_id: number
  cluster_name: string
  cpu_pct: number | null
  mem_pct: number | null
  pods: number
  alert_pending: number
}

export interface PlatformHealth {
  api_server: boolean
  database: boolean
  redis: boolean
  executor?: boolean | null
  executor_detail?: { logstream?: boolean; grpc?: boolean; error?: string }
  time?: string
}

export interface DashboardCluster {
  id: number
  name: string
  status: string
  version?: string
  rbac_mode?: string
}

// ============ 值班 ============
export interface DutyOverride {
  id: number
  date: string
  type: string // swap / cover
  [key: string]: unknown
}

// ============ 故障诊断（告警 AI 诊断台账）============
export interface FaultDiagnosisItem {
  alert_id: number
  cluster_name?: string
  fault_type?: string
  resource_name?: string
  has_diagnosis?: boolean
  confidence?: number
  root_cause?: string
  diagnosis_time?: string
  diagnosis_json?: string
  created_at?: string
  /** 归并后：同一问题累计告警次数 / 诊断条数（2026-10-29） */
  occurrences?: number
  diagnosis_count?: number
  grouped?: boolean
  [key: string]: unknown
}

export interface DiagnosisFailureItem {
  id: number
  alert_id: number
  cluster_id?: number | null
  namespace?: string
  resource_name?: string
  fault_type?: string
  stage?: string
  reason?: string
  attempts?: number
  error?: string
  model?: string
  created_at?: string
}

export interface DiagnosisQuality {
  days: number
  diagnoses: number
  judged: number
  useful: number
  useless: number
  useful_rate: number | null
}

export interface DiagnosisFailureStats {
  days: number
  total: number
  by_reason: Array<{ reason: string; count: number }>
  by_fault_type: Array<{ fault_type: string; count: number }>
  by_stage: Array<{ stage: string; count: number }>
}

export interface AiFeedbackData {
  useful?: number
  useless?: number
  items?: Array<{ id: number; rating: number; username: string; comment?: string; created_at?: string }>
}

// ============ 定时任务 ============
export interface CustomTask {
  id: number
  name: string
  enabled: boolean
  [key: string]: unknown
}

// ============ 日志源（ES 多实例） ============
export interface LogSource {
  id: number
  name: string
  es_url: string
  index_pattern: string
  username: string
  has_password: boolean
  is_default: boolean
  enabled: boolean
  created_by?: number
  created_at?: string
  updated_at?: string
  [key: string]: unknown
}

// ============ 工单（N1：集中类型定义，避免各页自写重复 interface） ============
export interface Ticket {
  id: number
  ticket_no: string
  title: string
  description: string
  source: string
  alert_id: number | null
  approval_id: number | null
  cluster_id: number | null
  cluster_name: string
  db_connection_id: number | null
  db_name: string
  namespace: string
  resource_name: string
  priority: string
  status: string
  assignee_id: number | null
  assignee_name: string
  created_by: number | null
  creator_name: string
  resolved_at: string
  closed_at: string
  created_at: string
  updated_at: string
}

export interface TicketComment {
  id: number
  ticket_id: number
  user_id: number | null
  username: string
  type: string
  content: string
  created_at: string
}

export interface TicketDetail {
  ticket: Ticket
  comments: TicketComment[]
}

export interface TicketStats {
  scope: 'all' | 'mine'
  year?: number
  years?: number[]
  mine: {
    total: number
    open: number
    processing: number
    resolved: number
    closed: number
  }
  created: number
}

// ============ 去重：原本在多页各写一份的通用类型（2026-09-20） ============
export interface PodTarget {
  clusterId: number
  namespace: string
  name: string
}

export interface MetricPoint {
  sampled_at?: string
  cpu_usage?: number
  memory_usage?: number
  network_rx?: number
  network_tx?: number
}

export interface AICallLog {
  id: number
  request_type?: string
  model_config_id?: number
  token_usage?: number
  latency_ms?: number
  cost?: number
  status?: string
  created_at?: string
  [key: string]: unknown
}

// 统一数据库连接视图模型（原 DiagnosticCenter.DBConn / Permissions.DBConn / ProjectManagement.DBConnection 三份）
export interface DBConnection {
  group_name?: string
  id: number
  name: string
  db_type?: string
  host?: string
  port?: number
  database?: string
  slow_sql_threshold?: number
  status?: string
  collect_enabled?: boolean
  inspect_enabled?: boolean
  inspect_cron?: string
  password?: string
  readonly_password?: string
  [key: string]: unknown
}

// ============ 值班排班（on-duty：排班规则 / 节假日 / 换班代班 / 值班日历） ============
export interface OnDutySchedule {
  id: number
  name: string
  day_type: string // weekday / weekend / holiday
  shift_type: string // day / night
  start_time: string // HH:mm
  end_time: string // HH:mm
  user_ids: string // 逗号分隔的用户名
  enabled: boolean
}

export interface Holiday {
  id: number
  date: string // YYYY-MM-DD
  name: string
}

// 换班/代班完整字段（services/api.ts 里 DutyOverride 仅保留 id/date/type 精简字段）
export interface DutyOverrideItem {
  id: number
  date: string // YYYY-MM-DD
  original_username: string // 被替换人（代班时为空）
  substitute_username: string // 替班人
  type: string // swap / substitute
  note: string
  created_by: string
  created_at: string
}

export interface DutyCalendarDay {
  date: string // YYYY-MM-DD
  day_type: string // weekday / weekend / holiday
  holiday: string // 节假日名称，非节假日为空
  users: string[] // 当日值班人用户名
  overrides: Array<{ id: number; original: string; substitute: string; type: string; note: string }>
}

export interface DutyCalendarData {
  start: string
  end: string
  days: DutyCalendarDay[]
}
