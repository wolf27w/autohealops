// 创建工作负载（无状态/有状态/守护集/定时任务）：预置 YAML 模板 → server-side apply 创建
import { useEffect, useState } from 'react'
import { Modal, Input, Alert, message } from 'antd'
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

function template(kind: string, namespace: string, name: string): string {
  const ns = namespace || 'default'
  const nm = name || 'example'
  const podSpec = `      containers:
        - name: ${nm}
          image: nginx:latest
          ports:
            - containerPort: 80`
  if (kind === 'statefulset') {
    return `apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: ${nm}
  namespace: ${ns}
spec:
  serviceName: ${nm}
  replicas: 1
  selector:
    matchLabels:
      app: ${nm}
  template:
    metadata:
      labels:
        app: ${nm}
    spec:
${podSpec}
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
${podSpec}
`
  }
  if (kind === 'cronjob') {
    return `apiVersion: batch/v1
kind: CronJob
metadata:
  name: ${nm}
  namespace: ${ns}
spec:
  schedule: "*/5 * * * *"
  jobTemplate:
    spec:
      template:
        spec:
          restartPolicy: OnFailure
          containers:
            - name: ${nm}
              image: busybox:latest
              command: ["/bin/sh", "-c", "echo hello"]
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
  replicas: 1
  selector:
    matchLabels:
      app: ${nm}
  template:
    metadata:
      labels:
        app: ${nm}
    spec:
${podSpec}
`
}

export default function WorkloadCreateModal({ target, onClose, onSaved }: {
  target: WorkloadCreateTarget | null
  onClose: () => void
  onSaved?: () => void
}) {
  const [yaml, setYaml] = useState('')
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!target) return
    setYaml(template(target.kind, target.namespace, ''))
  }, [target])

  const submit = async () => {
    if (!target) return
    if (!yaml.trim()) { message.warning('YAML 不能为空'); return }
    setLoading(true)
    try {
      await applyWorkload(target.clusterId, { kind: target.kind, namespace: target.namespace, name: '', yaml })
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
      title={`创建工作负载：${KIND_LABEL[target?.kind || ''] || target?.kind || ''}`}
      open={!!target}
      onCancel={onClose}
      onOk={submit}
      okText="创建"
      confirmLoading={loading}
      width={920}
    >
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 8 }}
        message="编辑下方 YAML 后点击「创建」。metadata.name 不能为空；命名空间默认取当前选择。"
      />
      <Input.TextArea
        value={yaml}
        onChange={(e) => setYaml(e.target.value)}
        rows={24}
        style={{ fontFamily: 'monospace', fontSize: 12 }}
      />
    </Modal>
  )
}
