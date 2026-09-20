// 通知配置（合并页）：通知渠道配置 / 消息群
import { Tabs } from 'antd'
import NotificationConfig from './NotificationConfig'
import IMGroups from './IMGroups'

export default function NotificationCenter() {
  return (
    <Tabs
      defaultActiveKey="configs"
      destroyInactiveTabPane
      items={[
        { key: 'configs', label: '通知渠道配置', children: <NotificationConfig /> },
        { key: 'im-groups', label: '消息群', children: <IMGroups /> },
      ]}
    />
  )
}
