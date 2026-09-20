// 严重级别标签
import { Tag } from 'antd'

export default function SeverityTag({ severity }: { severity: string }) {
  const colorMap: Record<string, string> = {
    critical: 'red',
    high: 'orange',
    medium: 'gold',
    low: 'blue',
  }
  return <Tag color={colorMap[severity] || 'default'}>{severity}</Tag>
}
