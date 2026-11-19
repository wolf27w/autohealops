// Agent 运行轨迹页私有数据访问层（自包含，不依赖 services/api.ts）
// 注：父会话若要统一封装，可把这三个函数合并进 services/api.ts（http.get 通用封装），
// 本页只 import 这三个签名，路由/菜单由父会话接线。

export interface TraceStep {
  key: string
  parent_key: string
  kind: string
  title: string
  tool_name: string
  args?: unknown
  result_summary: string
  status: 'ok' | 'error' | 'skip'
  error: string
  at_ms: number
  duration_ms: number
  duration_text: string
  evidence_refs: string[]
}

export interface TraceEvidence {
  type: string
  label: string
  content: string
}

export interface TraceItem {
  trace_id: string
  alert_id: number
  domain: 'app' | 'db'
  target: string
  cluster_name: string
  namespace: string
  resource_name: string
  fault_type: string
  severity: string
  trigger: string
  result: 'ok' | 'partial' | 'failed' | 'ticket'
  action: 'auto' | 'approval' | 'plan_only'
  risk_level: string
  rounds: number
  duration_ms: number
  duration_text: string
  started_at: string
  ended_at: string
  approval_id: number | null
  ticket_id: number | null
  ticket_no: string
  summary: { 判定: string; 动作: string; 结论: string }
  step_count: number
}

export interface TraceDetail extends Omit<TraceItem, 'step_count'> {
  steps: TraceStep[]
  evidence: TraceEvidence[]
}

export interface TraceStats {
  today_total: number
  auto_closed: number
  auto_ratio: number
  approved: number
  ticketed: number
  avg_rounds: number
  avg_duration_ms: number
  avg_duration_text: string
}

export interface TraceListResult {
  total: number
  items: TraceItem[]
}

const BASE = '/api/v1'

async function request<T>(path: string, params?: Record<string, unknown>): Promise<T> {
  const token = localStorage.getItem('token')
  const url = new URL(BASE + path, window.location.origin)
  if (params) {
    Object.entries(params).forEach(([k, v]) => {
      if (v !== undefined && v !== null && v !== '') url.searchParams.set(k, String(v))
    })
  }
  const res = await fetch(url.toString(), {
    headers: { Authorization: token ? `Bearer ${token}` : '' },
  })
  let body: { code?: number; message?: string; data?: T }
  try {
    body = await res.json()
  } catch {
    throw new Error(`HTTP ${res.status}`)
  }
  if (!res.ok || body.code !== 200) {
    throw new Error(body?.message || `HTTP ${res.status}`)
  }
  return body.data as T
}

export const getAgentTraceStats = () => request<TraceStats>('/agent-traces/stats')

export const getAgentTraces = (params: Record<string, unknown>) =>
  request<TraceListResult>('/agent-traces', params)

export const getAgentTraceDetail = (traceId: string) =>
  request<TraceDetail>(`/agent-traces/${traceId}`)
