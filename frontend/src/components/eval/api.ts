// 诊断质量评估页私有数据访问层（自包含，不依赖 services/api.ts）
// 注：父会话若要统一封装，可把这些函数合并进 services/api.ts；本页只 import 这些签名，
// 路由/菜单由父会话接线。token 读取方式与 services/api.ts 一致：localStorage.getItem('token')。

export type Judgment = 'correct' | 'partial' | 'wrong'

export interface Annotation {
  trace_id: string
  judgment: Judgment
  correct_root_cause: string
  expected_tools: string[]
  note: string
  annotated_by: string
  annotated_at: string
}

export interface EvalSet {
  id: string
  name: string
  description: string
  trace_ids: string[]
  count: number
  filters: Record<string, unknown>
  created_by: string
  created_at: string
}

export interface CandidateItem {
  trace_id: string
  alert_id: number
  target: string
  domain: 'app' | 'db'
  cluster_name: string
  namespace: string
  resource_name: string
  fault_type: string
  severity: string
  result: string
  action: string
  rounds: number
  duration_ms: number
  duration_text: string
  started_at: string
  step_count: number
  annotated: boolean
  annotation: Annotation | null
}

export interface EvalSetsResult {
  sets: EvalSet[]
  candidates: { total: number; items: CandidateItem[] }
}

export interface EvalSummary {
  total: number
  scored: number
  correct: number
  partial: number
  wrong: number
  accuracy_pct: number
  avg_tool_steps: number
  avg_rounds: number
  avg_duration_ms: number
  avg_duration_text: string
  tool_hit_rate_pct: number | null
  tool_traces: number
  partial_weight: number
  set_id?: string | null
  set_name?: string
  filters?: Record<string, unknown>
  resolved_count?: number
}

export interface EvalItem {
  trace_id: string
  target: string
  domain: string
  fault_type: string
  result: string
  rounds: number
  duration_ms: number
  duration_text: string
  judgment: Judgment | null
  score: number | null
  tool_steps: number
  expected_tools: string[]
  actual_tools: string[]
  tool_hit: number | null
  correct_root_cause: string
  note: string
  skipped_reason?: string
}

export interface EvalRunResult {
  summary: EvalSummary
  items: EvalItem[]
  run_at: string
  run_by: string
}

const BASE = '/api/v1'

async function request<T>(path: string, options: { method?: string; params?: Record<string, unknown>; body?: unknown } = {}): Promise<T> {
  const token = localStorage.getItem('token')
  const { method = 'GET', params, body } = options
  const url = new URL(BASE + path, window.location.origin)
  if (params) {
    Object.entries(params).forEach(([k, v]) => {
      if (v !== undefined && v !== null && v !== '') url.searchParams.set(k, String(v))
    })
  }
  const res = await fetch(url.toString(), {
    method,
    headers: {
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}),
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })
  let payload: { code?: number; message?: string; data?: T }
  try {
    payload = await res.json()
  } catch {
    throw new Error(`HTTP ${res.status}`)
  }
  if (!res.ok || payload.code !== 200) {
    throw new Error(payload?.message || `HTTP ${res.status}`)
  }
  return payload.data as T
}

export const postAnnotation = (data: {
  trace_id: string
  judgment: Judgment
  correct_root_cause?: string
  expected_tools?: string[]
  note?: string
}) => request<Annotation>('/eval/annotations', { method: 'POST', body: data })

export const getEvalSets = (params: Record<string, unknown>) =>
  request<EvalSetsResult>('/eval/sets', { params })

export const createEvalSet = (data: {
  name: string
  description?: string
  trace_ids?: string[]
  filters?: Record<string, unknown>
}) => request<EvalSet>('/eval/sets', { method: 'POST', body: data })

export const runEval = (data: {
  set_id?: string | null
  trace_ids?: string[] | null
  filters?: Record<string, unknown> | null
  partial_weight?: number
}) => request<EvalRunResult>('/eval/run', { method: 'POST', body: data })

export const getEvalSummary = () => request<EvalRunResult | null>('/eval/summary')
