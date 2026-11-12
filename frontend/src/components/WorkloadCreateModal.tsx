// 创建工作负载（无状态/有状态/守护集/定时任务）：分步向导（Steps）→ 拼 YAML 创建/更新
// 编辑模式：target.create=false + target.name + target.initialValues 回填现有值
import { useEffect, useState } from 'react'
import { Modal, Form, Input, InputNumber, Switch, Select, Steps, Divider, Button, Space, message, Alert } from 'antd'
import { PlusOutlined } from '@ant-design/icons'
import { applyWorkload } from '../services/api'
import { KeyValueList, type KvItem } from './KeyValueEditor'
import { buildYaml, mergeWorkloadYaml, type WorkloadValues, type VolumeType } from './workloadYaml'

export interface WorkloadCreateTarget {
  clusterId: number
  kind: string        // deployment/statefulset/daemonset/cronjob
  namespace: string
  name?: string       // 编辑模式：已存在的工作负载名
  create?: boolean    // 默认 true（创建）；false = 编辑
  initialValues?: Partial<WorkloadValues>
  existingYaml?: string // 编辑模式：现有完整 YAML（用于字段级 merge，避免丢弃高级字段）
}

const KIND_LABEL: Record<string, string> = {
  deployment: '无状态（Deployment）',
  statefulset: '有状态（StatefulSet）',
  daemonset: '守护集（DaemonSet）',
  cronjob: '定时任务（CronJob）',
}

const HAS_REPLICAS = (kind: string) => kind === 'deployment' || kind === 'statefulset'

const VOLUME_TYPES: { value: VolumeType; label: string }[] = [
  { value: 'pvc', label: '持久卷声明(PVC)' },
  { value: 'configMap', label: '配置字典' },
  { value: 'secret', label: '密钥' },
  { value: 'emptyDir', label: '临时卷' },
]

const IMAGE_PULL_POLICIES = [
  { value: 'IfNotPresent', label: 'IfNotPresent（本地优先）' },
  { value: 'Always', label: 'Always（总是拉取）' },
  { value: 'Never', label: 'Never（仅本地）' },
]

export default function WorkloadCreateModal({ target, onClose, onSaved }: {
  target: WorkloadCreateTarget | null
  onClose: () => void
  onSaved?: () => void
}) {
  const [form] = Form.useForm()
  const [loading, setLoading] = useState(false)
  const [current, setCurrent] = useState(0)
  const kind = target?.kind || ''
  const isCron = kind === 'cronjob'
  const isCreate = target ? target.create !== false : false
  const livenessEnabled = Form.useWatch('livenessEnabled', form)
  const readinessEnabled = Form.useWatch('readinessEnabled', form)

  const steps = [
    { title: '基本信息' },
    { title: '资源与健康检查' },
    { title: '存储挂载' },
    { title: '高级设置' },
  ]

  useEffect(() => {
    if (!target) return
    setCurrent(0)
    form.resetFields()
    const defaults = {
      name: target.name || '',
      namespace: target.namespace || 'default',
      replicas: 1,
      image: kind === 'cronjob' ? 'busybox:latest' : 'nginx:latest',
      imagePullPolicy: 'IfNotPresent',
      schedule: '*/5 * * * *',
      command: 'echo hello',
      env: [] as KvItem[],
      labels: [] as KvItem[],
      volumes: [] as WorkloadValues['volumes'],
      livenessEnabled: false,
      livenessPath: '/healthz',
      readinessEnabled: false,
      readinessPath: '/healthz',
    }
    form.setFieldsValue({ ...defaults, ...(isCreate ? {} : (target.initialValues || {})) })
  }, [target, kind, form, isCreate])

  const stepFields: string[][] = [
    isCron
      ? ['name', 'namespace', 'schedule', 'image', 'command']
      : ['name', 'namespace', 'replicas', 'image', 'containerPort'],
    ['cpuRequest', 'cpuLimit', 'memoryRequest', 'memoryLimit'],
    [],
    ['imagePullPolicy'],
  ]

  const next = async () => {
    try {
      await form.validateFields(stepFields[current] as never[])
      setCurrent((c) => Math.min(c + 1, steps.length - 1))
    } catch { /* 校验未过，停留当前步 */ }
  }

  const submit = async () => {
    if (!target) return
    const v = await form.validateFields()
    // 存储挂载：非 emptyDir 必须有来源
    const vols = (v.volumes || []) as WorkloadValues['volumes']
    for (const m of vols || []) {
      if (m && m.type && m.type !== 'emptyDir' && !m.source) {
        message.error('请填写每个卷的来源名称（ConfigMap/Secret/PVC）')
        return
      }
      if (m && m.type && m.type !== 'emptyDir' && !m.mountPath) {
        message.error('请填写每个卷的挂载路径')
        return
      }
    }
    setLoading(true)
    try {
      await applyWorkload(target.clusterId, {
        kind: target.kind,
        namespace: (v.namespace || '').trim(),
        name: isCreate ? '' : (target.name || ''),
        yaml: isCreate ? buildYaml(target.kind, v) : mergeWorkloadYaml(target.existingYaml || '', v, target.kind),
      })
      message.success(isCreate ? '已创建' : '已保存（正在滚动更新 Pod）')
      onSaved?.()
      onClose()
    } catch (e) {
      const msg = (e as { response?: { data?: { message?: string } } })?.response?.data?.message
      message.error(msg || (isCreate ? '创建失败' : '保存失败'))
    } finally {
      setLoading(false)
    }
  }

  const cronNote = (
    <Alert type="info" showIcon style={{ marginBottom: 8 }}
      message="定时任务（CronJob）不涉及资源配额 / 健康检查 / 存储挂载，此步可直接「下一步」。" />
  )

  const step1 = (
    <>
      <Form.Item name="name" label="名称" rules={[{ required: true, message: '名称必填' }]}>
        <Input placeholder="如 nginx-demo" disabled={!isCreate} />
      </Form.Item>
      <Form.Item name="namespace" label="命名空间" rules={[{ required: true, message: '命名空间必填' }]}>
        <Input disabled={!isCreate} />
      </Form.Item>
      {HAS_REPLICAS(kind) && (
        <Form.Item name="replicas" label="副本数" rules={[{ required: true }]}>
          <InputNumber min={0} style={{ width: 160 }} />
        </Form.Item>
      )}
      {isCron && (
        <Form.Item name="schedule" label="定时表达式（Cron）" rules={[{ required: true }]} tooltip="分 时 日 月 周；如 */5 * * * * = 每 5 分钟">
          <Input placeholder="*/5 * * * *" />
        </Form.Item>
      )}
      <Form.Item name="image" label="镜像" rules={[{ required: true, message: '镜像必填' }]}>
        <Input placeholder="nginx:latest" />
      </Form.Item>
      {!isCron && (
        <Form.Item name="containerPort" label="容器端口">
          <InputNumber min={1} max={65535} placeholder="80" style={{ width: 160 }} />
        </Form.Item>
      )}
      {isCron && (
        <Form.Item name="command" label="执行命令" tooltip="以 /bin/sh -c 执行">
          <Input placeholder="echo hello" />
        </Form.Item>
      )}
    </>
  )

  const step2 = isCron ? cronNote : (
    <>
      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
        <Form.Item name="cpuRequest" label="CPU 请求" style={{ flex: 1, minWidth: 120 }}><Input placeholder="如 100m" /></Form.Item>
        <Form.Item name="cpuLimit" label="CPU 上限" style={{ flex: 1, minWidth: 120 }}><Input placeholder="如 500m" /></Form.Item>
        <Form.Item name="memoryRequest" label="内存请求" style={{ flex: 1, minWidth: 120 }}><Input placeholder="如 256Mi" /></Form.Item>
        <Form.Item name="memoryLimit" label="内存上限" style={{ flex: 1, minWidth: 120 }}><Input placeholder="如 512Mi" /></Form.Item>
      </div>
      <Divider style={{ margin: '8px 0 12px' }} />
      <Form.Item name="livenessEnabled" label="存活探针 livenessProbe" valuePropName="checked"><Switch /></Form.Item>
      {livenessEnabled && (
        <div style={{ display: 'flex', gap: 12 }}>
          <Form.Item name="livenessPath" label="路径" style={{ flex: 2 }}><Input placeholder="/healthz" /></Form.Item>
          <Form.Item name="livenessPort" label="端口" style={{ flex: 1 }}><InputNumber min={1} max={65535} placeholder="80" style={{ width: '100%' }} /></Form.Item>
        </div>
      )}
      <Form.Item name="readinessEnabled" label="就绪探针 readinessProbe" valuePropName="checked"><Switch /></Form.Item>
      {readinessEnabled && (
        <div style={{ display: 'flex', gap: 12 }}>
          <Form.Item name="readinessPath" label="路径" style={{ flex: 2 }}><Input placeholder="/healthz" /></Form.Item>
          <Form.Item name="readinessPort" label="端口" style={{ flex: 1 }}><InputNumber min={1} max={65535} placeholder="80" style={{ width: '100%' }} /></Form.Item>
        </div>
      )}
    </>
  )

  const step3 = isCron ? cronNote : (
    <Form.List name="volumes">
      {(fields, { add, remove }) => (
        <>
          {fields.map(({ key, name, ...rest }) => (
            <div key={key} style={{ border: '1px solid #f0f0f0', borderRadius: 6, padding: 12, marginBottom: 12, position: 'relative' }}>
              <Button type="link" danger size="small" style={{ position: 'absolute', top: 2, right: 4 }} onClick={() => remove(name)}>移除</Button>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0 16px' }}>
                <Form.Item {...rest} name={[name, 'type']} label="类型" rules={[{ required: true, message: '请选择类型' }]}>
                  <Select options={VOLUME_TYPES} placeholder="选择卷类型" />
                </Form.Item>
                <Form.Item {...rest} name={[name, 'name']} label="卷名" tooltip="留空自动生成 vol-N（挂载路径与卷按此名关联）">
                  <Input placeholder="如 data（留空自动）" />
                </Form.Item>
                <Form.Item {...rest} name={[name, 'source']} label="来源名称" tooltip="ConfigMap/Secret/PVC 的名称；临时卷（emptyDir）留空">
                  <Input placeholder="如 my-config / my-pvc" />
                </Form.Item>
                <Form.Item {...rest} name={[name, 'mountPath']} label="挂载路径（容器内）" rules={[{ required: true, message: '请填写容器内路径' }, { pattern: /^\//, message: '必须是绝对路径（以 / 开头）' }]}>
                  <Input placeholder="/data" />
                </Form.Item>
                <Form.Item {...rest} name={[name, 'subPath']} label="文件名 / 子路径（可选）" tooltip="挂载单个文件时填来源里的 key/文件名；留空整体挂载为目录">
                  <Input placeholder="如 application.yml" />
                </Form.Item>
                <Form.Item {...rest} name={[name, 'readOnly']} label="只读" valuePropName="checked">
                  <Switch />
                </Form.Item>
              </div>
            </div>
          ))}
          <Button type="dashed" block icon={<PlusOutlined />} onClick={() => add({ type: 'pvc', readOnly: false })}>添加卷</Button>
        </>
      )}
    </Form.List>
  )

  const step4 = (
    <>
      <Form.Item name="imagePullPolicy" label="镜像拉取策略 imagePullPolicy">
        <Select options={IMAGE_PULL_POLICIES} style={{ width: 320 }} />
      </Form.Item>
      <Divider style={{ margin: '8px 0 12px' }} />
      <Form.Item label="环境变量">
        <KeyValueList name="env" addLabel="添加环境变量" keyPlaceholder="变量名" valuePlaceholder="变量值" />
      </Form.Item>
      <Form.Item label="标签">
        <KeyValueList name="labels" addLabel="添加标签" keyPlaceholder="标签 key" valuePlaceholder="标签 value" />
      </Form.Item>
    </>
  )

  const body = (
    <Form form={form} layout="vertical">
      {current === 0 && step1}
      {current === 1 && step2}
      {current === 2 && step3}
      {current === 3 && step4}
    </Form>
  )

  return (
    <Modal
      title={`${isCreate ? '创建' : '编辑设置'}工作负载：${KIND_LABEL[kind] || kind}${isCreate ? '' : `（${target?.namespace}/${target?.name}）`}`}
      open={!!target}
      onCancel={onClose}
      footer={null}
      width={720}
      destroyOnClose
    >
      <Steps current={current} size="small" items={steps} style={{ marginBottom: 20 }} />
      {body}
      <Divider style={{ margin: '8px 0 16px' }} />
      <Space style={{ width: '100%', justifyContent: 'flex-end' }}>
        {current > 0 && <Button onClick={() => setCurrent((c) => c - 1)}>上一步</Button>}
        {current < steps.length - 1 && <Button type="primary" onClick={next}>下一步</Button>}
        {current === steps.length - 1 && (
          <Button type="primary" loading={loading} onClick={submit}>{isCreate ? '创建' : '保存'}</Button>
        )}
      </Space>
    </Modal>
  )
}
