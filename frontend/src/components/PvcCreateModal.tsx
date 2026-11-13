// 创建存储 PVC
import { useEffect, useState } from 'react'
import { Modal, Form, Input, Select, Alert, message } from 'antd'
import { applyObject, getMiscResources, getClusterNamespaces } from '../services/api'
import type { ConfigObjectTarget } from './ConfigObjectYamlModal'

export default function PvcCreateModal({ target, onClose, onSaved }: {
  target: ConfigObjectTarget | null
  onClose: () => void
  onSaved?: () => void
}) {
  const [form] = Form.useForm()
  const [loading, setLoading] = useState(false)
  const [scs, setScs] = useState<Array<{ name: string; is_default?: boolean }>>([])
  const [namespaces, setNamespaces] = useState<string[]>([])
  const [nsLoading, setNsLoading] = useState(false)

  useEffect(() => {
    if (!target) return
    form.resetFields()
    form.setFieldsValue({ name: '', namespace: target.namespace || 'default', accessMode: 'ReadWriteOnce', capacity: '10Gi' })
    getMiscResources(target.clusterId, '')
      .then((d) => setScs(((d as { storage_classes?: Array<{ name: string; is_default?: boolean }> }).storage_classes) || []))
      .catch(() => setScs([]))
  }, [target, form])

  useEffect(() => {
    if (!target) return
    const current = target.namespace || 'default'
    setNsLoading(true)
    getClusterNamespaces(target.clusterId)
      .then((d) => setNamespaces(Array.from(new Set([...(d || []), current]))))
      .catch(() => { setNamespaces([current]); message.error('命名空间列表获取失败') })
      .finally(() => setNsLoading(false))
  }, [target])

  const submit = async () => {
    if (!target) return
    const v = await form.validateFields()
    setLoading(true)
    try {
      await applyObject(target.clusterId, 'pvc', {
        apiVersion: 'v1', kind: 'PersistentVolumeClaim',
        metadata: { name: (v.name || '').trim(), namespace: (v.namespace || '').trim() || 'default' },
        spec: {
          accessModes: [v.accessMode],
          ...(v.storageClass ? { storageClassName: v.storageClass } : {}),
          resources: { requests: { storage: v.capacity } },
        },
      })
      message.success('已创建')
      onSaved?.()
      onClose()
    } catch {
      message.error('创建失败')
    } finally {
      setLoading(false)
    }
  }

  return (
    <Modal title="创建存储（PVC）" open={!!target} onCancel={onClose} onOk={submit} okText="创建" confirmLoading={loading} width={560}>
      <Form form={form} layout="vertical">
        <Form.Item name="name" label="名称" rules={[{ required: true, message: '名称必填' }]}><Input /></Form.Item>
        <Form.Item name="namespace" label="命名空间" rules={[{ required: true }]}>
          <Select showSearch optionFilterProp="label" loading={nsLoading} placeholder="选择命名空间"
            options={namespaces.map((n) => ({ value: n, label: n }))} style={{ width: 320 }} />
        </Form.Item>
        <Form.Item name="storageClass" label="存储类型 StorageClass">
          <Select allowClear placeholder="（不选=默认 StorageClass）" style={{ width: 320 }}
            options={scs.map((s) => ({ value: s.name, label: s.is_default ? `${s.name}（默认）` : s.name }))} />
        </Form.Item>
        <Form.Item name="accessMode" label="访问模式" rules={[{ required: true }]}>
          <Select style={{ width: 320 }} options={[
            { value: 'ReadWriteOnce', label: 'ReadWriteOnce (RWO)' },
            { value: 'ReadOnlyMany', label: 'ReadOnlyMany (ROX)' },
            { value: 'ReadWriteMany', label: 'ReadWriteMany (RWX)' },
          ]} />
        </Form.Item>
        <Form.Item name="capacity" label="容量" rules={[{ required: true, message: '如 10Gi' }]}>
          <Input placeholder="如 10Gi" style={{ width: 200 }} />
        </Form.Item>
        <Alert type="info" showIcon message="PVC 创建后容量只能扩容、不能缩小；删除 PVC 会释放存储（数据可能丢失）。" />
      </Form>
    </Modal>
  )
}
