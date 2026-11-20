// R57 弹性伸缩：HPA 创建/编辑表单弹窗（提交统一走表单，不做 YAML 写回）
import { useEffect, useState } from 'react'
import { Modal, Form, Input, InputNumber, Select, Button, Space, message } from 'antd'
import { MinusCircleOutlined, PlusOutlined } from '@ant-design/icons'
import { applyHpa, getWorkloadYaml, type HpaInfo, type HpaMetric } from '../../services/api'

export interface HpaFormModalProps {
  open: boolean
  clusterId: number
  kind: string
  namespace: string
  workloadName: string
  editing?: HpaInfo | null
  onClose: () => void
  onSaved: () => void
}

const METRIC_TYPE_OPTIONS = [
  { value: 'Resource', label: 'Resource' },
  { value: 'Pods', label: 'Pods' },
  { value: 'Object', label: 'Object' },
  { value: 'External', label: 'External' },
]

const RESOURCE_NAME_OPTIONS = [
  { value: 'cpu', label: 'cpu' },
  { value: 'memory', label: 'memory' },
]

function targetTypeOptions(metricType?: string) {
  if (metricType === 'Resource') {
    return [
      { value: 'Utilization', label: 'Utilization（百分比）' },
      { value: 'AverageValue', label: 'AverageValue（绝对值）' },
    ]
  }
  return [
    { value: 'AverageValue', label: 'AverageValue（平均值）' },
    { value: 'Value', label: 'Value（绝对值）' },
  ]
}

// 从 axios 错误里取可读信息（http 封装在 code!=200 时 reject Error；网络错误 reject axios error）
function errMsg(e: unknown): string {
  const a = e as { response?: { data?: { message?: string; detail?: string } }; message?: string }
  return a?.response?.data?.message || a?.response?.data?.detail || a?.message || String(e)
}

// 简单字符串匹配：YAML 里某个容器的 requests 块内是否声明了 cpu（不写 YAML 解析器）
function hasCpuRequests(yaml: string): boolean {
  if (!yaml) return false
  const block = yaml.match(/requests:\s*\n((?:[ \t].*\n?)*)/)
  return !!block && /^\s*cpu\s*:/m.test(block[1])
}

interface MetricFormValue {
  type?: string
  name?: string
  target_type?: string
  target_value?: number
}

interface FormValues {
  name?: string
  min_replicas?: number
  max_replicas?: number
  metrics?: MetricFormValue[]
  scale_up_stabilization_seconds?: number
  scale_down_stabilization_seconds?: number
}

export default function HpaFormModal(props: HpaFormModalProps) {
  const { open, clusterId, kind, namespace, workloadName, editing, onClose, onSaved } = props
  const [form] = Form.useForm()
  const [submitting, setSubmitting] = useState(false)
  const [advanced, setAdvanced] = useState(false)

  const watchedMetrics = (Form.useWatch('metrics', form) as MetricFormValue[] | undefined) || []

  useEffect(() => {
    if (!open) return
    setAdvanced(false)
    if (editing) {
      form.setFieldsValue({
        name: editing.name,
        min_replicas: editing.min_replicas,
        max_replicas: editing.max_replicas,
        metrics: editing.metrics.map((m: HpaMetric) => ({
          type: m.type,
          name: m.name,
          target_type: m.target_type,
          target_value: m.target_type === 'Utilization' ? m.target_utilization : parseFloat(m.target_value || '0'),
        })),
        scale_up_stabilization_seconds: editing.scale_up_stabilization_seconds,
        scale_down_stabilization_seconds: editing.scale_down_stabilization_seconds,
      })
    } else {
      form.resetFields()
      form.setFieldsValue({ metrics: [] })
    }
  }, [open, editing, form])

  const buildMetrics = (list: MetricFormValue[]): Record<string, unknown>[] =>
    list.map((m) => {
      const base: Record<string, unknown> = { type: m.type, name: m.name, target_type: m.target_type }
      if (m.target_type === 'Utilization') base.target_utilization = m.target_value
      else base.target_value = String(m.target_value ?? '')
      return base
    })

  const doSubmit = async (values: FormValues, metrics: Record<string, unknown>[]) => {
    const hpaName = editing ? editing.name : values.name
    const payload: Record<string, unknown> = {
      namespace,
      name: hpaName,
      target_kind: kind,
      target_name: workloadName,
      min_replicas: values.min_replicas,
      max_replicas: values.max_replicas,
      metrics,
    }
    if (values.scale_up_stabilization_seconds != null) payload.scale_up_stabilization_seconds = values.scale_up_stabilization_seconds
    if (values.scale_down_stabilization_seconds != null) payload.scale_down_stabilization_seconds = values.scale_down_stabilization_seconds

    setSubmitting(true)
    try {
      await applyHpa(clusterId, payload)
      message.success(editing ? 'HPA 已更新' : 'HPA 已创建')
      onSaved()
      onClose()
    } catch (e) {
      message.error(errMsg(e))
    } finally {
      setSubmitting(false)
    }
  }

  const submit = async () => {
    const values = (await form.validateFields()) as FormValues
    const rawMetrics: MetricFormValue[] = values.metrics || []
    if (rawMetrics.length === 0) {
      message.warning('HPA 至少需要 1 个指标，否则永不扩缩')
      return
    }
    const metrics = buildMetrics(rawMetrics)
    const cpuUtilization = metrics.some(
      (m) => m.type === 'Resource' && String(m.name).toLowerCase() === 'cpu' && m.target_type === 'Utilization',
    )
    if (cpuUtilization) {
      try {
        const d = (await getWorkloadYaml(clusterId, kind, namespace, workloadName)) as { yaml?: string }
        if (!hasCpuRequests(d?.yaml || '')) {
          Modal.confirm({
            title: '未检测到 requests.cpu',
            content: '容器未配置 requests.cpu，HPA 的 CPU 利用率将一直是 unknown，HPA 不会扩容。仍要继续？',
            okText: '继续提交',
            okButtonProps: { danger: true },
            onOk: () => doSubmit(values, metrics),
          })
          return
        }
      } catch {
        // 拿不到工作负载 YAML 时不阻塞提交
      }
    }
    await doSubmit(values, metrics)
  }

  return (
    <Modal
      title={editing ? `编辑 HPA：${editing.name}` : '创建 HPA'}
      open={open}
      onCancel={onClose}
      onOk={submit}
      okText={editing ? '保存' : '创建'}
      confirmLoading={submitting}
      width={720}
      destroyOnClose
    >
      <Form form={form} layout="vertical" style={{ marginTop: 12 }}>
        <Form.Item name="name" label="HPA 名称" rules={[{ required: true, message: '请填写 HPA 名称' }]}>
          <Input placeholder="自定义 HPA 名称，例如 myapp-hpa" disabled={!!editing} />
        </Form.Item>

        <Space size={16} align="start">
          <Form.Item
            name="min_replicas"
            label="最小副本数"
            rules={[
              { required: true, message: '请填写最小副本数' },
              { type: 'number', min: 1, message: '最小副本数至少为 1' },
            ]}
          >
            <InputNumber min={1} style={{ width: 160 }} />
          </Form.Item>
          <Form.Item
            name="max_replicas"
            label="最大副本数"
            dependencies={['min_replicas']}
            rules={[
              { required: true, message: '请填写最大副本数' },
              ({ getFieldValue }) => ({
                validator: (_: unknown, v: number) => {
                  const min = getFieldValue('min_replicas') as number | undefined
                  if (v == null || min == null || v >= min) return Promise.resolve()
                  return Promise.reject(new Error('最大副本数不能小于最小副本数'))
                },
              }),
            ]}
          >
            <InputNumber min={1} style={{ width: 160 }} />
          </Form.Item>
        </Space>

        <div style={{ marginBottom: 4, fontWeight: 600 }}>指标（至少 1 条，否则永不扩缩）</div>
        <Form.List name="metrics">
          {(fields, { add, remove }) => (
            <div>
              {fields.map((field) => {
                const mtype = watchedMetrics[field.name]?.type
                return (
                  <Space key={field.key} align="baseline" style={{ display: 'flex', marginBottom: 8, flexWrap: 'wrap' }}>
                    <Form.Item name={[field.name, 'type']} rules={[{ required: true, message: '请选择类型' }]} style={{ marginBottom: 0 }}>
                      <Select
                        style={{ width: 120 }}
                        placeholder="类型"
                        options={METRIC_TYPE_OPTIONS}
                        onChange={() => form.setFieldValue(['metrics', field.name, 'target_type'], undefined)}
                      />
                    </Form.Item>
                    <Form.Item name={[field.name, 'name']} rules={[{ required: true, message: '请填写指标名' }]} style={{ marginBottom: 0 }}>
                      {mtype === 'Resource'
                        ? <Select style={{ width: 120 }} placeholder="指标名" options={RESOURCE_NAME_OPTIONS} />
                        : <Input style={{ width: 150 }} placeholder="指标名" />}
                    </Form.Item>
                    <Form.Item name={[field.name, 'target_type']} rules={[{ required: true, message: '请选择目标类型' }]} style={{ marginBottom: 0 }}>
                      <Select style={{ width: 200 }} placeholder="目标类型" options={targetTypeOptions(mtype)} />
                    </Form.Item>
                    <Form.Item name={[field.name, 'target_value']} rules={[{ required: true, message: '请填写目标值' }]} style={{ marginBottom: 0 }}>
                      <InputNumber style={{ width: 120 }} min={0} placeholder="目标值" />
                    </Form.Item>
                    <MinusCircleOutlined onClick={() => remove(field.name)} style={{ color: '#999' }} />
                  </Space>
                )
              })}
              <Button type="dashed" block icon={<PlusOutlined />} onClick={() => add()}>
                添加指标
              </Button>
            </div>
          )}
        </Form.List>

        <div style={{ marginTop: 12 }}>
          <Button type="link" size="small" style={{ padding: 0 }} onClick={() => setAdvanced(!advanced)}>
            {advanced ? '收起高级选项' : '展开高级选项（伸缩稳定窗口）'}
          </Button>
          {advanced && (
            <Space size={16} wrap style={{ marginTop: 8 }}>
              <Form.Item name="scale_up_stabilization_seconds" label="扩容稳定窗口（秒）" style={{ marginBottom: 0 }}>
                <InputNumber min={0} style={{ width: 160 }} placeholder="默认（不传）" />
              </Form.Item>
              <Form.Item name="scale_down_stabilization_seconds" label="缩容稳定窗口（秒）" style={{ marginBottom: 0 }}>
                <InputNumber min={0} style={{ width: 160 }} placeholder="默认（不传）" />
              </Form.Item>
            </Space>
          )}
        </div>
      </Form>
    </Modal>
  )
}
