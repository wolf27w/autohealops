// 值班排班：排班规则 + 值班日历 + 换班/代班与节假日
import { useEffect, useMemo, useState } from 'react'
import {
  Button, Calendar, Card, DatePicker, Empty, Form, Input, Modal, Popconfirm, Select, Space, Spin, Switch, Table, Tabs, Tag, Typography, message,
} from 'antd'
import { PlusOutlined, SwapOutlined } from '@ant-design/icons'
import dayjs, { type Dayjs } from 'dayjs'
import { canWrite } from '../utils/auth'
import {
  getOnDuty, createOnDuty, updateOnDuty, deleteOnDuty,
  getHolidays, createHoliday, deleteHoliday,
  getDutyCalendar, getDutyOverrides, createDutyOverride, deleteDutyOverride,
  getUsers,
} from '../services/api'
import type { DutyCalendarData, DutyCalendarDay, DutyOverrideItem, Holiday, OnDutySchedule, User } from '../types'

// ============ 常量（与后端口径一致） ============
const DAY_TYPE_LABEL: Record<string, string> = { weekday: '工作日', weekend: '周末', holiday: '节假日' }
const DAY_TYPE_COLOR: Record<string, string> = { weekday: 'blue', weekend: 'purple', holiday: 'red' }
const SHIFT_LABEL: Record<string, string> = { day: '白班', night: '夜班' }
const OVERRIDE_TYPE_LABEL: Record<string, string> = { swap: '换班', substitute: '代班' }

const fmt = (d: Dayjs) => d.format('YYYY-MM-DD')

// ============ 排班规则 ============
function ScheduleTab({ users, writable }: { users: User[]; writable: boolean }) {
  const [data, setData] = useState<OnDutySchedule[]>([])
  const [loading, setLoading] = useState(false)
  const [open, setOpen] = useState(false)
  const [editing, setEditing] = useState<OnDutySchedule | null>(null)
  const [saving, setSaving] = useState(false)
  const [form] = Form.useForm()

  const load = () => {
    setLoading(true)
    getOnDuty()
      .then((d) => setData((d as OnDutySchedule[]) ?? []))
      .catch(() => {})
      .finally(() => setLoading(false))
  }
  useEffect(() => { load() }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const userOptions = useMemo(() => users.map((u) => ({ value: u.username, label: u.username })), [users])

  const openCreate = () => {
    setEditing(null)
    form.resetFields()
    form.setFieldsValue({ day_type: 'weekday', shift_type: 'day', start_time: '09:00', end_time: '18:00', enabled: true })
    setOpen(true)
  }

  const openEdit = (r: OnDutySchedule) => {
    setEditing(r)
    form.setFieldsValue({ ...r, user_ids: (r.user_ids || '').split(',').filter(Boolean) })
    setOpen(true)
  }

  const save = async () => {
    const v = await form.validateFields()
    setSaving(true)
    try {
      const payload = { ...v, user_ids: Array.isArray(v.user_ids) ? v.user_ids.join(',') : (v.user_ids || '') }
      if (editing) await updateOnDuty(editing.id, payload)
      else await createOnDuty(payload)
      message.success('已保存')
      setOpen(false)
      load()
    } catch { /* 拦截器已提示 */ } finally { setSaving(false) }
  }

  const remove = async (id: number) => {
    try { await deleteOnDuty(id); message.success('已删除'); load() } catch { /* 拦截器已提示 */ }
  }

  return (
    <Card>
      <Typography.Paragraph type="secondary" style={{ marginBottom: 12 }}>
        值班排班规则：按「日期类型 + 班次」组合匹配，系统据此自动算出每天的值班人。同一日类型可配置多班次。
      </Typography.Paragraph>
      {writable && (
        <Button type="primary" icon={<PlusOutlined />} style={{ marginBottom: 16 }} onClick={openCreate}>新增排班</Button>
      )}
      <Table
        rowKey="id" loading={loading} dataSource={data}
        pagination={false}
        locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无排班规则" /> }}
        columns={[
          { title: '名称', dataIndex: 'name', ellipsis: true },
          { title: '日期类型', dataIndex: 'day_type', width: 100, render: (v: string) => <Tag color={DAY_TYPE_COLOR[v]}>{DAY_TYPE_LABEL[v] ?? v}</Tag> },
          { title: '班次', dataIndex: 'shift_type', width: 80, render: (v: string) => SHIFT_LABEL[v] ?? v },
          { title: '时间段', width: 140, render: (_, r) => `${r.start_time} - ${r.end_time}` },
          {
            title: '值班人', dataIndex: 'user_ids',
            render: (ids: string) => (ids ? ids.split(',').filter(Boolean).map((u) => <Tag key={u} color="blue" style={{ marginBottom: 2 }}>{u}</Tag>) : '-'),
          },
          {
            title: '启用', dataIndex: 'enabled', width: 70,
            render: (v: boolean) => (v ? <Tag color="green">启用</Tag> : <Tag>停用</Tag>),
          },
          ...(writable ? [{
            title: '操作', width: 120,
            render: (_: unknown, r: OnDutySchedule) => (
              <Space>
                <Button size="small" onClick={() => openEdit(r)}>编辑</Button>
                <Popconfirm title="确认删除该排班规则？" onConfirm={() => remove(r.id)}>
                  <Button size="small" danger>删除</Button>
                </Popconfirm>
              </Space>
            ),
          }] : []),
        ]}
      />
      <Modal
        title={editing ? '编辑排班规则' : '新增排班规则'}
        open={open} onOk={save} confirmLoading={saving} onCancel={() => setOpen(false)} destroyOnHidden
      >
        <Form form={form} layout="vertical" initialValues={{ day_type: 'weekday', shift_type: 'day', start_time: '09:00', end_time: '18:00', enabled: true }}>
          <Form.Item name="name" label="名称" rules={[{ required: true, message: '请输入名称' }]}>
            <Input placeholder="如：工作日白班" maxLength={100} />
          </Form.Item>
          <Form.Item name="day_type" label="日期类型">
            <Select options={Object.entries(DAY_TYPE_LABEL).map(([k, v]) => ({ value: k, label: v }))} />
          </Form.Item>
          <Form.Item name="shift_type" label="班次">
            <Select options={Object.entries(SHIFT_LABEL).map(([k, v]) => ({ value: k, label: v }))} />
          </Form.Item>
          <Form.Item name="start_time" label="开始时间" rules={[{ required: true, message: '请输入开始时间' }]}>
            <Input placeholder="09:00" />
          </Form.Item>
          <Form.Item name="end_time" label="结束时间" rules={[{ required: true, message: '请输入结束时间' }]}>
            <Input placeholder="18:00（夜班可跨天，如 09:00）" />
          </Form.Item>
          <Form.Item name="user_ids" label="值班人" rules={[{ required: true, message: '请选择值班人' }]}>
            <Select mode="multiple" allowClear placeholder="选择值班人" optionFilterProp="label" options={userOptions} />
          </Form.Item>
          <Form.Item name="enabled" label="启用" valuePropName="checked"><Switch /></Form.Item>
        </Form>
      </Modal>
    </Card>
  )
}

// ============ 值班日历 ============
function CalendarTab({ writable, onNewOverride }: { writable: boolean; onNewOverride: (date: string) => void }) {
  const [month, setMonth] = useState<Dayjs>(dayjs())
  const [days, setDays] = useState<DutyCalendarDay[]>([])
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    setLoading(true)
    const start = month.startOf('month').format('YYYY-MM-DD')
    const end = month.endOf('month').format('YYYY-MM-DD')
    getDutyCalendar({ start, end })
      .then((d) => setDays((d as DutyCalendarData)?.days ?? []))
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [month])

  const dayMap: Record<string, DutyCalendarDay> = {}
  days.forEach((d) => { dayMap[d.date] = d })

  return (
    <Card>
      <Typography.Paragraph type="secondary" style={{ marginBottom: 12 }}>
        值班日历：按「排班规则 + 节假日」自动算出每天值班人；橙色标签表示当日有换班/代班覆盖。
        {writable && '如需变动，可点击日期直接登记换班/代班。'}
      </Typography.Paragraph>
      <Spin spinning={loading}>
        <Calendar
          value={month}
          onPanelChange={(m) => setMonth(m)}
          onSelect={(dt) => { if (writable) onNewOverride(fmt(dt)) }}
          cellRender={(current, info) => {
            if (info.type !== 'date') return info.originNode
            const d = dayMap[fmt(current)]
            if (!d) return null
            const hasOverride = (d.overrides ?? []).length > 0
            return (
              <div style={{ fontSize: 12, lineHeight: '18px', minHeight: 24 }}>
                {d.holiday ? <Tag color="red" style={{ marginInlineEnd: 2 }}>{d.holiday}</Tag> : null}
                {hasOverride ? <Tag color="orange" style={{ marginInlineEnd: 2 }}>换</Tag> : null}
                {d.users.length === 0
                  ? <span style={{ color: '#bbb' }}>无值班</span>
                  : d.users.map((u) => (
                    <Tag key={u} color={(d.overrides ?? []).some((o) => o.substitute === u) ? 'orange' : 'blue'} style={{ marginBottom: 2 }}>{u}</Tag>
                  ))}
              </div>
            )
          }}
        />
      </Spin>
    </Card>
  )
}

// ============ 换班/代班 + 节假日 ============
function OverridesTab({ users, writable, prefillDate, onPrefillConsumed }: { users: User[]; writable: boolean; prefillDate?: string; onPrefillConsumed?: () => void }) {
  const [overrides, setOverrides] = useState<DutyOverrideItem[]>([])
  const [holidays, setHolidays] = useState<Holiday[]>([])
  const [loading, setLoading] = useState(false)
  const [holidayLoading, setHolidayLoading] = useState(false)

  const [overrideOpen, setOverrideOpen] = useState(false)
  const [savingOverride, setSavingOverride] = useState(false)
  const [overrideForm] = Form.useForm()

  const [holidayOpen, setHolidayOpen] = useState(false)
  const [savingHoliday, setSavingHoliday] = useState(false)
  const [holidayForm] = Form.useForm()

  const userOptions = useMemo(() => users.map((u) => ({ value: u.username, label: u.username })), [users])

  const loadOverrides = () => {
    setLoading(true)
    getDutyOverrides()
      .then((d) => setOverrides((d as unknown as DutyOverrideItem[]) ?? []))
      .catch(() => {})
      .finally(() => setLoading(false))
  }
  const loadHolidays = () => {
    setHolidayLoading(true)
    getHolidays()
      .then((d) => setHolidays((d as Holiday[]) ?? []))
      .catch(() => {})
      .finally(() => setHolidayLoading(false))
  }
  useEffect(() => { loadOverrides(); loadHolidays() }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // 供日历页签点击日期后跳过来并预填日期
  const openOverride = (date?: string) => {
    overrideForm.resetFields()
    overrideForm.setFieldsValue({ type: 'swap', date: date ? dayjs(date) : undefined })
    setOverrideOpen(true)
  }

  // 从日历点击日期跳转过来时，预填并打开登记弹窗（消费后由父级清空，避免重复触发）
  useEffect(() => {
    if (prefillDate) {
      openOverride(prefillDate)
      onPrefillConsumed?.()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [prefillDate])

  const saveOverride = async () => {
    const v = await overrideForm.validateFields()
    setSavingOverride(true)
    try {
      await createDutyOverride({ ...v, date: v.date ? fmt(v.date as Dayjs) : '' })
      message.success('已登记换班/代班')
      setOverrideOpen(false)
      loadOverrides()
    } catch { /* 拦截器已提示 */ } finally { setSavingOverride(false) }
  }

  const removeOverride = async (id: number) => {
    try { await deleteDutyOverride(id); message.success('已删除'); loadOverrides() } catch { /* 拦截器已提示 */ }
  }

  const saveHoliday = async () => {
    const v = await holidayForm.validateFields()
    setSavingHoliday(true)
    try {
      await createHoliday({ date: fmt(v.date as Dayjs), name: v.name })
      message.success('已添加节假日')
      setHolidayOpen(false)
      loadHolidays()
    } catch { /* 拦截器已提示 */ } finally { setSavingHoliday(false) }
  }

  const removeHoliday = async (id: number) => {
    try { await deleteHoliday(id); message.success('已删除'); loadHolidays() } catch { /* 拦截器已提示 */ }
  }

  return (
    <Space direction="vertical" style={{ width: '100%' }} size={16}>
      <Card
        title="换班 / 代班"
        extra={writable ? <Button type="primary" icon={<SwapOutlined />} onClick={() => openOverride()}>新增换班/代班</Button> : null}
      >
        <Table
          rowKey="id" loading={loading} dataSource={overrides} size="small"
          pagination={false}
          locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无换班/代班记录" /> }}
          columns={[
            { title: '日期', dataIndex: 'date', width: 120 },
            { title: '类型', dataIndex: 'type', width: 80, render: (v: string) => <Tag color={v === 'swap' ? 'orange' : 'cyan'}>{OVERRIDE_TYPE_LABEL[v] ?? v}</Tag> },
            { title: '原值班人', dataIndex: 'original_username', width: 120, render: (v: string) => v || <span style={{ color: '#bbb' }}>整班代</span> },
            { title: '替班人', dataIndex: 'substitute_username', width: 120, render: (v: string) => <Tag color="orange">{v}</Tag> },
            { title: '备注', dataIndex: 'note', ellipsis: true, render: (v: string) => v || '-' },
            { title: '登记人', dataIndex: 'created_by', width: 110, render: (v: string) => v || '-' },
            ...(writable ? [{
              title: '操作', width: 70,
              render: (_: unknown, r: DutyOverrideItem) => (
                <Popconfirm title="确认删除该记录？" onConfirm={() => removeOverride(r.id)}>
                  <Button size="small" danger>删除</Button>
                </Popconfirm>
              ),
            }] : []),
          ]}
        />
      </Card>

      <Card
        title="节假日"
        extra={writable ? <Button type="primary" icon={<PlusOutlined />} onClick={() => { holidayForm.resetFields(); setHolidayOpen(true) }}>新增节假日</Button> : null}
      >
        <Table
          rowKey="id" loading={holidayLoading} dataSource={holidays} size="small"
          pagination={false}
          locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无节假日" /> }}
          columns={[
            { title: '日期', dataIndex: 'date', width: 160 },
            { title: '名称', dataIndex: 'name' },
            ...(writable ? [{
              title: '操作', width: 70,
              render: (_: unknown, r: Holiday) => (
                <Popconfirm title="确认删除该节假日？" onConfirm={() => removeHoliday(r.id)}>
                  <Button size="small" danger>删除</Button>
                </Popconfirm>
              ),
            }] : []),
          ]}
        />
      </Card>

      {/* 新增换班/代班 */}
      <Modal
        title="换班 / 代班" open={overrideOpen} onOk={saveOverride} confirmLoading={savingOverride}
        onCancel={() => setOverrideOpen(false)} destroyOnHidden
      >
        <Form form={overrideForm} layout="vertical" initialValues={{ type: 'swap' }}>
          <Form.Item name="date" label="日期" rules={[{ required: true, message: '请选择日期' }]}>
            <DatePicker style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item name="type" label="类型">
            <Select options={[
              { value: 'swap', label: '换班（替换指定值班人）' },
              { value: 'substitute', label: '代班（整班换人）' },
            ]} />
          </Form.Item>
          <Form.Item name="original_username" label="原值班人（换班时填；代班留空）">
            <Select allowClear showSearch optionFilterProp="label" placeholder="如 zhangsan" options={userOptions} />
          </Form.Item>
          <Form.Item name="substitute_username" label="替班人" rules={[{ required: true, message: '请选择替班人' }]}>
            <Select showSearch optionFilterProp="label" placeholder="选择替班人" options={userOptions} />
          </Form.Item>
          <Form.Item name="note" label="备注">
            <Input placeholder="如：家里有事，调休" maxLength={255} />
          </Form.Item>
        </Form>
      </Modal>

      {/* 新增节假日 */}
      <Modal
        title="新增节假日" open={holidayOpen} onOk={saveHoliday} confirmLoading={savingHoliday}
        onCancel={() => setHolidayOpen(false)} destroyOnHidden
      >
        <Form form={holidayForm} layout="vertical">
          <Form.Item name="date" label="日期" rules={[{ required: true, message: '请选择日期' }]}>
            <DatePicker style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item name="name" label="名称" rules={[{ required: true, message: '请输入名称' }]}>
            <Input placeholder="如：国庆节" maxLength={100} />
          </Form.Item>
        </Form>
      </Modal>
    </Space>
  )
}

// ============ 页面入口 ============
export default function OnDuty() {
  const writable = canWrite()
  const [users, setUsers] = useState<User[]>([])
  const [activeTab, setActiveTab] = useState('schedule')
  const [overrideDate, setOverrideDate] = useState<string | undefined>()

  useEffect(() => {
    getUsers().then((d) => setUsers(d ?? [])).catch(() => {})
  }, [])

  // 从日历点击日期 → 切到换班页签并预填日期
  const handleNewOverride = (date: string) => {
    setOverrideDate(date)
    setActiveTab('overrides')
  }

  return (
    <Tabs
      activeKey={activeTab}
      onChange={setActiveTab}
      items={[
        { key: 'schedule', label: '排班规则', children: <ScheduleTab users={users} writable={writable} /> },
        { key: 'calendar', label: '值班日历', children: <CalendarTab writable={writable} onNewOverride={handleNewOverride} /> },
        { key: 'overrides', label: '换班/代班 + 节假日', children: <OverridesTab users={users} writable={writable} prefillDate={overrideDate} onPrefillConsumed={() => setOverrideDate(undefined)} /> },
      ]}
    />
  )
}
