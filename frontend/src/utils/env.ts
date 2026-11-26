// 环境维度（R24）：集群/数据库/告警/工单统一用 env_tag，展示时映射成中文标签
export interface EnvMeta { value: string; label: string; color: string }

export const ENV_OPTIONS: EnvMeta[] = [
  { value: 'prod', label: '生产', color: 'red' },
  // 2026-11-26：新增独立 uat 环境（原来 UAT 借用 release 标签，语义混淆）
  { value: 'uat', label: 'UAT', color: 'orange' },
  { value: 'release', label: '预发', color: 'gold' },
  { value: 'qa', label: '测试', color: 'green' },
]

export function envLabel(v?: string | null): string {
  if (!v) return '-'
  return ENV_OPTIONS.find((e) => e.value === v)?.label ?? v
}

export function envColor(v?: string | null): string {
  if (!v) return 'default'
  return ENV_OPTIONS.find((e) => e.value === v)?.color ?? 'blue'
}
