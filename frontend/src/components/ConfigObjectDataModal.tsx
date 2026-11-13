// 编辑设置：只改数据（对齐 KubeSphere 3.4.1「编辑设置」入口）
// ConfigMap → 数据键值对；Secret → 类型只读展示 + 数据键值对；创建统一走 ConfigObjectCreateModal
import { useEffect, useState } from 'react'
import { Modal, Form, Tag, message } from 'antd'
import { getObjectDetail, updateObjectData } from '../services/api'
import { KeyValueList, toKvList, fromKvList } from './KeyValueEditor'
import type { ConfigObjectTarget } from './ConfigObjectYamlModal'

const SECRET_TYPE_LABELS: Record<string, string> = {
  Opaque: '默认',
  'kubernetes.io/tls': 'TLS 信息',
  'kubernetes.io/dockerconfigjson': '镜像服务信息',
  'kubernetes.io/basic-auth': '用户名和密码',
  'kubernetes.io/ssh-auth': 'SSH 认证',
}

interface Detail {
  data?: Record<string, string>
  type?: string
}

export default function ConfigObjectDataModal({ target, onClose, onSaved }: {
  target: ConfigObjectTarget | null
  onClose: () => void
  onSaved?: () => void
}) {
  const [form] = Form.useForm()
  const [loading, setLoading] = useState(false)
  const [secType, setSecType] = useState('')

  useEffect(() => {
    if (!target) return
    setLoading(true)
    getObjectDetail(target.clusterId, target.kind, target.namespace, target.name)
      .then((d) => {
        const t = d as Detail
        setSecType(t.type || 'Opaque')
        form.setFieldsValue({ data: toKvList(t.data) })
      })
      .catch(() => message.error('加载失败'))
      .finally(() => setLoading(false))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [target])

  const submit = async () => {
    if (!target) return
    const v = await form.validateFields()
    setLoading(true)
    try {
      const data = fromKvList(v.data)
      await updateObjectData(target.clusterId, target.kind, target.namespace, target.name, data,
        target.kind === 'secret' ? secType : undefined)
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
      onOk={submit} okText="确定" confirmLoading={loading} width={760}>
      <Form form={form} layout="vertical">
        {isSecret && (
          <Form.Item label="类型" style={{ marginBottom: 16 }}>
            <Tag>{SECRET_TYPE_LABELS[secType] ? `${SECRET_TYPE_LABELS[secType]}（${secType}）` : secType || '-'}</Tag>
          </Form.Item>
        )}
        <Form.Item label="数据" required style={{ marginBottom: 0 }}>
          <KeyValueList name="data" addLabel="添加数据" textarea />
        </Form.Item>
      </Form>
    </Modal>
  )
}
