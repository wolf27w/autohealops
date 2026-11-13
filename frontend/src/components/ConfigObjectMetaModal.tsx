// 编辑信息（别名 / 描述）—— 对齐 KubeSphere 3.4.1「编辑信息」入口
// 名称只读展示；保存时保留现有 labels 与其它 annotations，仅更新 alias-name / description 两个注解
import { useEffect, useState } from 'react'
import { Modal, Form, Input, message } from 'antd'
import { getObjectDetail, updateObjectMetadata } from '../services/api'
import type { ConfigObjectTarget } from './ConfigObjectYamlModal'

const ALIAS_KEY = 'kubesphere.io/alias-name'
const DESC_KEY = 'kubesphere.io/description'

interface Detail {
  labels?: Record<string, string>
  annotations?: Record<string, string>
}

export default function ConfigObjectMetaModal({ target, onClose, onSaved }: {
  target: ConfigObjectTarget | null
  onClose: () => void
  onSaved?: () => void
}) {
  const [form] = Form.useForm()
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [labels, setLabels] = useState<Record<string, string>>({})

  useEffect(() => {
    if (!target) return
    setLoading(true)
    getObjectDetail(target.clusterId, target.kind, target.namespace, target.name)
      .then((d) => {
        const t = d as Detail
        setLabels(t.labels || {})
        const ann = t.annotations || {}
        form.setFieldsValue({
          name: target.name,
          alias: ann[ALIAS_KEY] || '',
          description: ann[DESC_KEY] || '',
        })
      })
      .catch(() => message.error('加载失败'))
      .finally(() => setLoading(false))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [target])

  const submit = async () => {
    if (!target) return
    const v = await form.validateFields()
    const alias = (v.alias || '').trim()
    const description = (v.description || '').trim()
    setSaving(true)
    try {
      const d = (await getObjectDetail(target.clusterId, target.kind, target.namespace, target.name)) as Detail
      const ann = { ...(d.annotations || {}) }
      ann[ALIAS_KEY] = alias
      ann[DESC_KEY] = description
      await updateObjectMetadata(target.clusterId, target.kind, target.namespace, target.name, labels, ann)
      message.success('已保存')
      onSaved?.()
      onClose()
    } catch {
      message.error('保存失败')
    } finally {
      setSaving(false)
    }
  }

  const label = target?.kind === 'secret' ? '保密字典' : '配置字典'
  return (
    <Modal title={`编辑信息：${target?.name || ''}（${label}）`} open={!!target} onCancel={onClose}
      onOk={submit} okText="保存" confirmLoading={saving} width={640}>
      <Form form={form} layout="vertical">
        <Form.Item name="name" label="名称">
          <Input disabled />
        </Form.Item>
        <Form.Item name="alias" label="别名" extra="别名只能包含中文、字母、数字和连字符（-），不得以连字符（-）开头或结尾，最长 63 个字符。" rules={[
          {
            validator: (_: unknown, val: string) => {
              if (!val || !val.trim()) return Promise.resolve()
              const t = val.trim()
              if (!/^[\u4e00-\u9fa5A-Za-z0-9]([\u4e00-\u9fa5A-Za-z0-9-]*[\u4e00-\u9fa5A-Za-z0-9])?$/.test(t) || t.length > 63) {
                return Promise.reject(new Error('别名只能包含中文、字母、数字和连字符（-），不得以连字符（-）开头或结尾，最长 63 个字符。'))
              }
              return Promise.resolve()
            },
          },
        ]}>
          <Input placeholder="请输入别名" />
        </Form.Item>
        <Form.Item name="description" label="描述" extra="描述可包含任意字符，最长 256 个字符。" rules={[
          { max: 256, message: '描述可包含任意字符，最长 256 个字符。' },
        ]}>
          <Input.TextArea rows={3} placeholder="请输入描述" />
        </Form.Item>
      </Form>
    </Modal>
  )
}
