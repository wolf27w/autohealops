// 创建工作负载（无状态/有状态/守护集/定时任务）：KubeSphere 风格结构化表单 → 拼 YAML 创建
import { useEffect, useState } from 'react'
import { Modal, Form, Input, InputNumber, Select, Alert, message } from 'antd'
import { applyWorkload } from '../services/api'

export interface WorkloadCreateTarget {
  clusterId: number
  kind: string        // deployment/statefulset/daemonset/cronjob
  namespace: string
}

const KIND_LABEL: Record<string, string> = {
  deployment: '无状态（Deployment）',
  statefulset: '有状态（StatefulSet）',
  daemonset: '守护集（DaemonSet）',
  cronjob: '定时任务（CronJob）',
}

// 各 kind 是否有「副本数」字段（Deployment/StatefulSet 有；DaemonSet/CronJob 无）
const HAS_REPLICAS = (kind: string) => kind === 'deployment' || kind === 'statefulset'

function buildYaml(kind: string, v: {
  name: string; namespace: string; replicas?: number; image: string; containerPort?: number;
  schedule?: string; command?: string;
}): string {
  const ns = v.namespace || 'default'
  const nm = v.name
  const ports = v.containerPort ? `          ports:\n            - containerPort: ${v.containerPort}\n` : ''
  const containerLines = `        - name: ${nm}\n          image: ${v.image}${ports}`

  if (kind === 'statefulset') {
    return `apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: ${nm}
  namespace: ${ns}
spec:
  serviceName: ${nm}
  replicas: ${v.replicas ?? 1}
  selector:
    matchLabels:
      app: ${nm}
  template:
    metadata:
      labels:
        app: ${nm}
    spec:
      containers:
${containerLines}
`
  }
  if (kind === 'daemonset') {
    return `apiVersion: apps/v1
kind: DaemonSet
metadata:
  name: ${nm}
  namespace: ${ns}
spec:
  selector:
    matchLabels:
      app: ${nm}
  template:
    metadata:
      labels:
        app: ${nm}
    spec:
      containers:
${containerLines}
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
              command: ["/bin/sh", "-c", "${(v.command || 'echo hello').replace(/"/g, '\\"')}"]
`
  }
  return `apiVersion: apps/v1
kind: Deployment
metadata:
  name: ${nm}
  namespace: ${ns}
  labels:
    app: ${nm}
spec:
  replicas: ${v.replicas ?? 1}
  selector:
    matchLabels:
      app: ${nm}
  template:
    metadata:
      labels:
        app: ${nm}
    spec:
      containers:
${containerLines}
`
}

export default function WorkloadCreateModal({ target, onClose, onSaved }: {
  target: WorkloadCreateTarget | null
  onClose: () => void
  onSaved?: () => void
}) {
  const [form] = Form.useForm()
  const [loading, setLoading] = useState(false)
  const kind = target?.kind || ''

  useEffect(() => {
    if (!target) return
    form.resetFields()
    form.setFieldsValue({
      name: '',
      namespace: target.namespace || 'default',
      replicas: 1,
      image: kind === 'cronjob' ? 'busybox:latest' : 'nginx:latest',
      schedule: '*/5 * * * *',
      command: 'echo hello',
    })
  }, [target, kind, form])

  const submit = async () => {
    if (!target) return
    const v = await form.validateFields()
    setLoading(true)
    try {
      await applyWorkload(target.clusterId, {
        kind: target.kind,
        namespace: (v.namespace || '').trim(),
        name: '',
        yaml: buildYaml(target.kind, v),
      })
      message.success('已创建')
      onSaved?.()
      onClose()
    } catch (e) {
      const msg = (e as { response?: { data?: { message?: string } } })?.response?.data?.message
      message.error(msg || '创建失败')
    } finally {
      setLoading(false)
    }
  }

  return (
    <Modal
      title={`创建工作负载：${KIND_LABEL[kind] || kind}`}
      open={!!target}
      onCancel={onClose}
      onOk={submit}
      okText="创建"
      confirmLoading={loading}
      width={560}
    >
      <Form form={form} layout="vertical">
        <Form.Item name="name" label="名称" rules={[{ required: true, message: '名称必填' }]}>
          <Input placeholder="如 nginx-demo" />
        </Form.Item>
        <Form.Item name="namespace" label="命名空间" rules={[{ required: true, message: '命名空间必填' }]}>
          <Input />
        </Form.Item>
        {HAS_REPLICAS(kind) && (
          <Form.Item name="replicas" label="副本数" rules={[{ required: true }]}>
            <InputNumber min={0} style={{ width: 160 }} />
          </Form.Item>
        )}
        {kind === 'cronjob' && (
          <Form.Item name="schedule" label="定时表达式（Cron）" rules={[{ required: true }]} tooltip="分 时 日 月 周；如 */5 * * * * = 每 5 分钟">
            <Input placeholder="*/5 * * * *" />
          </Form.Item>
        )}
        <Form.Item name="image" label="镜像" rules={[{ required: true, message: '镜像必填' }]}>
          <Input placeholder="nginx:latest" />
        </Form.Item>
        <Form.Item name="containerPort" label="容器端口">
          <InputNumber min={1} max={65535} placeholder="80" style={{ width: 160 }} />
        </Form.Item>
        {kind === 'cronjob' && (
          <Form.Item name="command" label="执行命令" tooltip="以 /bin/sh -c 执行">
            <Input placeholder="echo hello" />
          </Form.Item>
        )}
      </Form>
      <Alert type="info" showIcon message="创建后按名称自动打 app 标签作为 selector；副本数为 0 表示创建后不启动任何 Pod。" />
    </Modal>
  )
}
