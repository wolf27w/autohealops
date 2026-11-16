// 告警处理（合并页）：告警列表 / 审批列表 / 处理记录 / 批量处理
// 2026-11-16：去掉「故障诊断」Tab —— 与「诊断与自愈」里的应用/集群故障诊断重复，统一从告警行的诊断入口看
import { Tabs } from 'antd'
import AlertList from './AlertList'
import ApprovalList from './ApprovalList'
import ProcessingRecords from './ProcessingRecords'
import BatchOperation from './BatchOperation'

export default function AlertHandling() {
  return (
    <Tabs
      defaultActiveKey="alerts"
      destroyInactiveTabPane
      items={[
        { key: 'alerts', label: '告警列表', children: <AlertList /> },
        { key: 'approvals', label: '审批列表', children: <ApprovalList /> },
        { key: 'records', label: '处理记录', children: <ProcessingRecords /> },
        { key: 'batch', label: '批量处理', children: <BatchOperation /> },
      ]}
    />
  )
}
