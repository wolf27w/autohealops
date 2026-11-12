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

function resourcesNode(v: WorkloadValues): Record<string, YamlNode> | undefined {
  const req: Record<string, YamlNode> = {}
  const lim: Record<string, YamlNode> = {}
  if (v.cpuRequest) req.cpu = v.cpuRequest
  if (v.memoryRequest) req.memory = v.memoryRequest
  if (v.cpuLimit) lim.cpu = v.cpuLimit
  if (v.memoryLimit) lim.memory = v.memoryLimit
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
export function mergeWorkloadYaml(existingYaml: string, v: WorkloadValues, kind: string): string {
  const root = asMap(parseYaml(existingYaml))

  // 剥离服务端管理字段（server-side apply 不接受 status / 只读元数据）
  delete root.status
  const meta = asMap(root.metadata)
  for (const k of ['generation', 'selfLink', 'managedFields', 'resourceVersion', 'uid', 'creationTimestamp']) delete meta[k]
  // metadata.labels 覆盖（向导）
  meta.labels = labelsNode(v)

  const spec = asMap(root.spec)
  if (kind === 'deployment' || kind === 'statefulset') spec.replicas = v.replicas ?? 1
  if (kind === 'cronjob') spec.schedule = v.schedule || '*/5 * * * *'

  // 容器所在 spec（cronjob 走 jobTemplate.spec.template.spec）
  const tplSpec: Record<string, YamlNode> = kind === 'cronjob'
    ? asMap(asMap(asMap(asMap(spec.jobTemplate).spec).template).spec)
    : asMap(asMap(spec.template).spec)

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
  }

  return stringifyYaml(root)
}
