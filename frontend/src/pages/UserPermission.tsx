// 用户与权限（合并页）：用户管理 / 权限管理
// 权限管理内部再分：按项目授权 / 按集群授权 / 按数据库授权 / 我的告警订阅
import { Tabs } from 'antd'
import UserManagement from './UserManagement'
import Permissions from './Permissions'

export default function UserPermission() {
  return (
    <Tabs
      defaultActiveKey="users"
      destroyInactiveTabPane
      items={[
        { key: 'users', label: '用户管理', children: <UserManagement /> },
        { key: 'permissions', label: '权限管理', children: <Permissions /> },
      ]}
    />
  )
}
