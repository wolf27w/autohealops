// 匹配器构建器（复用于屏蔽规则 / 抑制规则的 source·target 匹配条件）
// 受控组件：value / onChange，可直接嵌进 antd Form.Item（name="matchers" 等）。
import { Button, Input, Select, Space } from 'antd'
import { MinusCircleOutlined, PlusOutlined } from '@ant-design/icons'
import type { SilenceMatcher } from '../../services/alertSilenceApi'

export const MATCHER_KEYS = [
  { value: 'cluster_id', label: '集群 ID' },
  { value: 'namespace', label: '命名空间' },
  { value: 'resource_type', label: '资源类型' },
  { value: 'resource_name', label: '资源名' },
  { value: 'fault_type', label: '故障类型' },
  { value: 'severity', label: '级别' },
]

export const MATCHER_OPS = ['=', '!=', '=~', '!~']

const EMPTY_ROW: SilenceMatcher = { key: '', op: '=', value: '' }

interface MatcherEditorProps {
  value?: SilenceMatcher[]
  onChange?: (value: SilenceMatcher[]) => void
  valuePlaceholder?: string
}

export default function MatcherEditor({ value, onChange, valuePlaceholder }: MatcherEditorProps) {
  const rows: SilenceMatcher[] = value && value.length > 0 ? value : [EMPTY_ROW]

  const update = (index: number, patch: Partial<SilenceMatcher>) => {
    onChange?.(rows.map((r, i) => (i === index ? { ...r, ...patch } : r)))
  }

  const add = () => onChange?.([...rows, { ...EMPTY_ROW }])

  const remove = (index: number) => {
    const next = rows.filter((_, i) => i !== index)
    onChange?.(next.length > 0 ? next : [{ ...EMPTY_ROW }])
  }

  return (
    <div>
      {rows.map((r, i) => (
        <Space key={i} align="baseline" style={{ display: 'flex', marginBottom: 8 }}>
          <Select
            value={r.key || undefined}
            placeholder="字段"
            style={{ width: 132 }}
            options={MATCHER_KEYS}
            onChange={(v: string) => update(i, { key: v })}
          />
          <Select
            value={r.op}
            style={{ width: 76 }}
            options={MATCHER_OPS.map((o) => ({ value: o, label: o }))}
            onChange={(v: string) => update(i, { op: v })}
          />
          <Input
            value={r.value}
            placeholder={valuePlaceholder || '值（=~ / !~ 填正则）'}
            style={{ width: 260 }}
            onChange={(e) => update(i, { value: e.target.value })}
          />
          <Button
            type="text"
            danger
            icon={<MinusCircleOutlined />}
            onClick={() => remove(i)}
            disabled={rows.length <= 1}
          />
        </Space>
      ))}
      <Button size="small" type="dashed" icon={<PlusOutlined />} onClick={add} block>
        添加条件
      </Button>
    </div>
  )
}
