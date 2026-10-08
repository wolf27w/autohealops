// 编辑设置（数据键值；Secret 为明文）
import { useEffect, useState } from 'react'
import { Modal, Form, Input, Alert, message } from 'antd'
import { getObjectDetail, updateObjectData } from '../services/api'
import { KeyValueList, toKvList, fromKvList } from './KeyValueEditor'
import type { ConfigObjectTarget } from './ConfigObjectYamlModal'

export default function ConfigObjectDataModal({ target, onClose, onSaved }: {
  target: ConfigObjectTarget | null
  onClose: () => void
  onSaved?: () => void
}) {
  const [form] = Form.useForm()
  const [loading, setLoading] = useState(false)
  const [type, setType] = useState<string>('')

  useEffect(() => {
    if (!target) return
    setLoading(true)
    getObjectDetail(target.clusterId, target.kind, target.namespace, target.name)
      .then((d) => {
        const t = d as { data?: Record<string, string>; type?: string }
        form.setFieldsValue({ data: toKvList(t.data) })
        setType(t.type || '')
      })
      .catch(() => message.error('加载失败'))
      .finally(() => setLoading(false))
  }, [target, form])

  const submit = async () => {
    if (!target) return
    const v = await form.validateFields()
    setLoading(true)
    try {
      await updateObjectData(target.clusterId, target.kind, target.namespace, target.name,
        fromKvList(v.data), target.kind === 'secret' ? type : undefined)
      message.success('已保存')
      onSaved?.()
      onClose()
    } catch {
      message.error('保存失败')
    } finally {
      setLoading(false)
    }
  }

  const isSecret = target?.kind === 'secret'
  const label = isSecret ? '保密字典' : '配置字典'
  return (
    <Modal title={`编辑设置：${target?.name || ''}（${label}）`} open={!!target} onCancel={onClose}
      onOk={submit} okText="保存" confirmLoading={loading} width={760}>
      {isSecret && (
        <Alert type="warning" showIcon style={{ marginBottom: 8 }}
          message={`类型的值将以明文提交（k8s 自动 base64）；当前类型：${type || 'Opaque'}`} />
      )}
      {isSecret && (
        <Form form={form} layout="vertical">
          <Form.Item label="类型 Type">
            <Input value={type} onChange={(e) => setType(e.target.value)} placeholder="Opaque / kubernetes.io/dockerconfigjson ..." />
          </Form.Item>
          <Form.Item label="数据（明文，key → value）" style={{ marginBottom: 0 }}>
            <KeyValueList name="data" addLabel="添加数据项" />
          </Form.Item>
        </Form>
      )}
      {!isSecret && (
        <Form form={form} layout="vertical">
          <Form.Item label="数据（key → value）" style={{ marginBottom: 0 }}>
            <KeyValueList name="data" addLabel="添加数据项" />
          </Form.Item>
        </Form>
      )}
    </Modal>
  )
}
