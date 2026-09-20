// 公共常量（去重：原各页自写的状态/级别文案与颜色）
export const ALERT_STATUS_LABEL: Record<string, string> = {
  pending: '待处理',
  awaiting_approval: '审批中',
  resolved: '已解决',
  self_healed: '已自愈',
}

export const SEVERITY_LABEL: Record<string, string> = {
  critical: '严重',
  high: '高',
  medium: '中',
  low: '低',
}
