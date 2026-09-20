// 用户与权限（合并页）：用户管理 / 项目管理 / 权限管理
import { Tabs } from 'antd'
import UserManagement from './UserManagement'
import ProjectManagement from './ProjectManagement'
import Permissions from './Permissions'

export default function UserPermission() {
  return (
    <Tabs
      defaultActiveKey="users"
      destroyInactiveTabPane
      items={[
        { key: 'users', label: '用户管理', children: <UserManagement /> },
        { key: 'projects', label: '项目管理', children: <ProjectManagement /> },
        { key: 'permissions', label: '权限管理', children: <Permissions /> },
      ]}
    />
  )
}
