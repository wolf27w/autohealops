// 工作负载创建：结构化表单值 → YAML（纯函数，无 React 依赖，便于单测）
export interface KvItem { key: string; value?: string }

export type VolumeType = 'configMap' | 'secret' | 'pvc' | 'emptyDir'

// 存储挂载：一个 item = 一个卷 + 挂载到容器的路径（volumes + volumeMounts 由同一 name 关联）
export interface VolumeMountValue {
  name: string
  type: VolumeType
  source?: string        // configMap/secret/pvc 的名称；emptyDir 留空
  mountPath: string
  subPath?: string
  readOnly?: boolean
}

export interface WorkloadValues {
  name: string
  namespace: string
  replicas?: number
  image: string
  containerPort?: number
  imagePullPolicy?: string
  cpuRequest?: string
  cpuLimit?: string
  memoryRequest?: string
  memoryLimit?: string
  env?: KvItem[]
  labels?: KvItem[]
  volumes?: VolumeMountValue[]
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

// 归一化卷列表：过滤空项、自动生成卷名（volumeMounts 与 volumes 用同一 name 关联）
function normVolumes(v: WorkloadValues): VolumeMountValue[] {
  return (v.volumes || [])
    .filter((x) => x && x.type && (x.type === 'emptyDir' || x.source))
    .map((x, i) => ({ ...x, name: x.name || `vol-${i + 1}` }))
}

function containerSpec(v: WorkloadValues): string {
  const lines: string[] = []
  lines.push(`        - name: ${v.name}`)
  lines.push(`          image: ${v.image}`)
  if (v.imagePullPolicy && v.imagePullPolicy !== 'IfNotPresent') {
    lines.push(`          imagePullPolicy: ${v.imagePullPolicy}`)
  }
  if (v.containerPort) {
    lines.push(`          ports:`)
    lines.push(`            - containerPort: ${v.containerPort}`)
  }
  const vols = normVolumes(v)
  if (vols.length) {
    lines.push(`          volumeMounts:`)
    for (const m of vols) {
      lines.push(`            - name: ${m.name}`)
      lines.push(`              mountPath: ${m.mountPath}`)
      if (m.subPath) lines.push(`              subPath: ${m.subPath}`)
      if (m.readOnly) lines.push(`              readOnly: true`)
    }
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

function volumesBlock(v: WorkloadValues): string {
  const vols = normVolumes(v)
  if (!vols.length) return ''
  const lines = [`      volumes:`]
  for (const m of vols) {
    lines.push(`        - name: ${m.name}`)
    if (m.type === 'configMap') {
      lines.push(`          configMap:`)
      lines.push(`            name: ${m.source}`)
    } else if (m.type === 'secret') {
      lines.push(`          secret:`)
      lines.push(`            secretName: ${m.source}`)
    } else if (m.type === 'pvc') {
      lines.push(`          persistentVolumeClaim:`)
      lines.push(`            claimName: ${m.source}`)
    } else {
      lines.push(`          emptyDir: {}`)
    }
  }
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
${volumesBlock(v)}
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
${volumesBlock(v)}
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
${volumesBlock(v)}
`
}
