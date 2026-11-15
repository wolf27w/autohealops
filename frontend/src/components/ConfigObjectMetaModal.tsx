// 配置字典 / 保密字典「编辑信息」弹窗 —— 薄封装，内部渲染通用 ObjectMetaModal
// 保留此文件以避免其它引用点（原 StorageConfig 等）直接依赖通用组件导致签名变更时炸
import ObjectMetaModal from './ObjectMetaModal'
import type { ConfigObjectTarget } from './ConfigObjectYamlModal'

export default function ConfigObjectMetaModal({ target, onClose, onSaved }: {
  target: ConfigObjectTarget | null
  onClose: () => void
  onSaved?: () => void
}) {
  const label = target?.kind === 'secret' ? '保密字典' : '配置字典'
  return (
    <ObjectMetaModal
      open={!!target}
      clusterId={target?.clusterId ?? 0}
      kind={target?.kind ?? 'configmap'}
      namespace={target?.namespace ?? ''}
      name={target?.name ?? ''}
      title={target ? `编辑信息：${label}` : undefined}
      onClose={onClose}
      onSaved={onSaved}
    />
  )
}
