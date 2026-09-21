// 当前登录用户与角色判断（统一入口）
// localStorage.user 的读取统一走这里：加 try/catch + JSON.parse 失败兜底，避免写坏时页面崩溃

export interface CurrentUser {
  id?: number
  username?: string
  role?: string
  [key: string]: unknown
}

/** 安全读取 localStorage.user（缺失/写坏/非对象 → 返回空对象，绝不抛异常） */
export function getCurrentUser(): CurrentUser {
  try {
    const raw = localStorage.getItem('user')
    if (!raw) return {}
    const parsed = JSON.parse(raw)
    return parsed && typeof parsed === 'object' ? (parsed as CurrentUser) : {}
  } catch {
    return {}
  }
}

/** 当前角色（admin / operator / viewer / developer），缺失返回空串 */
export function getRole(): string {
  return getCurrentUser().role || ''
}

/** 是否 admin（管理专属菜单/路由可见） */
export function isAdmin(): boolean {
  return getRole() === 'admin'
}

/** 是否可写：admin / operator 可写，viewer 只读 */
export function canWrite(): boolean {
  const role = getRole()
  return role === 'admin' || role === 'operator'
}
