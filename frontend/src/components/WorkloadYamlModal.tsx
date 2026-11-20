// 工作负载（整个服务）YAML 查看/编辑弹窗
import { useEffect, useState } from 'react'
import { Modal, Input, Alert, message } from 'antd'
import { getWorkloadYaml, applyWorkloadYaml } from '../services/api'

export interface WorkloadTarget {
  clusterId: number
  kind: string        // deployment/statefulset/daemonset/cronjob
  namespace: string
  name: string
}

export default function WorkloadYamlModal({ target, onClose, onApplied }: { target: WorkloadTarget | null; onClose: () => void; onApplied?: () => void }) {
  const [yaml, setYaml] = useState('')
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!target) return
    setLoading(true)
    getWorkloadYaml(target.clusterId, target.kind, target.namespace, target.name)
      .then((d) => setYaml((d as { yaml?: string }).yaml || ''))
      .catch(() => message.error('YAML 获取失败'))
      .finally(() => setLoading(false))
  }, [target])

  const apply = async () => {
    if (!target) return
    if (!yaml.trim()) { message.warning('YAML 不能为空'); return }
    setLoading(true)
    try {
      await applyWorkloadYaml(target.clusterId, target.kind, target.namespace, target.name, yaml)
      message.success('已应用（整个服务）')
      onClose()
      onApplied?.()
    } catch {
      message.error('YAML 应用失败')
    } finally {
      setLoading(false)
    }
  }

  return (
    <Modal
      title={`编辑 YAML（整个服务）：${target?.namespace || ''}/${target?.name || ''}`}
      open={!!target}
      onCancel={onClose}
      onOk={apply}
      okText="应用"
      confirmLoading={loading}
      width={920}
    >
      <Alert
        type="warning"
        showIcon
        style={{ marginBottom: 8 }}
        message="此处修改的是整个服务（工作负载），应用后会触发滚动更新/重建该服务全部 Pod，请谨慎操作。"
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
