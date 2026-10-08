// 配置类对象（ConfigMap/Secret）YAML 创建/编辑弹窗
import { useEffect, useState } from 'react'
import { Modal, Input, Alert, message } from 'antd'
import { getObjectYaml, applyObjectYaml } from '../services/api'

export interface ConfigObjectTarget {
  clusterId: number
  kind: string        // configmap / secret
  namespace: string
  name: string        // 空 = 新建
  create?: boolean
}

function template(kind: string, ns: string): string {
  if (kind === 'secret') {
    return `apiVersion: v1\nkind: Secret\nmetadata:\n  name: \n  namespace: ${ns || 'default'}\ntype: Opaque\nstringData:\n  key: value\n`
  }
  return `apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: \n  namespace: ${ns || 'default'}\ndata:\n  key: value\n`
}

export default function ConfigObjectYamlModal({ target, onClose, onSaved }: {
  target: ConfigObjectTarget | null
  onClose: () => void
  onSaved?: () => void
}) {
  const [yaml, setYaml] = useState('')
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!target) return
    if (target.create || !target.name) {
      setYaml(template(target.kind, target.namespace))
      return
    }
    setLoading(true)
    getObjectYaml(target.clusterId, target.kind, target.namespace, target.name)
      .then((d) => setYaml((d as { yaml?: string }).yaml || ''))
      .catch(() => message.error('YAML 获取失败'))
      .finally(() => setLoading(false))
  }, [target])

  const apply = async () => {
    if (!target) return
    if (!yaml.trim()) { message.warning('YAML 不能为空'); return }
    setLoading(true)
    try {
      await applyObjectYaml(target.clusterId, target.kind, yaml)
      message.success(target.create ? '已创建' : '已保存')
      onSaved?.()
      onClose()
    } catch {
      message.error('应用失败')
    } finally {
      setLoading(false)
    }
  }

  const label = target?.kind === 'secret' ? '保密字典' : '配置字典'
  return (
    <Modal
      title={`${target?.create ? '创建' : '编辑'}${label} YAML`}
      open={!!target}
      onCancel={onClose}
      onOk={apply}
      okText={target?.create ? '创建' : '保存'}
      confirmLoading={loading}
      width={920}
    >
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 8 }}
        message="改的是 k8s 原生 YAML（apiVersion/kind/metadata/data），应用后立即生效；Secret 建议用 stringData 写明文（k8s 自动 base64）。"
      />
      <Input.TextArea
        value={yaml}
        onChange={(e) => setYaml(e.target.value)}
        rows={22}
        style={{ fontFamily: 'monospace', fontSize: 12 }}
      />
    </Modal>
  )
}
