// 占位页（R32-P1）：集群内「监控告警 / 巡检与诊断」在后续阶段接入，
// 先把导航与路由打通，避免点进去 404。
import { Empty, Typography } from 'antd'

export default function ComingSoon({ title, note }: { title: string; note?: string }) {
  return (
    <div style={{ paddingTop: 80 }}>
      <Typography.Title level={4} style={{ textAlign: 'center', marginBottom: 8 }}>{title}</Typography.Title>
      <Empty description={note || '该页签在下一阶段接入（现有数据入口暂时保留在左侧平台菜单）'} />
    </div>
  )
}
