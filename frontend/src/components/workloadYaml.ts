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

// 单个容器（多容器支持：主容器之外的附加普通容器，如 sidecar）
export interface WorkloadContainerValue {
  name: string
  image: string
  containerPort?: number
  cpuRequest?: string
  cpuLimit?: string
  memoryRequest?: string
  memoryLimit?: string
  command?: string[]        // 启动命令（数组形式，覆盖镜像默认 entrypoint）
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
  // ===== 更新策略（Deployment） =====
  strategyType?: string         // RollingUpdate / Recreate
  maxSurge?: string             // 如 25% 或绝对数值
  maxUnavailable?: string       // 如 25% 或绝对数值
  // ===== StatefulSet =====
  podManagementPolicy?: string  // OrderedReady / Parallel
  updateStrategyType?: string   // RollingUpdate / OnDelete
  // ===== 镜像拉取密钥（dockerconfigjson Secret 名称） =====
  imagePullSecrets?: string[]
  // ===== 多容器 =====
  containers?: WorkloadContainerValue[]  // 附加容器（不含主容器）；主容器 = 顶层 name/image/资源/探针/挂载字段
  containerCommand?: string[]            // 主容器启动命令（非 Cron）
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
  if (v.containerCommand && v.containerCommand.length) {
    lines.push(...commandBlockLines(v.containerCommand))
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

// 启动命令（块式列表，避免 flow 风格 [..] 无法被极简解析器读回）
function commandBlockLines(cmd: string[]): string[] {
  if (!cmd || !cmd.length) return []
  const out = [`          command:`]
  for (const a of cmd) out.push(`            - ${yamlString(a)}`)
  return out
}

// Deployment 更新策略（默认 RollingUpdate，与集群默认一致）
function deploymentStrategyBlock(v: WorkloadValues): string {
  if (v.strategyType === 'Recreate') return `  strategy:\n    type: Recreate`
  return `  strategy:\n    type: RollingUpdate\n    rollingUpdate:\n      maxSurge: ${v.maxSurge || '25%'}\n      maxUnavailable: ${v.maxUnavailable || '25%'}`
}

// 附加容器（多容器：主容器之外）；主容器由 containerSpec 渲染
function additionalContainerSpec(c: WorkloadContainerValue): string {
  const lines: string[] = []
  lines.push(`        - name: ${c.name}`)
  lines.push(`          image: ${c.image}`)
  if (c.containerPort) {
    lines.push(`          ports:`)
    lines.push(`            - containerPort: ${c.containerPort}`)
  }
  const req: string[] = []
  const lim: string[] = []
  if (c.cpuRequest) req.push(`              cpu: ${c.cpuRequest}`)
  if (c.memoryRequest) req.push(`              memory: ${c.memoryRequest}`)
  if (c.cpuLimit) lim.push(`              cpu: ${c.cpuLimit}`)
  if (c.memoryLimit) lim.push(`              memory: ${c.memoryLimit}`)
  if (req.length || lim.length) {
    lines.push(`          resources:`)
    if (req.length) { lines.push(`            requests:`); lines.push(...req) }
    if (lim.length) { lines.push(`            limits:`); lines.push(...lim) }
  }
  if (c.command && c.command.length) lines.push(...commandBlockLines(c.command))
  return lines.join('\n')
}

// 所有容器：主容器 + 附加容器
function containersBlock(v: WorkloadValues): string {
  const blocks = [containerSpec(v), ...(v.containers || []).filter((c) => c && c.image).map(additionalContainerSpec)]
  return blocks.join('\n')
}

// 镜像拉取密钥（template.spec 级别）
function imagePullSecretsBlock(v: WorkloadValues): string {
  const names = (v.imagePullSecrets || []).filter(Boolean)
  if (!names.length) return ''
  return `      imagePullSecrets:\n` + names.map((n) => `        - name: ${n}`).join('\n')
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
  podManagementPolicy: ${v.podManagementPolicy || 'OrderedReady'}
  updateStrategy:
    type: ${v.updateStrategyType || 'RollingUpdate'}
  selector:
    matchLabels:
      app: ${nm}
  template:
    metadata:
      labels:
${labelsBlock('        ', v)}
    spec:
      containers:
${containersBlock(v)}
${imagePullSecretsBlock(v)}
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
${containersBlock(v)}
${imagePullSecretsBlock(v)}
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
${deploymentStrategyBlock(v)}
  template:
    metadata:
      labels:
${labelsBlock('        ', v)}
    spec:
      containers:
${containersBlock(v)}
${imagePullSecretsBlock(v)}
${volumesBlock(v)}
`
}

// ============ 极简 YAML 子集解析（无第三方库，仅解析 k8s 工作负载 YAML 需要的字段） ============
export type YamlNode = string | number | boolean | null | YamlNode[] | { [k: string]: YamlNode }

interface L { indent: number; text: string }

function stripTrailingComment(s: string): string {
  let inS = false, inD = false
  for (let i = 0; i < s.length; i++) {
    const c = s[i]
    if (c === "'" && !inD) inS = !inS
    else if (c === '"' && !inS) inD = !inD
    else if (c === '#' && !inS && !inD) {
      if (i === 0 || s[i - 1] === ' ' || s[i - 1] === '\t') return s.slice(0, i).trimEnd()
    }
  }
  return s
}

function preprocess(src: string): L[] {
  const out: L[] = []
  for (const raw of src.split('\n')) {
    if (raw.trim() === '') continue
    const m = raw.match(/^(\s*)/)
    const indent = m ? m[1].length : 0
    let text = raw.slice(indent)
    if (text.startsWith('#')) continue
    if (text === '---' || text === '...') continue
    text = stripTrailingComment(text)
    out.push({ indent, text })
  }
  return out
}

// 键冒号位置（冒号后是空格或行尾才算键分隔；忽略引号内与值里的冒号如 http://）
function keyColonIndex(s: string): number {
  let inS = false, inD = false
  for (let i = 0; i < s.length; i++) {
    const c = s[i]
    if (c === "'" && !inD) inS = !inS
    else if (c === '"' && !inS) inD = !inD
    else if (c === ':' && !inS && !inD) {
      const after = s[i + 1]
      if (after === undefined || after === ' ' || after === '\t') return i
    }
  }
  return -1
}

function parseScalar(s: string): YamlNode {
  const t = s.trim()
  if (t === '' || t === 'null' || t === 'Null' || t === 'NULL' || t === '~') return null
  if (t === 'true' || t === 'True') return true
  if (t === 'false' || t === 'False') return false
  if ((t.startsWith('"') && t.endsWith('"')) || (t.startsWith("'") && t.endsWith("'"))) return t.slice(1, -1)
  if (/^-?\d+(\.\d+)?$/.test(t)) return Number(t)
  return t
}

function isDash(t: string) { return t === '-' || t.startsWith('- ') }

function parseNode(lines: L[], i: number): { val: YamlNode; next: number } {
  if (i >= lines.length) return { val: null, next: i }
  if (isDash(lines[i].text)) return parseList(lines, i, lines[i].indent)
  return parseMap(lines, i, lines[i].indent)
}

// 键后面的值在下一行：可能是缩进的 map / 同级 compact 列表 / 空
function parseValueAfterKey(lines: L[], i: number, indent: number): { val: YamlNode; next: number } {
  if (i >= lines.length) return { val: null, next: i }
  const nxt = lines[i]
  if (isDash(nxt.text)) return parseList(lines, i, nxt.indent)
  if (nxt.indent > indent) return parseMap(lines, i, nxt.indent)
  return { val: null, next: i }
}

function parseMap(lines: L[], i: number, indent: number): { val: YamlNode; next: number } {
  const obj: Record<string, YamlNode> = {}
  while (i < lines.length) {
    const line = lines[i]
    if (line.indent < indent) break
    if (line.indent > indent) break
    if (isDash(line.text)) break
    const idx = keyColonIndex(line.text)
    if (idx < 0) { i++; continue }
    const key = line.text.slice(0, idx).trim()
    const rest = line.text.slice(idx + 1).trim()
    if (rest === '') {
      const sub = parseValueAfterKey(lines, i + 1, indent)
      obj[key] = sub.val
      i = sub.next
    } else if (/^[|>]/.test(rest)) {
      // 块标量（如 last-applied-configuration 的多行 JSON）：吞掉更深缩进的行
      let j = i + 1
      const chunks: string[] = []
      while (j < lines.length && lines[j].indent > indent) { chunks.push(lines[j].text); j++ }
      obj[key] = chunks.join('\n') || null
      i = j
    } else {
      obj[key] = parseScalar(rest)
      i++
    }
  }
  return { val: obj, next: i }
}

function parseList(lines: L[], i: number, indent: number): { val: YamlNode; next: number } {
  const arr: YamlNode[] = []
  while (i < lines.length) {
    const line = lines[i]
    if (line.indent < indent) break
    if (line.indent > indent) break
    if (!isDash(line.text)) break
    const afterDash = line.text === '-' ? '' : line.text.slice(2).trim()
    if (afterDash === '') {
      if (i + 1 < lines.length && lines[i + 1].indent > indent) {
        const sub = parseNode(lines, i + 1)
        arr.push(sub.val)
        i = sub.next
      } else {
        arr.push(null)
        i++
      }
    } else if (keyColonIndex(afterDash) >= 0) {
      // 内联 map 项：`- key: value` + 后续更深缩进的续行
      const itemObj: Record<string, YamlNode> = {}
      const idx = keyColonIndex(afterDash)
      const key = afterDash.slice(0, idx).trim()
      const rest = afterDash.slice(idx + 1).trim()
      let j = i + 1
      if (rest === '') {
        if (j < lines.length && (lines[j].indent > indent || isDash(lines[j].text))) {
          const sub = parseValueAfterKey(lines, j, indent)
          itemObj[key] = sub.val
          j = sub.next
        } else {
          itemObj[key] = null
        }
      } else {
        itemObj[key] = parseScalar(rest)
      }
      while (j < lines.length && lines[j].indent > indent && !isDash(lines[j].text)) {
        const sub = parseMap(lines, j, lines[j].indent)
        Object.assign(itemObj, sub.val as Record<string, YamlNode>)
        j = sub.next
      }
      arr.push(itemObj)
      i = j
    } else {
      arr.push(parseScalar(afterDash))
      i++
    }
  }
  return { val: arr, next: i }
}

export function parseYaml(src: string): YamlNode {
  const lines = preprocess(src || '')
  if (lines.length === 0) return null
  return parseNode(lines, 0).val
}

export const asMap = (n: YamlNode): Record<string, YamlNode> => (n && typeof n === 'object' && !Array.isArray(n) ? n as Record<string, YamlNode> : {})
export const asArr = (n: YamlNode): YamlNode[] => (Array.isArray(n) ? n : [])
export const asStr = (n: YamlNode): string => (n == null ? '' : String(n))

// ============ 对象 → YAML stringify（够用子集：map/array/string/number/bool/null，2 空格缩进） ============
function escapeStr(s: string): string {
  return s
    .replace(/\\/g, '\\\\')
    .replace(/"/g, '\\"')
    .replace(/\n/g, '\\n')
    .replace(/\r/g, '\\r')
    .replace(/\t/g, '\\t')
}

function needsQuote(s: string): boolean {
  if (s === '') return true
  if (s !== s.trim()) return true
  if (/[\n\r\t]/.test(s)) return true
  if (/^[-?:,\[\]{}#&*!|>'"%@`]/.test(s)) return true
  if (/:\s/.test(s) || s.endsWith(':') || s.includes('#')) return true
  if (/^(true|false|null|~|yes|no|on|off)$/i.test(s)) return true
  if (/^[-+]?(\d+(\.\d+)?|\.\d+)([eE][-+]?\d+)?$/.test(s)) return true
  return false
}

function yamlString(s: string): string {
  return needsQuote(s) ? `"${escapeStr(s)}"` : s
}

function yamlKey(k: string): string {
  return needsQuote(k) ? `"${escapeStr(k)}"` : k
}

function stringifyScalar(v: YamlNode): string {
  if (v === null) return 'null'
  if (typeof v === 'number') return Number.isFinite(v) ? String(v) : 'null'
  if (typeof v === 'boolean') return v ? 'true' : 'false'
  return yamlString(String(v))
}

function stringifyMap(obj: Record<string, YamlNode>, indent: number): string {
  const keys = Object.keys(obj)
  if (keys.length === 0) return '{}'
  const pad = ' '.repeat(indent)
  const lines: string[] = []
  for (const k of keys) {
    const v = obj[k]
    const key = yamlKey(k)
    if (v === null) {
      lines.push(`${pad}${key}:`)
    } else if (Array.isArray(v)) {
      if (v.length === 0) lines.push(`${pad}${key}: []`)
      else { lines.push(`${pad}${key}:`); lines.push(stringifyList(v, indent + 2)) }
    } else if (typeof v === 'object') {
      const m = v as Record<string, YamlNode>
      if (Object.keys(m).length === 0) lines.push(`${pad}${key}: {}`)
      else { lines.push(`${pad}${key}:`); lines.push(stringifyMap(m, indent + 2)) }
    } else {
      lines.push(`${pad}${key}: ${stringifyScalar(v)}`)
    }
  }
  return lines.join('\n')
}

function stringifyList(arr: YamlNode[], indent: number): string {
  const pad = ' '.repeat(indent)
  const lines: string[] = []
  for (const item of arr) {
    if (item === null) {
      lines.push(`${pad}-`)
    } else if (Array.isArray(item)) {
      lines.push(`${pad}-`)
      lines.push(stringifyList(item, indent + 2))
    } else if (typeof item === 'object') {
      lines.push(stringifyListItemMap(item as Record<string, YamlNode>, indent))
    } else {
      lines.push(`${pad}- ${stringifyScalar(item)}`)
    }
  }
  return lines.join('\n')
}

function stringifyListItemMap(obj: Record<string, YamlNode>, indent: number): string {
  const keys = Object.keys(obj)
  const pad = ' '.repeat(indent)
  if (keys.length === 0) return `${pad}- {}`
  const lines: string[] = []
  for (let i = 0; i < keys.length; i++) {
    const k = keys[i]
    const v = obj[k]
    const key = yamlKey(k)
    const prefix = i === 0 ? `${pad}- ${key}` : `${pad}  ${key}`
    if (v === null) {
      lines.push(`${prefix}:`)
    } else if (Array.isArray(v)) {
      if (v.length === 0) lines.push(`${prefix}: []`)
      else { lines.push(`${prefix}:`); lines.push(stringifyList(v, indent + 4)) }
    } else if (typeof v === 'object') {
      const m = v as Record<string, YamlNode>
      if (Object.keys(m).length === 0) lines.push(`${prefix}: {}`)
      else { lines.push(`${prefix}:`); lines.push(stringifyMap(m, indent + 4)) }
    } else {
      lines.push(`${prefix}: ${stringifyScalar(v)}`)
    }
  }
  return lines.join('\n')
}

export function stringifyYaml(node: YamlNode): string {
  if (Array.isArray(node)) return stringifyList(node, 0)
  if (node !== null && typeof node === 'object') return stringifyMap(node as Record<string, YamlNode>, 0)
  return stringifyScalar(node)
}

// ============ 编辑保存：字段级 merge（只覆盖向导涉及的字段，保留高级字段） ============
function labelsNode(v: WorkloadValues): Record<string, YamlNode> {
  const o: Record<string, YamlNode> = { app: v.name }
  for (const it of v.labels || []) if (it && it.key && it.key !== 'app') o[it.key] = it.value ?? ''
  return o
}

interface ResSpec { cpuRequest?: string; cpuLimit?: string; memoryRequest?: string; memoryLimit?: string }

function resourcesNode(r: ResSpec): Record<string, YamlNode> | undefined {
  const req: Record<string, YamlNode> = {}
  const lim: Record<string, YamlNode> = {}
  if (r.cpuRequest) req.cpu = r.cpuRequest
  if (r.memoryRequest) req.memory = r.memoryRequest
  if (r.cpuLimit) lim.cpu = r.cpuLimit
  if (r.memoryLimit) lim.memory = r.memoryLimit
  const out: Record<string, YamlNode> = {}
  if (Object.keys(req).length) out.requests = req
  if (Object.keys(lim).length) out.limits = lim
  return Object.keys(out).length ? out : undefined
}

function volumeMountNode(m: VolumeMountValue): Record<string, YamlNode> {
  const o: Record<string, YamlNode> = { name: m.name, mountPath: m.mountPath }
  if (m.subPath) o.subPath = m.subPath
  if (m.readOnly) o.readOnly = true
  return o
}

function volumeNode(m: VolumeMountValue): Record<string, YamlNode> {
  const o: Record<string, YamlNode> = { name: m.name }
  if (m.type === 'configMap') o.configMap = { name: m.source ?? '' }
  else if (m.type === 'secret') o.secret = { secretName: m.source ?? '' }
  else if (m.type === 'pvc') o.persistentVolumeClaim = { claimName: m.source ?? '' }
  else o.emptyDir = {}
  return o
}

function probeNode(enabled: boolean | undefined, path: string | undefined, port: number | undefined, fallbackPort: number | undefined): Record<string, YamlNode> | undefined {
  if (!enabled) return undefined
  return {
    httpGet: { path: path || '/', port: (port || fallbackPort || 80) },
    initialDelaySeconds: 10,
    periodSeconds: 10,
  }
}

// env：只替换简单 name/value 项，保留 valueFrom 等高级项；envFrom 不在此处、天然保留
function mergeEnv(c0: Record<string, YamlNode>, v: WorkloadValues): void {
  const keep: YamlNode[] = []
  for (const e of asArr(c0.env)) {
    const m = asMap(e)
    if (m.valueFrom != null || m.value == null) keep.push(m)
  }
  for (const e of v.env || []) {
    if (e && e.key) keep.push({ name: e.key, value: e.value ?? '' })
  }
  if (keep.length) c0.env = keep
  else delete c0.env
}

// 合并现有 YAML 与向导值，返回可直接 apply 的 YAML 字符串（编辑模式用）
// merge 策略：只覆盖向导涉及的字段；表单未覆盖的高级字段（annotations、initContainers、securityContext、tolerations、
// valueFrom 环境变量、非向导卷等）一律保留。容器层面：主容器=现有第一个容器（保留其 name 与高级字段），
// 附加容器（v.containers）按 name 匹配现有容器保留高级字段；多容器模式下未列出的普通容器会被移除（表单权威），
// 单容器模式（v.containers 非数组）则保留现有全部 sidecar。initContainers 始终不动。
export function mergeWorkloadYaml(existingYaml: string, v: WorkloadValues, kind: string): string {
  const root = asMap(parseYaml(existingYaml))

  // 剥离服务端管理字段（server-side apply 不接受 status / 只读元数据）
  delete root.status
  const meta = asMap(root.metadata)
  for (const k of ['generation', 'selfLink', 'managedFields', 'resourceVersion', 'uid', 'creationTimestamp']) delete meta[k]
  // metadata.labels 覆盖（向导）；annotations 保留
  meta.labels = labelsNode(v)

  const spec = asMap(root.spec)
  if (kind === 'deployment' || kind === 'statefulset') spec.replicas = v.replicas ?? 1
  if (kind === 'cronjob') spec.schedule = v.schedule || '*/5 * * * *'

  // 更新策略：Deployment → strategy；StatefulSet → podManagementPolicy + updateStrategy
  if (kind === 'deployment') {
    if (v.strategyType === 'Recreate') {
      spec.strategy = { type: 'Recreate' }
    } else {
      const strat = asMap(spec.strategy)
      strat.type = 'RollingUpdate'
      const ru = asMap(strat.rollingUpdate)
      ru.maxSurge = v.maxSurge || '25%'
      ru.maxUnavailable = v.maxUnavailable || '25%'
      strat.rollingUpdate = ru
      spec.strategy = strat
    }
  } else if (kind === 'statefulset') {
    if (v.podManagementPolicy) spec.podManagementPolicy = v.podManagementPolicy
    else delete spec.podManagementPolicy
    spec.updateStrategy = { type: v.updateStrategyType || 'RollingUpdate' }
  }

  // 容器所在 spec（cronjob 走 jobTemplate.spec.template.spec）
  const tplSpec: Record<string, YamlNode> = kind === 'cronjob'
    ? asMap(asMap(asMap(asMap(spec.jobTemplate).spec).template).spec)
    : asMap(asMap(spec.template).spec)

  // 镜像拉取密钥：表单权威（下拉多选）；字段未在表单中（如 CronJob）时保持现有值不动
  if (Array.isArray(v.imagePullSecrets)) {
    if (v.imagePullSecrets.length) tplSpec.imagePullSecrets = v.imagePullSecrets.filter(Boolean).map((n) => ({ name: n }))
    else delete tplSpec.imagePullSecrets
  }

  const containers = asArr(tplSpec.containers)
  const c0 = asMap(containers[0])

  c0.image = v.image
  if (v.imagePullPolicy && v.imagePullPolicy !== 'IfNotPresent') c0.imagePullPolicy = v.imagePullPolicy
  else delete c0.imagePullPolicy
  mergeEnv(c0, v)

  if (kind === 'cronjob') {
    c0.command = ['/bin/sh', '-c', v.command || 'echo hello']
  } else {
    const res = resourcesNode(v)
    if (res) c0.resources = res
    else delete c0.resources
    if (v.containerPort) c0.ports = [{ containerPort: v.containerPort }]
    else delete c0.ports
    if (v.containerCommand && v.containerCommand.length) c0.command = v.containerCommand
    else delete c0.command
    const lp = probeNode(v.livenessEnabled, v.livenessPath, v.livenessPort, v.containerPort)
    if (lp) c0.livenessProbe = lp
    else delete c0.livenessProbe
    const rp = probeNode(v.readinessEnabled, v.readinessPath, v.readinessPort, v.containerPort)
    if (rp) c0.readinessProbe = rp
    else delete c0.readinessProbe

    const wizVols = normVolumes(v)
    const wizNames = new Set(wizVols.map((m) => m.name))
    if (wizVols.length) c0.volumeMounts = wizVols.map(volumeMountNode)
    else delete c0.volumeMounts
    const preservedVols = asArr(tplSpec.volumes).filter((vol) => !wizNames.has(asStr(asMap(vol).name)))
    const newVols = [...wizVols.map(volumeNode), ...preservedVols]
    if (newVols.length) tplSpec.volumes = newVols
    else delete tplSpec.volumes

    // 多容器：v.containers 是数组时表单权威；否则保留现有 sidecar（向后兼容）
    if (Array.isArray(v.containers)) {
      const byName = new Map<string, Record<string, YamlNode>>()
      for (const c of containers) { const m = asMap(c); if (asStr(m.name)) byName.set(asStr(m.name), m) }
      const extras: YamlNode[] = []
      for (const fc of v.containers) {
        if (!fc || !fc.name || !fc.image) continue
        const c: Record<string, YamlNode> = { ...(byName.get(fc.name) || {}), name: fc.name, image: fc.image }
        if (fc.containerPort) c.ports = [{ containerPort: fc.containerPort }]
        else delete c.ports
        const er = resourcesNode(fc)
        if (er) c.resources = er
        else delete c.resources
        if (fc.command && fc.command.length) c.command = fc.command
        else delete c.command
        extras.push(c)
      }
      tplSpec.containers = [c0, ...extras]
    } else {
      tplSpec.containers = [c0, ...containers.slice(1)]
    }
  }

  return stringifyYaml(root)
}

// ============ YAML → 向导表单值（编辑设置回填；与 buildYaml / mergeWorkloadYaml 互逆） ============
function strMapOf(n: YamlNode): Record<string, string> {
  const m = asMap(n)
  const out: Record<string, string> = {}
  for (const k of Object.keys(m)) out[k] = asStr(m[k])
  return out
}

function containerToValue(c: Record<string, YamlNode>): WorkloadContainerValue {
  const res = asMap(c.resources)
  const ports = asArr(c.ports).map((x) => asMap(x))
  const cp = ports.find((p) => p.containerPort != null)
  const cmd = asArr(c.command).map((x) => asStr(x))
  return {
    name: asStr(c.name),
    image: asStr(c.image),
    containerPort: cp ? Number(cp.containerPort) : undefined,
    cpuRequest: asStr(asMap(res.requests).cpu) || undefined,
    cpuLimit: asStr(asMap(res.limits).cpu) || undefined,
    memoryRequest: asStr(asMap(res.requests).memory) || undefined,
    memoryLimit: asStr(asMap(res.limits).memory) || undefined,
    command: cmd.length ? cmd : undefined,
  }
}

export function yamlToValues(root: YamlNode, kind: string): Partial<WorkloadValues> {
  const spec = asMap(asMap(root).spec)
  const meta = asMap(asMap(root).metadata)
  const tplSpec = kind === 'cronjob'
    ? asMap(asMap(asMap(asMap(spec.jobTemplate).spec).template).spec)
    : asMap(asMap(spec.template).spec)
  const containers = asArr(tplSpec.containers)
  const c0 = asMap(containers[0])

  const resources = asMap(c0.resources)
  const requests = asMap(resources.requests)
  const limits = asMap(resources.limits)

  const env = asArr(c0.env).map((e) => asMap(e)).filter((e) => e.value != null)
    .map((e) => ({ key: asStr(e.name), value: asStr(e.value) }))

  const rawLabels = strMapOf(meta.labels)
  delete rawLabels.app
  const labels = Object.entries(rawLabels).map(([key, value]) => ({ key, value }))

  const volumes: VolumeMountValue[] = []
  const volList = asArr(tplSpec.volumes).map((x) => asMap(x))
  const mounts = asArr(c0.volumeMounts).map((x) => asMap(x))
  for (const m of mounts) {
    const vn = asStr(m.name)
    const vol = volList.find((x) => asStr(x.name) === vn) || {}
    let type: VolumeType = 'emptyDir'
    let source: string | undefined
    if (vol.configMap != null) { type = 'configMap'; source = asStr(asMap(vol.configMap).name) }
    else if (vol.secret != null) { type = 'secret'; source = asStr(asMap(vol.secret).secretName) }
    else if (vol.persistentVolumeClaim != null) { type = 'pvc'; source = asStr(asMap(vol.persistentVolumeClaim).claimName) }
    else if (vol.emptyDir != null) { type = 'emptyDir' }
    volumes.push({ name: vn, type, source, mountPath: asStr(m.mountPath), subPath: m.subPath != null ? asStr(m.subPath) : undefined, readOnly: m.readOnly === true })
  }

  const live = asMap(c0.livenessProbe)
  const liveGet = asMap(live.httpGet)
  const ready = asMap(c0.readinessProbe)
  const readyGet = asMap(ready.httpGet)

  const ports = asArr(c0.ports).map((x) => asMap(x))
  const cp = ports.find((p) => p.containerPort != null)

  const out: Partial<WorkloadValues> = {
    name: asStr(meta.name),
    namespace: asStr(meta.namespace),
    image: asStr(c0.image),
    imagePullPolicy: asStr(c0.imagePullPolicy) || 'IfNotPresent',
    replicas: spec.replicas != null ? Number(spec.replicas) : undefined,
    cpuRequest: asStr(requests.cpu) || undefined,
    cpuLimit: asStr(limits.cpu) || undefined,
    memoryRequest: asStr(requests.memory) || undefined,
    memoryLimit: asStr(limits.memory) || undefined,
    env,
    labels,
    volumes,
    livenessEnabled: live.httpGet != null,
    livenessPath: asStr(liveGet.path) || '/healthz',
    livenessPort: liveGet.port != null ? Number(liveGet.port) : undefined,
    readinessEnabled: ready.httpGet != null,
    readinessPath: asStr(readyGet.path) || '/healthz',
    readinessPort: readyGet.port != null ? Number(readyGet.port) : undefined,
    containers: containers.slice(1).map((x) => containerToValue(asMap(x))),
  }
  if (cp) out.containerPort = Number(cp.containerPort)

  const cmd = asArr(c0.command).map((x) => asStr(x))
  if (kind === 'cronjob') {
    out.schedule = asStr(spec.schedule)
    out.command = cmd.length >= 3 ? asStr(cmd[2]) : (cmd.length ? asStr(cmd[cmd.length - 1]) : '')
  } else if (cmd.length) {
    out.containerCommand = cmd
  }

  // 更新策略
  if (kind === 'deployment') {
    const strat = asMap(spec.strategy)
    out.strategyType = asStr(strat.type) || 'RollingUpdate'
    const ru = asMap(strat.rollingUpdate)
    if (ru.maxSurge != null) out.maxSurge = asStr(ru.maxSurge)
    if (ru.maxUnavailable != null) out.maxUnavailable = asStr(ru.maxUnavailable)
  } else if (kind === 'statefulset') {
    out.podManagementPolicy = asStr(spec.podManagementPolicy) || 'OrderedReady'
    out.updateStrategyType = asStr(asMap(spec.updateStrategy).type) || 'RollingUpdate'
  }

  // 镜像拉取密钥
  const ips = asArr(tplSpec.imagePullSecrets).map((x) => asStr(asMap(x).name)).filter(Boolean)
  if (ips.length) out.imagePullSecrets = ips

  return out
}
