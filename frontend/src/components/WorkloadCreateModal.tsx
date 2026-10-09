// 创建工作负载（无状态/有状态/守护集/定时任务）：KubeSphere 风格结构化表单 → 拼 YAML 创建
import { useEffect, useState } from 'react'
import { Modal, Form, Input, InputNumber, Switch, Collapse, Divider, message } from 'antd'
import { applyWorkload } from '../services/api'
import { KeyValueList, type KvItem } from './KeyValueEditor'
import { buildYaml } from './workloadYaml'

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

const HAS_REPLICAS = (kind: string) => kind === 'deployment' || kind === 'statefulset'

export default function WorkloadCreateModal({ target, onClose, onSaved }: {
  target: WorkloadCreateTarget | null
  onClose: () => void
  onSaved?: () => void
}) {
  const [form] = Form.useForm()
  const [loading, setLoading] = useState(false)
  const kind = target?.kind || ''
  const livenessEnabled = Form.useWatch('livenessEnabled', form)
  const readinessEnabled = Form.useWatch('readinessEnabled', form)

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
      env: [] as KvItem[],
      labels: [] as KvItem[],
      livenessEnabled: false,
      livenessPath: '/healthz',
      readinessEnabled: false,
      readinessPath: '/healthz',
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

  const isCron = kind === 'cronjob'

  return (
    <Modal
      title={`创建工作负载：${KIND_LABEL[kind] || kind}`}
      open={!!target}
      onCancel={onClose}
      onOk={submit}
      okText="创建"
      confirmLoading={loading}
      width={640}
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

        {!isCron && (
          <Collapse
            ghost
            size="small"
            items={[
              {
                key: 'res',
                label: '资源配额',
                forceRender: true,
                children: (
                  <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
                    <Form.Item name="cpuRequest" label="CPU 请求" style={{ flex: 1, minWidth: 120 }}><Input placeholder="如 100m" /></Form.Item>
                    <Form.Item name="cpuLimit" label="CPU 上限" style={{ flex: 1, minWidth: 120 }}><Input placeholder="如 500m" /></Form.Item>
                    <Form.Item name="memoryRequest" label="内存请求" style={{ flex: 1, minWidth: 120 }}><Input placeholder="如 256Mi" /></Form.Item>
                    <Form.Item name="memoryLimit" label="内存上限" style={{ flex: 1, minWidth: 120 }}><Input placeholder="如 512Mi" /></Form.Item>
                  </div>
                ),
              },
              {
                key: 'env',
                label: '环境变量',
                forceRender: true,
                children: <KeyValueList name="env" addLabel="添加环境变量" keyPlaceholder="变量名" valuePlaceholder="变量值" />,
              },
              {
                key: 'labels',
                label: '标签',
                forceRender: true,
                children: (
                  <>
                    <KeyValueList name="labels" addLabel="添加标签" keyPlaceholder="标签 key" valuePlaceholder="标签 value" />
                    <Divider style={{ margin: '8px 0' }} />
                  </>
                ),
              },
              {
                key: 'probe',
                label: '健康检查',
                forceRender: true,
                children: (
                  <div>
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
                  </div>
                ),
              },
            ]}
          />
        )}
      </Form>
    </Modal>
  )
}
