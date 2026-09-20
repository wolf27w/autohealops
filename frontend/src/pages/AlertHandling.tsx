// 告警处理（合并页）：告警列表 / 故障诊断 / 审批列表 / 处理记录 / 批量处理
import { Tabs } from 'antd'
import AlertList from './AlertList'
import FaultDiagnosis from './FaultDiagnosis'
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
        { key: 'fault-diagnosis', label: '故障诊断', children: <FaultDiagnosis /> },
        { key: 'approvals', label: '审批列表', children: <ApprovalList /> },
        { key: 'records', label: '处理记录', children: <ProcessingRecords /> },
        { key: 'batch', label: '批量处理', children: <BatchOperation /> },
      ]}
    />
  )
}
