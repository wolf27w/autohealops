// Oracle 客户端版本管理 API（2026-11-16）：列表 / 上传 / 一键下载 / 启用 / 删除
// 单独一个文件，避免与 services/api.ts 的巨型改动冲突
import axios from 'axios'

const api = axios.create({ baseURL: '/api/v1', timeout: 0 })
api.interceptors.request.use((config) => {
  const token = localStorage.getItem('token')
  if (token) config.headers.Authorization = 'Bearer ' + token
  return config
})
api.interceptors.response.use(
  (resp) => {
    const body = resp.data
    if (body && body.code !== 200) return Promise.reject(new Error(body.message || '请求失败'))
    return body.data
  },
  (err) => {
    const msg = err?.response?.data?.message || err?.message || '网络错误'
    return Promise.reject(new Error(msg))
  },
)

export interface OracleClientItem {
  name: string
  path: string
  libs: string[]
  usable: boolean
  size_mb: number
  created_at: string
  active: boolean
}
export interface OracleCatalogItem {
  id: string
  label: string
  platform: string
  version: string
  url: string
  manual: boolean
}
export interface OracleClientsData {
  home: string
  active: string
  current_lib_dir: string
  clients: OracleClientItem[]
  catalog: OracleCatalogItem[]
}

export const getOracleClients = () => api.get<OracleClientsData>('/oracle-clients') as unknown as Promise<OracleClientsData>

export const uploadOracleClient = (name: string, file: File) =>
  api.post(`/oracle-clients/upload`, file, {
    params: { name },
    // X-Filename 用来判断包类型（zip / tar.gz / dmg / pkg）
    headers: { 'Content-Type': 'application/octet-stream', 'X-Filename': file.name },
    timeout: 0,
  })

export const downloadOracleClient = (body: { catalog_id?: string; url?: string; name?: string }) =>
  api.post('/oracle-clients/download', body) as unknown as Promise<{ name: string; size_mb?: number }>

export const activateOracleClient = (name: string) =>
  api.post(`/oracle-clients/${encodeURIComponent(name)}/activate`) as unknown as Promise<{ active: string; need_restart: boolean }>

export const deleteOracleClient = (name: string) =>
  api.delete(`/oracle-clients/${encodeURIComponent(name)}`) as unknown as Promise<{ name: string }>
