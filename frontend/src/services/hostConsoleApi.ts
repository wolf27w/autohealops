// 堡垒机（R8）：Web 终端 / 会话回放 / SFTP 的 API 与 WS 地址封装
import api from './api'

// 复用 api 实例（响应拦截器已把 AxiosResponse 解包为 body.data，这里只做类型收窄）
const http = {
  get: <T = unknown>(url: string, config?: Record<string, unknown>) =>
    api.get(url, config) as unknown as Promise<T>,
  post: <T = unknown>(url: string, data?: unknown, config?: Record<string, unknown>) =>
    api.post(url, data, config) as unknown as Promise<T>,
}

export interface HostSessionItem {
  id: number
  host_id: number
  host_name: string
  user_id: number
  username: string
  source_ip: string
  status: string
  started_at: string | null
  ended_at: string | null
  duration_sec: number
  bytes_in: number
  bytes_out: number
  recorded: boolean
  close_reason: string
  active: boolean
}

export interface ReplayChunk {
  seq: number
  ts_ms: number
  direction: 'input' | 'output'
  text: string
}

export interface SftpEntry {
  name: string
  path: string
  is_dir: boolean
  size: number
  mtime: number
  perm: string
}

export function buildHostTerminalWsUrl(hostId: number, opts?: { cols?: number; rows?: number; record?: boolean }) {
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  const token = localStorage.getItem('token') || ''
  const cols = opts?.cols || 120
  const rows = opts?.rows || 32
  const rec = opts?.record === false ? '0' : '1'
  return `${proto}//${window.location.host}/api/v1/ws/hosts/${hostId}/terminal?token=${encodeURIComponent(token)}&cols=${cols}&rows=${rows}&record=${rec}`
}

export const getHostSessions = (hostId: number, limit = 50) =>
  http.get<HostSessionItem[]>(`/hosts/${hostId}/sessions`, { params: { limit } })

export const getHostSession = (sessionId: number) =>
  http.get<HostSessionItem>(`/host-sessions/${sessionId}`)

export const getHostSessionReplay = (sessionId: number) =>
  http.get<{ session: Record<string, unknown>; chunks: ReplayChunk[] }>(`/host-sessions/${sessionId}/replay`)

export const kickHostSession = (sessionId: number) =>
  http.post<{ id: number }>(`/host-sessions/${sessionId}/kick`)

export const sftpList = (hostId: number, path: string) =>
  http.get<{ path: string; entries: SftpEntry[] }>(`/hosts/${hostId}/sftp/list`, { params: { path } })

export async function sftpUpload(hostId: number, dir: string, file: File, onProgress?: (pct: number) => void) {
  // 直接以文件二进制为请求体（后端按 octet-stream 流式写远程），避免 multipart 依赖
  const token = localStorage.getItem('token') || ''
  return new Promise<{ path: string; size: number }>((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    const url = `/api/v1/hosts/${hostId}/sftp/upload?path=${encodeURIComponent(dir)}&filename=${encodeURIComponent(file.name)}`
    xhr.open('POST', url)
    xhr.setRequestHeader('Authorization', `Bearer ${token}`)
    xhr.setRequestHeader('Content-Type', 'application/octet-stream')
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable && onProgress) onProgress(Math.round((e.loaded / e.total) * 100))
    }
    xhr.onload = () => {
      try {
        const body = JSON.parse(xhr.responseText || '{}')
        if (xhr.status >= 200 && xhr.status < 300 && body.code === 200) resolve(body.data)
        else reject(new Error(body.message || body.detail || `上传失败（${xhr.status}）`))
      } catch {
        reject(new Error(`上传失败（${xhr.status}）`))
      }
    }
    xhr.onerror = () => reject(new Error('网络错误，上传失败'))
    xhr.send(file)
  })
}

export function sftpDownloadUrl(hostId: number, path: string) {
  const token = localStorage.getItem('token') || ''
  return `/api/v1/hosts/${hostId}/sftp/download?path=${encodeURIComponent(path)}&token=${encodeURIComponent(token)}`
}

export async function sftpDownload(hostId: number, path: string) {
  const token = localStorage.getItem('token') || ''
  const resp = await fetch(sftpDownloadUrl(hostId, path), { headers: { Authorization: `Bearer ${token}` } })
  if (!resp.ok) throw new Error(`下载失败（${resp.status}）`)
  const blob = await resp.blob()
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = path.split('/').pop() || 'download'
  a.click()
  setTimeout(() => URL.revokeObjectURL(a.href), 2000)
}

// ============ 命令审计（B8）：终端危险命令拦截审计列表 / CSV 导出 ============
export interface HostCommandAuditItem {
  id: number
  host_id: number | null
  host_name: string
  user_id: number
  username: string
  session_id: string | null
  command: string
  risk: string
  action: string
  reason: string
  client_ip: string
  created_at: string
}

export interface HostCommandAuditsPage {
  items: HostCommandAuditItem[]
  total: number
  page: number
  page_size: number
}

export interface HostCommandAuditParams {
  host_id?: number
  user_id?: number | string
  keyword?: string
  action?: 'allowed' | 'blocked'
  start?: string
  end?: string
  page?: number
  page_size?: number
}

export const listHostCommandAudits = (params: HostCommandAuditParams) =>
  http.get<HostCommandAuditsPage>('/host-command-audits', { params })

// 拼出带 query 的导出 URL（供直接下载 / fetch 用）
export function hostCommandAuditsExportUrl(params: HostCommandAuditParams) {
  const qs = new URLSearchParams()
  Object.entries(params).forEach(([k, v]) => {
    if (v === undefined || v === null || v === '') return
    qs.append(k, String(v))
  })
  return `/api/v1/host-command-audits/export?${qs.toString()}`
}

// 浏览器下载：带 token header 拉 blob 再触发下载（window.open 无法带 header）
export async function downloadHostCommandAudits(params: HostCommandAuditParams) {
  const token = localStorage.getItem('token') || ''
  const resp = await fetch(hostCommandAuditsExportUrl(params), {
    headers: { Authorization: `Bearer ${token}` },
  })
  if (!resp.ok) {
    let msg = `导出失败（${resp.status}）`
    try {
      const body = await resp.json()
      msg = body?.message || body?.detail || msg
    } catch { /* 非 JSON 响应 */ }
    throw new Error(msg)
  }
  const blob = await resp.blob()
  // 从 Content-Disposition 提取文件名，缺省用默认名
  let filename = 'host-command-audits.csv'
  const cd = resp.headers.get('Content-Disposition') || ''
  const m = /filename\*?=(?:UTF-8''|UTF-8|['"])?([^;"']+)/i.exec(cd)
  if (m && m[1]) {
    try { filename = decodeURIComponent(m[1].trim()) } catch { filename = m[1].trim() }
  }
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(a.href), 2000)
}
