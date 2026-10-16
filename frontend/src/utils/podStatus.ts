// kubectl 风格 Pod 状态（后端 ListPods 计算的 status 字段）→ 颜色 / 展示
// 兼容动态值：Init:0/1、Init:CrashLoopBackOff、ExitCode:1、Signal:9 等
const POD_STATUS_COLOR: Record<string, string> = {
  Running: 'green', Completed: 'blue', Succeeded: 'blue', Terminating: 'default',
  Pending: 'orange', ContainerCreating: 'orange', PodInitializing: 'orange', NotReady: 'orange',
  CrashLoopBackOff: 'red', ImagePullBackOff: 'red', ErrImagePull: 'red', CreateContainerConfigError: 'red',
  Error: 'red', Failed: 'red', Unknown: 'default',
}

export function podStatusColor(s?: string): string {
  if (!s) return 'default'
  if (POD_STATUS_COLOR[s]) return POD_STATUS_COLOR[s]
  if (s.startsWith('Init:')) return /Error|CrashLoop|Signal/.test(s) ? 'red' : 'orange'
  if (s.startsWith('Signal:') || s.startsWith('ExitCode:')) return 'red'
  return 'default'
}

/** Pod 就绪列展示（READY 分子/分母）；无容器数时返回 '-' */
export function podReadyText(ready?: unknown, total?: unknown): string {
  const t = Number(total)
  if (!t) return '-'
  return `${Number(ready) || 0}/${t}`
}
