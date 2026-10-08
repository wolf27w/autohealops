// 编辑信息（标签 / 注解）
import { useEffect, useState } from 'react'
import { Modal, Form, message } from 'antd'
import { getObjectDetail, updateObjectMetadata } from '../services/api'
import { KeyValueList, toKvList, fromKvList } from './KeyValueEditor'
import type { ConfigObjectTarget } from './ConfigObjectYamlModal'

export default function ConfigObjectInfoModal({ target, onClose, onSaved }: {
  target: ConfigObjectTarget | null
  onClose: () => void
  onSaved?: () => void
}) {
  const [form] = Form.useForm()
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!target) return
    setLoading(true)
    getObjectDetail(target.clusterId, target.kind, target.namespace, target.name)
      .then((d) => {
        const t = d as { labels?: Record<string, string>; annotations?: Record<string, string> }
        form.setFieldsValue({ labels: toKvList(t.labels), annotations: toKvList(t.annotations) })
      })
      .catch(() => message.error('加载失败'))
      .finally(() => setLoading(false))
  }, [target, form])

  const submit = async () => {
    if (!target) return
    const v = await form.validateFields()
    setLoading(true)
    try {
      await updateObjectMetadata(target.clusterId, target.kind, target.namespace, target.name,
        fromKvList(v.labels), fromKvList(v.annotations))
      message.success('已保存')
      onSaved?.()
      onClose()
    } catch {
      message.error('保存失败')
    } finally {
      setLoading(false)
    }
  }

  const label = target?.kind === 'secret' ? '保密字典' : '配置字典'
  return (
    <Modal title={`编辑信息：${target?.name || ''}（${label}）`} open={!!target} onCancel={onClose}
      onOk={submit} okText="保存" confirmLoading={loading} width={760}>
      <Form form={form} layout="vertical">
        <Form.Item label="标签 Labels">
          <KeyValueList name="labels" addLabel="添加标签" />
        </Form.Item>
        <Form.Item label="注解 Annotations" style={{ marginBottom: 0 }}>
          <KeyValueList name="annotations" addLabel="添加注解" />
        </Form.Item>
      </Form>
    </Modal>
  )
}
