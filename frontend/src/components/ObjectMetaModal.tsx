// 通用「编辑信息」弹窗（别名 / 描述）—— 对齐 KubeSphere「编辑信息」入口
// 名称只读展示；保存时保留现有 labels 与其它 annotations，仅更新 alias-name / description 两个注解
// 适配任意资源类型（configmap / secret / service / ingress / storageclass ...），不写死对象类型
import { useEffect, useState } from 'react'
import { Modal, Form, Input, message } from 'antd'
import { getObjectDetail, getObjectYaml, updateObjectMetadata } from '../services/api'

const ALIAS_KEY = 'kubesphere.io/alias-name'
const DESC_KEY = 'kubesphere.io/description'

interface Detail {
  labels?: Record<string, string>
  annotations?: Record<string, string>
}

// 从 YAML 文本里按缩进解析 metadata 下 labels / annotations 两个块（getObjectDetail 失败时的兜底）
function parseBlock(yaml: string, key: 'labels' | 'annotations'): Record<string, string> {
  const out: Record<string, string> = {}
  const lines = yaml.split('\n')
  let inBlock = false
  let indent = -1
  for (const line of lines) {
    const head = line.match(/^(\s*)(labels|annotations):\s*$/)
    if (head && head[2] === key) {
      inBlock = true
      indent = head[1].length
      continue
    }
    if (!inBlock) continue
    if (!line.trim()) continue
    const curIndent = (line.match(/^\s*/) || [''])[0].length
    if (curIndent <= indent) { inBlock = false; continue }
    const kv = line.match(/^\s+([^:]+):\s*(.*)$/)
    if (kv) {
      let v = kv[2].trim().replace(/\s*#.*$/, '')
      if ((v.startsWith('"') && v.endsWith('"')) || (v.startsWith("'") && v.endsWith("'"))) v = v.slice(1, -1)
      out[kv[1].trim()] = v
    }
  }
  return out
}

export default function ObjectMetaModal({ open, clusterId, kind, namespace, name, title, onClose, onSaved }: {
  open: boolean
  clusterId: number
  kind: string
  namespace: string
  name: string
  title?: string
  onClose: () => void
  onSaved?: () => void
}) {
  const [form] = Form.useForm()
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  // 打开时取到的 labels/annotations（detail 优先，YAML 兜底），保存时兜底用
  const [current, setCurrent] = useState<Detail>({})

  useEffect(() => {
    if (!open) return
    setLoading(true)
    getObjectDetail(clusterId, kind, namespace, name)
      .then((d) => {
        const t = (d as Detail) || {}
        setCurrent({ labels: t.labels || {}, annotations: t.annotations || {} })
        form.setFieldsValue({
          name,
          alias: t.annotations?.[ALIAS_KEY] || '',
          description: t.annotations?.[DESC_KEY] || '',
        })
      })
      .catch(() => {
        // 退回 YAML 正则解析现有注解
        getObjectYaml(clusterId, kind, namespace, name)
          .then((d) => {
            const yaml = (d as { yaml?: string }).yaml || ''
            const labels = parseBlock(yaml, 'labels')
            const annotations = parseBlock(yaml, 'annotations')
            setCurrent({ labels, annotations })
            form.setFieldsValue({ name, alias: annotations[ALIAS_KEY] || '', description: annotations[DESC_KEY] || '' })
          })
          .catch(() => message.error('加载失败'))
      })
      .finally(() => setLoading(false))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, clusterId, kind, namespace, name])

  const submit = async () => {
    const v = await form.validateFields()
    const alias = (v.alias || '').trim()
    const description = (v.description || '').trim()
    setSaving(true)
    try {
      // 保存前重新拉取最新 metadata（避免覆盖他人改动）；失败则退回打开时缓存
      let detail = current
      try {
        detail = (await getObjectDetail(clusterId, kind, namespace, name)) as Detail
      } catch { /* 用缓存 */ }
      const ann = { ...(detail.annotations || {}) }
      ann[ALIAS_KEY] = alias
      ann[DESC_KEY] = description
      await updateObjectMetadata(clusterId, kind, namespace, name, detail.labels || {}, ann)
      message.success('已保存')
      onSaved?.()
      onClose()
    } catch {
      message.error('保存失败')
    } finally {
      setSaving(false)
    }
  }

  return (
    <Modal title={title ?? '编辑信息'} open={open} onCancel={onClose}
      onOk={submit} okText="保存" okButtonProps={{ disabled: loading }} confirmLoading={saving} width={640}>
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
