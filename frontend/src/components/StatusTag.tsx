// 状态标签：根据 status 值渲染不同颜色
import { Tag } from 'antd'

const GREEN = ['healthy', 'success', 'resolved', 'approved', 'completed', 'healed']
const RED = ['unreachable', 'failed', 'rejected', 'worsened', 'critical']
const BLUE = ['pending', 'acknowledged', 'running']
const ORANGE = ['in_progress', 'executing']
const GRAY = ['timeout', 'skipped', 'degraded']

export default function StatusTag({ status, label }: { status: string; label?: string }) {
  let color = 'default'
  if (GREEN.includes(status)) color = 'green'
  else if (RED.includes(status)) color = 'red'
  else if (BLUE.includes(status)) color = 'blue'
  else if (ORANGE.includes(status)) color = 'orange'
  else if (GRAY.includes(status)) color = 'gray'
  return <Tag color={color}>{label ?? status}</Tag>
}
