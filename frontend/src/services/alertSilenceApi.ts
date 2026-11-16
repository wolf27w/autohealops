// 告警屏蔽 / 抑制规则 API（R11 重做）
// 独立封装 axios 实例，不复用 services/api.ts，避免与其既有告警屏蔽函数（旧契约）冲突。
import axios, { type AxiosRequestConfig } from 'axios'
import { message } from 'antd'

// ============ 类型（与后端新契约一致） ============
export interface SilenceMatcher { key: string; op: string; value: string }

export type RecurrenceType = 'once' | 'daily' | 'weekly'

export interface SilenceRecurrence {
  type: RecurrenceType
  /** 每周生效的星期（1=周一 … 7=周日），type=weekly 时使用 */
  dailyDays?: number[]
  /** 每日生效时间段起点，格式 "HH:mm" */
  start?: string
  /** 每日生效时间段终点，格式 "HH:mm" */
  end?: string
}

export interface AlertSilence {
  id: number
  name: string
  matchers: SilenceMatcher[]
  starts_at: string
  ends_at: string
  comment: string
  created_by: string
  created_at: string
  recurrence: SilenceRecurrence | null
  /** 后端计算的「当前是否生效」 */
  active: boolean
  /** 是否启用（后端若支持停用；缺省视为 true） */
  enabled?: boolean
}

export interface SilenceStatus {
  active: boolean
  next_start: string | null
  next_end: string | null
  reason: string | null
}

export interface SilencePreviewSample {
  id: number | string
  fault_type: string
  severity: string
  resource_name: string
  detected_at: string
}

export interface SilencePreviewResult {
  matched_total: number
  by_fault_type: Record<string, number>
  samples: SilencePreviewSample[]
  window: { start: string; end: string; active: boolean }
}

export interface InhibitionRule {
  id: number
  name: string
  source_matchers: SilenceMatcher[]
  target_matchers: SilenceMatcher[]
  equal_labels: string[]
  enabled: boolean
  comment: string
}

// ============ 请求封装（与 services/api.ts 同款拦截逻辑） ============
const http = axios.create({ baseURL: '/api/v1', timeout: 30000 })

http.interceptors.request.use((config) => {
  const token = localStorage.getItem('token')
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

http.interceptors.response.use(
  (response) => {
    const body = response.data
    if (body && typeof body === 'object' && body.code !== 200) {
      const msg = body.message || '请求失败'
      message.error(msg)
      return Promise.reject(new Error(msg))
    }
    return body && typeof body === 'object' && 'data' in body ? body.data : body
  },
  (error) => {
    if (error?.response?.status === 401) {
      localStorage.removeItem('token')
      window.location.href = '/login'
    } else {
      message.error(error?.response?.data?.detail || error?.message || '网络错误')
    }
    return Promise.reject(error)
  },
)

const request = {
  get: <T = unknown>(url: string, config?: AxiosRequestConfig) => http.get(url, config) as Promise<T>,
  post: <T = unknown>(url: string, data?: unknown, config?: AxiosRequestConfig) => http.post(url, data, config) as Promise<T>,
  put: <T = unknown>(url: string, data?: unknown, config?: AxiosRequestConfig) => http.put(url, data, config) as Promise<T>,
  delete: <T = unknown>(url: string, config?: AxiosRequestConfig) => http.delete(url, config) as Promise<T>,
}

// ============ 屏蔽规则 ============
export interface SilencePayload {
  name?: string
  matchers: SilenceMatcher[]
  starts_at?: string
  ends_at?: string
  comment?: string
  recurrence?: SilenceRecurrence | null
  enabled?: boolean
}
export const getAlertSilences = () => request.get<AlertSilence[]>('/alert-silences')
export const createAlertSilence = (data: SilencePayload) => request.post<AlertSilence>('/alert-silences', data)
export const updateAlertSilence = (id: number, data: SilencePayload) => request.put<AlertSilence>(`/alert-silences/${id}`, data)
export const deleteAlertSilence = (id: number) => request.delete<{ id: number }>(`/alert-silences/${id}`)
export const getAlertSilenceStatus = (id: number) => request.get<SilenceStatus>(`/alert-silences/${id}/status`)

export interface PreviewPayload {
  matchers: SilenceMatcher[]
  recurrence?: SilenceRecurrence | null
  starts_at?: string
  ends_at?: string
}
export const previewAlertSilence = (data: PreviewPayload) => request.post<SilencePreviewResult>('/alert-silences/preview', data)

// ============ 抑制规则 ============
export interface InhibitionPayload {
  name?: string
  source_matchers: SilenceMatcher[]
  target_matchers: SilenceMatcher[]
  equal_labels?: string[]
  enabled?: boolean
  comment?: string
}
export const getInhibitions = () => request.get<InhibitionRule[]>('/alert-silences/inhibitions')
export const createInhibition = (data: InhibitionPayload) => request.post<InhibitionRule>('/alert-silences/inhibitions', data)
export const updateInhibition = (id: number, data: InhibitionPayload) => request.put<InhibitionRule>(`/alert-silences/inhibitions/${id}`, data)
export const deleteInhibition = (id: number) => request.delete<{ id: number }>(`/alert-silences/inhibitions/${id}`)
