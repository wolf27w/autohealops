// 告警屏蔽（P2-R11 重做）：屏蔽规则 + 抑制规则两个页签
// 屏蔽规则：匹配条件命中时只拦通知、不丢数据；支持一次性 / 每天 / 每周 周期 + 预览影响
// 抑制规则：source 告警抑制 target 告警（Alertmanager inhibition 风格）
import { useCallback, useEffect, useState } from 'react'
import {
  Button, Card, Checkbox, DatePicker, Form, Input, Modal, Popconfirm, Radio, Select, Space, Switch, Table, Tabs, Tag, TimePicker, Typography, message,
} from 'antd'
import { PlusOutlined, ReloadOutlined } from '@ant-design/icons'
import dayjs, { type Dayjs } from 'dayjs'
import MatcherEditor from '../components/alert/MatcherEditor'
import {
  createAlertSilence, createInhibition, deleteAlertSilence, deleteInhibition, getAlertSilences, getInhibitions,
  previewAlertSilence, updateAlertSilence, updateInhibition,
  type AlertSilence, type InhibitionRule, type RecurrenceType, type SilenceMatcher, type SilencePreviewResult, type SilenceRecurrence,
} from '../services/alertSilenceApi'

// ============ 常量 / 工具 ============
const DAY_SHORT: Record<number, string> = { 1: '一', 2: '二', 3: '三', 4: '四', 5: '五', 6: '六', 7: '日' }
const WEEK_OPTIONS = [1, 2, 3, 4, 5, 6, 7].map((d) => ({ label: `周${DAY_SHORT[d]}`, value: d }))

// RangePicker 值：兼容 antd setFieldsValue 的 RecursivePartial（tuple 会被拍平成数组）
type RangeValue = (Dayjs | null)[] | undefined

interface SilenceFormValues {
  name?: string
  matchers?: SilenceMatcher[]
  recurrenceType: RecurrenceType
  onceRange?: RangeValue
  overallRange?: RangeValue
  dailyWindow?: RangeValue
  weeklyDays?: number[]
  comment?: string
}

interface InhibitionFormValues {
  name?: string
  source_matchers?: SilenceMatcher[]
  target_matchers?: SilenceMatcher[]
  equal_labels?: string[]
  enabled?: boolean
  comment?: string
}

/** 解析后端时间（兼容 "YYYY-MM-DD HH:mm:ss" 与 ISO "T" 两种格式） */
function parseTs(s?: string | null): Dayjs | null {
  if (!s) return null
  const d = dayjs(s.includes('T') ? s : s.replace(' ', 'T'))
  return d.isValid() ? d : null
}

function toIso(d: Dayjs | null | undefined): string | undefined {
  return d ? d.format('YYYY-MM-DDTHH:mm:ss') : undefined
}

/** 过滤出「字段 + 值」都填了的有效匹配条件 */
function collectMatchers(ms?: SilenceMatcher[]): SilenceMatcher[] {
  return (ms || []).filter((m) => !!m && !!m.key && m.value !== undefined && m.value !== '')
}

/** 把星期数组压成 "一~五" / "一、三" 这样的紧凑文案 */
function compactDays(days: number[]): string {
  const sorted = Array.from(new Set(days)).sort((a, b) => a - b)
  if (sorted.length === 0) return ''
  const ranges: string[] = []
  let start = sorted[0]
  let prev = sorted[0]
  for (let i = 1; i < sorted.length; i++) {
    if (sorted[i] === prev + 1) {
      prev = sorted[i]
      continue
    }
    ranges.push(start === prev ? DAY_SHORT[start] : `${DAY_SHORT[start]}~${DAY_SHORT[prev]}`)
    start = sorted[i]
    prev = sorted[i]
  }
  ranges.push(start === prev ? DAY_SHORT[start] : `${DAY_SHORT[start]}~${DAY_SHORT[prev]}`)
  return ranges.join('、')
}

/** 生效规则描述文案 */
function describeRecurrence(r?: SilenceRecurrence | null): string {
  if (!r || r.type === 'once') return '一次性'
  const window = r.start && r.end ? `${r.start}-${r.end}` : ''
  if (r.type === 'daily') return window ? `每天 ${window}` : '每天'
  const days = compactDays(r.dailyDays || [])
  const daysText = days ? `每周${days}` : '每周'
  return window ? `${daysText} ${window}` : daysText
}

/** 由表单值构造创建/编辑请求体 */
function buildSilencePayload(v: SilenceFormValues) {
  const matchers = collectMatchers(v.matchers)
  let starts_at: string | undefined
  let ends_at: string | undefined
  let recurrence: SilenceRecurrence | null = null
  if (v.recurrenceType === 'once') {
    starts_at = toIso(v.onceRange?.[0])
    ends_at = toIso(v.onceRange?.[1])
  } else {
    starts_at = v.overallRange?.[0] ? `${v.overallRange[0].format('YYYY-MM-DD')}T00:00:00` : undefined
    ends_at = v.overallRange?.[1] ? `${v.overallRange[1].format('YYYY-MM-DD')}T23:59:59` : undefined
    recurrence = {
      type: v.recurrenceType,
      start: v.dailyWindow?.[0] ? v.dailyWindow[0].format('HH:mm') : undefined,
      end: v.dailyWindow?.[1] ? v.dailyWindow[1].format('HH:mm') : undefined,
      ...(v.recurrenceType === 'weekly' ? { dailyDays: v.weeklyDays || [] } : {}),
    }
  }
  return {
    name: v.name || '',
    matchers,
    starts_at,
    ends_at,
    comment: v.comment || '',
    recurrence,
  }
}

/** 编辑回填：后端行 → 表单值 */
function toFormValues(r: AlertSilence): SilenceFormValues {
  const type: RecurrenceType = r.recurrence?.type ?? 'once'
  const base: SilenceFormValues = {
    name: r.name,
    matchers: r.matchers,
    recurrenceType: type,
    comment: r.comment,
  }
  if (type === 'once') {
    base.onceRange = [parseTs(r.starts_at), parseTs(r.ends_at)]
    return base
  }
  base.overallRange = [parseTs(r.starts_at), parseTs(r.ends_at)]
  base.dailyWindow = r.recurrence?.start
    ? [dayjs(r.recurrence.start, 'HH:mm'), dayjs(r.recurrence.end, 'HH:mm')]
    : undefined
  base.weeklyDays = r.recurrence?.dailyDays ?? []
  return base
}

/** 当前状态：生效中（绿）/ 未生效（灰）/ 已过期（红） */
function silenceState(r: AlertSilence): { text: string; color: string } {
  if (r.active) return { text: '生效中', color: 'green' }
  const ends = parseTs(r.ends_at)
  if (ends && ends.valueOf() < Date.now()) return { text: '已过期', color: 'red' }
  return { text: '未生效', color: 'default' }
}

function matcherTags(ms?: SilenceMatcher[], color = 'blue') {
  const list = ms || []
  if (list.length === 0) return <span style={{ color: '#999' }}>—</span>
  return (
    <Space size={[4, 4]} wrap>
      {list.map((m, i) => (
        <Tag key={`${m.key}-${m.op}-${m.value}-${i}`} color={color}>
          {m.key}
          {m.op}
          {m.value}
        </Tag>
      ))}
    </Space>
  )
}

// ============ 屏蔽规则页签 ============
function SilenceRules() {
  const [rows, setRows] = useState<AlertSilence[]>([])
  const [loading, setLoading] = useState(false)
  const [open, setOpen] = useState(false)
  const [editing, setEditing] = useState<AlertSilence | null>(null)
  const [saving, setSaving] = useState(false)
  const [previewLoading, setPreviewLoading] = useState(false)
  const [previewResult, setPreviewResult] = useState<SilencePreviewResult | null>(null)
  const [form] = Form.useForm<SilenceFormValues>()
  const recurrenceType = Form.useWatch('recurrenceType', form) ?? 'once'

  const load = useCallback(() => {
    setLoading(true)
    getAlertSilences()
      .then((d) => setRows(d || []))
      .catch(() => setRows([]))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => { load() }, [load])

  const openCreate = () => {
    setEditing(null)
    setPreviewResult(null)
    form.resetFields()
    form.setFieldsValue({
      name: '',
      matchers: [{ key: '', op: '=', value: '' }],
      recurrenceType: 'once',
      onceRange: [dayjs(), dayjs().add(2, 'hour')],
      comment: '',
    })
    setOpen(true)
  }

  const openEdit = (r: AlertSilence) => {
    setEditing(r)
    setPreviewResult(null)
    form.resetFields()
    form.setFieldsValue(toFormValues(r))
    setOpen(true)
  }

  const doPreview = async () => {
    const v = form.getFieldsValue(true) as SilenceFormValues
    const matchers = collectMatchers(v.matchers)
    if (matchers.length === 0) {
      message.warning('请先填写至少一条匹配条件（字段和值）')
      return
    }
    const payload = buildSilencePayload(v)
    setPreviewLoading(true)
    setPreviewResult(null)
    try {
      const res = await previewAlertSilence({
        matchers,
        recurrence: payload.recurrence ?? undefined,
        starts_at: payload.starts_at,
        ends_at: payload.ends_at,
      })
      setPreviewResult(res)
    } catch {
      // 拦截器已弹错误提示
    } finally {
      setPreviewLoading(false)
    }
  }

  const submit = async () => {
    const v = await form.validateFields().catch(() => null)
    if (!v) return
    const matchers = collectMatchers(v.matchers)
    if (matchers.length === 0) {
      message.warning('请至少填写一条匹配条件')
      return
    }
    const payload = buildSilencePayload(v)
    if (!payload.starts_at || !payload.ends_at) {
      message.warning('请填写生效起止时间')
      return
    }
    if (v.recurrenceType !== 'once' && (!payload.recurrence?.start || !payload.recurrence?.end)) {
      message.warning('请填写每日生效时间段')
      return
    }
    if (v.recurrenceType === 'weekly' && (payload.recurrence?.dailyDays || []).length === 0) {
      message.warning('请选择每周生效的星期')
      return
    }
    setSaving(true)
    try {
      if (editing) {
        await updateAlertSilence(editing.id, payload)
        message.success('已保存')
      } else {
        await createAlertSilence(payload)
        message.success('已创建')
      }
      setOpen(false)
      load()
    } catch {
      // 拦截器已弹错误提示
    } finally {
      setSaving(false)
    }
  }

  const toggleSilence = async (r: AlertSilence) => {
    const nextEnabled = !(r.enabled ?? true)
    try {
      await updateAlertSilence(r.id, {
        name: r.name,
        matchers: r.matchers,
        starts_at: r.starts_at,
        ends_at: r.ends_at,
        comment: r.comment,
        recurrence: r.recurrence,
        enabled: nextEnabled,
      })
      message.success(nextEnabled ? '已启用' : '已停用')
      load()
    } catch {
      // 拦截器已弹错误提示
    }
  }

  const remove = async (r: AlertSilence) => {
    await deleteAlertSilence(r.id)
    message.success('已删除')
    load()
  }

  const columns = [
    { title: '名称', dataIndex: 'name', width: 160, render: (v: string) => v || <span style={{ color: '#999' }}>（未命名）</span> },
    { title: '匹配条件', dataIndex: 'matchers', render: (m: SilenceMatcher[]) => matcherTags(m) },
    { title: '生效规则', dataIndex: 'recurrence', width: 190, render: (r: SilenceRecurrence | null) => describeRecurrence(r) },
    {
      title: '当前状态', dataIndex: 'active', width: 100,
      render: (_: unknown, r: AlertSilence) => {
        const s = silenceState(r)
        return <Tag color={s.color}>{s.text}</Tag>
      },
    },
    { title: '创建人', dataIndex: 'created_by', width: 100, render: (v: string) => v || '-' },
    {
      title: '操作', width: 160, render: (_: unknown, r: AlertSilence) => (
        <Space size="small">
          <a onClick={() => openEdit(r)}>编辑</a>
          <a onClick={() => toggleSilence(r)}>{r.enabled === false ? '启用' : '停用'}</a>
          <Popconfirm title="删除该屏蔽规则？" onConfirm={() => remove(r)}>
            <a style={{ color: '#cf1322' }}>删除</a>
          </Popconfirm>
        </Space>
      ),
    },
  ]

  return (
    <Card
      size="small"
      title="屏蔽规则"
      extra={
        <Space>
          <Button icon={<ReloadOutlined />} onClick={load}>刷新</Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>新建屏蔽</Button>
        </Space>
      }
    >
      <Table rowKey="id" size="small" loading={loading} dataSource={rows} columns={columns} pagination={{ pageSize: 20 }} scroll={{ x: 1000 }} />

      <Modal
        open={open}
        title={editing ? '编辑告警屏蔽' : '新建告警屏蔽'}
        onOk={submit}
        onCancel={() => setOpen(false)}
        width={760}
        confirmLoading={saving}
        destroyOnClose
      >
        <Form form={form} layout="vertical">
          <Form.Item name="name" label="名称（可选）">
            <Input placeholder="如：压测期间屏蔽 xxx 告警" />
          </Form.Item>

          <Form.Item label="匹配条件（全部命中才屏蔽）" required>
            <Form.Item name="matchers" noStyle
              rules={[{ validator: (_r, v: SilenceMatcher[] | undefined) => (collectMatchers(v).length > 0 ? Promise.resolve() : Promise.reject(new Error('请至少填写一条匹配条件'))) }]}>
              <MatcherEditor />
            </Form.Item>
          </Form.Item>

          <Form.Item name="recurrenceType" label="生效方式" initialValue="once">
            <Radio.Group
              optionType="button"
              options={[
                { label: '一次性', value: 'once' },
                { label: '每天', value: 'daily' },
                { label: '每周', value: 'weekly' },
              ]}
            />
          </Form.Item>

          {recurrenceType === 'once' ? (
            <Form.Item name="onceRange" label="生效起止时间" rules={[{ required: true, message: '请选择起止时间' }]}>
              <DatePicker.RangePicker showTime format="YYYY-MM-DD HH:mm" style={{ width: '100%' }} />
            </Form.Item>
          ) : (
            <>
              <Form.Item name="overallRange" label="整体生效日期范围" rules={[{ required: true, message: '请选择日期范围' }]}>
                <DatePicker.RangePicker format="YYYY-MM-DD" style={{ width: '100%' }} />
              </Form.Item>
              {recurrenceType === 'weekly' && (
                <Form.Item name="weeklyDays" label="每周生效的星期" rules={[{ required: true, message: '请选择星期' }]}>
                  <Checkbox.Group options={WEEK_OPTIONS} />
                </Form.Item>
              )}
              <Form.Item name="dailyWindow" label="每日生效时间段" rules={[{ required: true, message: '请选择时间段' }]}>
                <TimePicker.RangePicker format="HH:mm" style={{ width: '100%' }} />
              </Form.Item>
            </>
          )}

          <Form.Item name="comment" label="备注">
            <Input.TextArea rows={2} />
          </Form.Item>

          <div>
            <Button onClick={doPreview} loading={previewLoading} icon={<ReloadOutlined />}>预览影响（近 7 天）</Button>
            {previewResult && (
              <div style={{ border: '1px solid #f0f0f0', borderRadius: 6, padding: 12, marginTop: 12, background: '#fafafa' }}>
                <Space direction="vertical" size={8} style={{ width: '100%' }}>
                  <div>
                    近 7 天命中告警：
                    <Typography.Text strong style={{ fontSize: 18, color: previewResult.matched_total > 0 ? '#cf1322' : '#52c41a' }}>
                      {previewResult.matched_total}
                    </Typography.Text>
                    条
                  </div>
                  <div>
                    按故障类型：
                    {Object.keys(previewResult.by_fault_type || {}).length === 0 ? (
                      <span style={{ color: '#999' }}>无</span>
                    ) : (
                      <Space size={[4, 4]} wrap>
                        {Object.entries(previewResult.by_fault_type || {}).map(([ft, n]) => (
                          <Tag key={ft} color="orange">{ft} × {n}</Tag>
                        ))}
                      </Space>
                    )}
                  </div>
                  <div>
                    样例：
                    {(previewResult.samples || []).length === 0 ? (
                      <span style={{ color: '#999' }}>无</span>
                    ) : (
                      <Table
                        rowKey={(s) => String(s.id)}
                        size="small"
                        pagination={false}
                        dataSource={previewResult.samples || []}
                        columns={[
                          { title: '故障类型', dataIndex: 'fault_type', width: 140 },
                          { title: '级别', dataIndex: 'severity', width: 80 },
                          { title: '资源', dataIndex: 'resource_name' },
                          { title: '检测时间', dataIndex: 'detected_at', width: 160, render: (v: string) => (v ? String(v).slice(0, 19) : '-') },
                        ]}
                      />
                    )}
                  </div>
                </Space>
              </div>
            )}
          </div>
        </Form>
      </Modal>
    </Card>
  )
}

// ============ 抑制规则页签 ============
function InhibitionRules() {
  const [rows, setRows] = useState<InhibitionRule[]>([])
  const [loading, setLoading] = useState(false)
  const [open, setOpen] = useState(false)
  const [editing, setEditing] = useState<InhibitionRule | null>(null)
  const [saving, setSaving] = useState(false)
  const [form] = Form.useForm<InhibitionFormValues>()

  const load = useCallback(() => {
    setLoading(true)
    getInhibitions()
      .then((d) => setRows(d || []))
      .catch(() => setRows([]))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => { load() }, [load])

  const openCreate = () => {
    setEditing(null)
    form.resetFields()
    form.setFieldsValue({
      name: '',
      source_matchers: [{ key: '', op: '=', value: '' }],
      target_matchers: [{ key: '', op: '=', value: '' }],
      equal_labels: [],
      enabled: true,
      comment: '',
    })
    setOpen(true)
  }

  const openEdit = (r: InhibitionRule) => {
    setEditing(r)
    form.resetFields()
    form.setFieldsValue({
      name: r.name,
      source_matchers: r.source_matchers,
      target_matchers: r.target_matchers,
      equal_labels: r.equal_labels,
      enabled: r.enabled,
      comment: r.comment,
    })
    setOpen(true)
  }

  const submit = async () => {
    const v = await form.validateFields().catch(() => null)
    if (!v) return
    const source = collectMatchers(v.source_matchers)
    const target = collectMatchers(v.target_matchers)
    if (source.length === 0 || target.length === 0) {
      message.warning('源匹配条件和目标匹配条件都至少填一条')
      return
    }
    const payload = {
      name: v.name || '',
      source_matchers: source,
      target_matchers: target,
      equal_labels: v.equal_labels || [],
      enabled: v.enabled ?? true,
      comment: v.comment || '',
    }
    setSaving(true)
    try {
      if (editing) {
        await updateInhibition(editing.id, payload)
        message.success('已保存')
      } else {
        await createInhibition(payload)
        message.success('已创建')
      }
      setOpen(false)
      load()
    } catch {
      // 拦截器已弹错误提示
    } finally {
      setSaving(false)
    }
  }

  const toggle = async (r: InhibitionRule, enabled: boolean) => {
    try {
      await updateInhibition(r.id, {
        name: r.name,
        source_matchers: r.source_matchers,
        target_matchers: r.target_matchers,
        equal_labels: r.equal_labels,
        enabled,
        comment: r.comment,
      })
      message.success(enabled ? '已启用' : '已停用')
      load()
    } catch {
      load()
    }
  }

  const remove = async (r: InhibitionRule) => {
    await deleteInhibition(r.id)
    message.success('已删除')
    load()
  }

  const columns = [
    { title: '名称', dataIndex: 'name', width: 150, render: (v: string) => v || <span style={{ color: '#999' }}>（未命名）</span> },
    { title: '源匹配', dataIndex: 'source_matchers', render: (m: SilenceMatcher[]) => matcherTags(m, 'geekblue') },
    { title: '目标匹配', dataIndex: 'target_matchers', render: (m: SilenceMatcher[]) => matcherTags(m, 'purple') },
    { title: '等价标签', dataIndex: 'equal_labels', width: 200, render: (v: string[]) => (v && v.length ? <Space size={[4, 4]} wrap>{v.map((x) => <Tag key={x}>{x}</Tag>)}</Space> : <span style={{ color: '#999' }}>—</span>) },
    {
      title: '启用', dataIndex: 'enabled', width: 80,
      render: (_: unknown, r: InhibitionRule) => <Switch checked={r.enabled} onChange={(c) => toggle(r, c)} />,
    },
    { title: '备注', dataIndex: 'comment', render: (v: string) => v || '-' },
    {
      title: '操作', width: 120, render: (_: unknown, r: InhibitionRule) => (
        <Space size="small">
          <a onClick={() => openEdit(r)}>编辑</a>
          <Popconfirm title="删除该抑制规则？" onConfirm={() => remove(r)}>
            <a style={{ color: '#cf1322' }}>删除</a>
          </Popconfirm>
        </Space>
      ),
    },
  ]

  return (
    <Card
      size="small"
      title="抑制规则"
      extra={
        <Space>
          <Button icon={<ReloadOutlined />} onClick={load}>刷新</Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>新建抑制规则</Button>
        </Space>
      }
    >
      <Table rowKey="id" size="small" loading={loading} dataSource={rows} columns={columns} pagination={{ pageSize: 20 }} scroll={{ x: 1000 }} />

      <Modal
        open={open}
        title={editing ? '编辑抑制规则' : '新建抑制规则'}
        onOk={submit}
        onCancel={() => setOpen(false)}
        width={760}
        confirmLoading={saving}
        destroyOnClose
      >
        <Form form={form} layout="vertical">
          <Form.Item name="name" label="名称（可选）">
            <Input placeholder="如：节点故障抑制其上的 Pod 告警" />
          </Form.Item>

          <Form.Item label="源匹配条件（命中即触发抑制）" required>
            <Form.Item name="source_matchers" noStyle
              rules={[{ validator: (_r, v: SilenceMatcher[] | undefined) => (collectMatchers(v).length > 0 ? Promise.resolve() : Promise.reject(new Error('请至少填写一条源匹配条件'))) }]}>
              <MatcherEditor />
            </Form.Item>
          </Form.Item>

          <Form.Item label="目标匹配条件（被抑制的告警）" required>
            <Form.Item name="target_matchers" noStyle
              rules={[{ validator: (_r, v: SilenceMatcher[] | undefined) => (collectMatchers(v).length > 0 ? Promise.resolve() : Promise.reject(new Error('请至少填写一条目标匹配条件'))) }]}>
              <MatcherEditor />
            </Form.Item>
          </Form.Item>

          <Form.Item name="equal_labels" label="等价标签（两侧取值需相同才抑制）">
            <Select mode="tags" placeholder="如 cluster_id、namespace" open={false} suffixIcon={null} tokenSeparators={[',']} />
          </Form.Item>

          <Form.Item name="enabled" label="启用" valuePropName="checked" initialValue={true}>
            <Switch />
          </Form.Item>

          <Form.Item name="comment" label="备注">
            <Input.TextArea rows={2} />
          </Form.Item>
        </Form>
      </Modal>
    </Card>
  )
}

// ============ 页面入口 ============
export default function AlertSilences() {
  return (
    <div style={{ padding: 16 }}>
      <Tabs
        defaultActiveKey="silences"
        destroyInactiveTabPane
        items={[
          { key: 'silences', label: '屏蔽规则', children: <SilenceRules /> },
          { key: 'inhibitions', label: '抑制规则', children: <InhibitionRules /> },
        ]}
      />
    </div>
  )
}
