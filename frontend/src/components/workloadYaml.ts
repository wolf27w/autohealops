// 工作负载创建：结构化表单值 → YAML（纯函数，无 React 依赖，便于单测）
export interface KvItem { key: string; value?: string }

export interface WorkloadValues {
  name: string
  namespace: string
  replicas?: number
  image: string
  containerPort?: number
  cpuRequest?: string
  cpuLimit?: string
  memoryRequest?: string
  memoryLimit?: string
  env?: KvItem[]
  labels?: KvItem[]
  schedule?: string
  command?: string
  livenessEnabled?: boolean
  livenessPath?: string
  livenessPort?: number
  readinessEnabled?: boolean
  readinessPath?: string
  readinessPort?: number
}

function fromKvList(list?: KvItem[]): Record<string, string> {
  const o: Record<string, string> = {}
  for (const it of list || []) if (it && it.key) o[it.key] = it.value ?? ''
  return o
}

// YAML 字符串转义（双引号值）
const q = (s: string) => `"${String(s).replace(/\\/g, '\\\\').replace(/"/g, '\\"')}"`

function labelsBlock(indent: string, v: WorkloadValues): string {
  const custom = Object.entries(fromKvList(v.labels)).filter(([k]) => k !== 'app')
  const lines = [`${indent}app: ${v.name}`]
  for (const [k, val] of custom) lines.push(`${indent}${k}: ${val}`)
  return lines.join('\n')
}

function containerSpec(v: WorkloadValues): string {
  const lines: string[] = []
  lines.push(`        - name: ${v.name}`)
  lines.push(`          image: ${v.image}`)
  if (v.containerPort) {
    lines.push(`          ports:`)
    lines.push(`            - containerPort: ${v.containerPort}`)
  }
  const req: string[] = []
  const lim: string[] = []
  if (v.cpuRequest) req.push(`              cpu: ${v.cpuRequest}`)
  if (v.memoryRequest) req.push(`              memory: ${v.memoryRequest}`)
  if (v.cpuLimit) lim.push(`              cpu: ${v.cpuLimit}`)
  if (v.memoryLimit) lim.push(`              memory: ${v.memoryLimit}`)
  if (req.length || lim.length) {
    lines.push(`          resources:`)
    if (req.length) { lines.push(`            requests:`); lines.push(...req) }
    if (lim.length) { lines.push(`            limits:`); lines.push(...lim) }
  }
  const envs = (v.env || []).filter((e) => e && e.key)
  if (envs.length) {
    lines.push(`          env:`)
    for (const e of envs) {
      lines.push(`            - name: ${e.key}`)
      lines.push(`              value: ${q(e.value ?? '')}`)
    }
  }
  const probe = (name: string, path: string, port: number) => [
    `          ${name}:`,
    `            httpGet:`,
    `              path: ${path || '/'}`,
    `              port: ${port || v.containerPort || 80}`,
    `            initialDelaySeconds: 10`,
    `            periodSeconds: 10`,
  ]
  if (v.livenessEnabled) lines.push(...probe('livenessProbe', v.livenessPath || '/', v.livenessPort as number))
  if (v.readinessEnabled) lines.push(...probe('readinessProbe', v.readinessPath || '/', v.readinessPort as number))
  return lines.join('\n')
}

export function buildYaml(kind: string, v: WorkloadValues): string {
  const ns = v.namespace || 'default'
  const nm = v.name

  if (kind === 'statefulset') {
    return `apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: ${nm}
  namespace: ${ns}
  labels:
${labelsBlock('    ', v)}
spec:
  serviceName: ${nm}
  replicas: ${v.replicas ?? 1}
  selector:
    matchLabels:
      app: ${nm}
  template:
    metadata:
      labels:
${labelsBlock('        ', v)}
    spec:
      containers:
${containerSpec(v)}
`
  }
  if (kind === 'daemonset') {
    return `apiVersion: apps/v1
kind: DaemonSet
metadata:
  name: ${nm}
  namespace: ${ns}
  labels:
${labelsBlock('    ', v)}
spec:
  selector:
    matchLabels:
      app: ${nm}
  template:
    metadata:
      labels:
${labelsBlock('        ', v)}
    spec:
      containers:
${containerSpec(v)}
`
  }
  if (kind === 'cronjob') {
    return `apiVersion: batch/v1
kind: CronJob
metadata:
  name: ${nm}
  namespace: ${ns}
spec:
  schedule: "${v.schedule || '*/5 * * * *'}"
  jobTemplate:
    spec:
      template:
        spec:
          restartPolicy: OnFailure
          containers:
            - name: ${nm}
              image: ${v.image}
              command: ["/bin/sh", "-c", "${(v.command || 'echo hello').replace(/\\/g, '\\\\').replace(/"/g, '\\"')}"]
`
  }
  return `apiVersion: apps/v1
kind: Deployment
metadata:
  name: ${nm}
  namespace: ${ns}
  labels:
${labelsBlock('    ', v)}
spec:
  replicas: ${v.replicas ?? 1}
  selector:
    matchLabels:
      app: ${nm}
  template:
    metadata:
      labels:
${labelsBlock('        ', v)}
    spec:
      containers:
${containerSpec(v)}
`
}
