// 日志高亮规则配置：关键字/正则 → 颜色，按顺序匹配（不区分大小写）。
// 规则保存在 localStorage（无后端存储需求）。组件 = 高亮设置 Popover；同时导出
// 规则类型 / 默认规则 / 读写函数 / applyHighlight（把一行按规则切成着色片段）。
import { Button, Input, Popover, Space, Switch, Typography } from 'antd'
import { BgColorsOutlined, DeleteOutlined, PlusOutlined } from '@ant-design/icons'

export interface HighlightRule {
  id: string
  pattern: string      // 关键字（isRegex=false）或正则源（isRegex=true）
  isRegex: boolean
  color: string        // 命中文本色（十六进制）
}

const STORAGE_KEY = 'autohealops.log-highlight-rules'

// 默认规则：等价于原来的固定 ERROR/WARN/INFO 着色
export const DEFAULT_RULES: HighlightRule[] = [
  { id: 'error', pattern: '\\b(ERROR|FATAL|ERR)\\b', isRegex: true, color: '#ff7875' },
  { id: 'warn', pattern: '\\bWARN(ING)?\\b', isRegex: true, color: '#ffc069' },
  { id: 'info', pattern: '\\bINFO\\b', isRegex: true, color: '#95de64' },
]

export function loadRules(): HighlightRule[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return DEFAULT_RULES
    const arr = JSON.parse(raw)
    if (!Array.isArray(arr)) return DEFAULT_RULES
    const valid = arr.filter((r) => r && typeof r.pattern === 'string' && r.pattern && typeof r.color === 'string' && r.color)
    return valid.length ? valid : DEFAULT_RULES
  } catch {
    return DEFAULT_RULES
  }
}

export function saveRules(rules: HighlightRule[]) {
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify(rules)) } catch { /* 忽略写入失败 */ }
}

export interface HighlightSeg { text: string; color?: string; bold?: boolean }

const escapeRegExp = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')

function compileRule(r: HighlightRule): RegExp | null {
  try {
    return new RegExp(r.isRegex ? r.pattern : escapeRegExp(r.pattern), 'gi')
  } catch {
    return null
  }
}

// 把一行日志按高亮规则切成着色片段：规则数组顺序即优先级，重叠时先命中者生效。
export function applyHighlight(line: string, rules: HighlightRule[]): HighlightSeg[] {
  if (!rules.length || !line) return [{ text: line }]
  const hits: { start: number; end: number; rule: HighlightRule }[] = []
  for (const r of rules) {
    const re = compileRule(r)
    if (!re) continue
    re.lastIndex = 0
    let m: RegExpExecArray | null
    while ((m = re.exec(line)) !== null) {
      if (m[0].length === 0) { re.lastIndex++; continue }
      hits.push({ start: m.index, end: m.index + m[0].length, rule: r })
    }
  }
  if (!hits.length) return [{ text: line }]
  hits.sort((a, b) => a.start - b.start)
  const segs: HighlightSeg[] = []
  let pos = 0
  for (const h of hits) {
    if (h.end <= pos) continue          // 与已保留区间重叠，跳过
    const start = Math.max(h.start, pos)
    if (start > pos) segs.push({ text: line.slice(pos, start) })
    segs.push({ text: line.slice(start, h.end), color: h.rule.color, bold: true })
    pos = h.end
  }
  if (pos < line.length) segs.push({ text: line.slice(pos) })
  return segs
}

export function LogHighlightSettings({ rules, onChange }: {
  rules: HighlightRule[]
  onChange: (r: HighlightRule[]) => void
}) {
  const update = (id: string, patch: Partial<HighlightRule>) =>
    onChange(rules.map((r) => (r.id === id ? { ...r, ...patch } : r)))
  const remove = (id: string) => onChange(rules.filter((r) => r.id !== id))
  const add = () => onChange([...rules, { id: `r${Date.now()}`, pattern: '', isRegex: false, color: '#e5c07b' }])

  const content = (
    <div style={{ width: 380 }}>
      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        命中文本按规则着色（正则或关键字，不区分大小写）；多规则按顺序匹配，重叠时先命中生效。
      </Typography.Text>
      <div style={{ marginTop: 8, display: 'flex', flexDirection: 'column', gap: 8, maxHeight: 340, overflow: 'auto' }}>
        {rules.length === 0 && (
          <div style={{ color: '#8c8c8c', fontSize: 12, textAlign: 'center', padding: '12px 0' }}>暂无规则</div>
        )}
        {rules.map((r) => (
          <div key={r.id} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <input
              type="color"
              value={r.color}
              onChange={(e) => update(r.id, { color: e.target.value })}
              style={{ width: 30, height: 30, padding: 0, border: '1px solid #434343', borderRadius: 4, background: 'none', cursor: 'pointer' }}
            />
            <Input
              size="small"
              style={{ flex: 1 }}
              value={r.pattern}
              placeholder={r.isRegex ? '正则，如 \\bERROR\\b' : '关键字'}
              onChange={(e) => update(r.id, { pattern: e.target.value })}
            />
            <Space size={4} style={{ alignItems: 'center' }}>
              <Switch size="small" checked={r.isRegex} onChange={(v) => update(r.id, { isRegex: v })} />
              <span style={{ fontSize: 12, color: '#8c8c8c', whiteSpace: 'nowrap' }}>正则</span>
            </Space>
            <Button size="small" type="text" danger icon={<DeleteOutlined />} onClick={() => remove(r.id)} />
          </div>
        ))}
      </div>
      <Button size="small" block style={{ marginTop: 8 }} icon={<PlusOutlined />} onClick={add}>添加规则</Button>
    </div>
  )

  return (
    <Popover trigger="click" placement="bottomRight" content={content} title="日志高亮规则">
      <Button icon={<BgColorsOutlined />}>高亮</Button>
    </Popover>
  )
}
