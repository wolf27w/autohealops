// 自愈规则与审批（合并页，R34）：自愈规则 / 审批流程
// 告警策略已独立到 /alerts/policies；风险评分（决策策略）暂挂自愈规则页内入口
import { Tabs } from 'antd'
import SelfHealRules from './SelfHealRules'
import ApprovalFlows from './ApprovalFlows'

export default function DiagnoseStrategy() {
  return (
    <Tabs
      defaultActiveKey="self-heal"
      destroyInactiveTabPane
      items={[
        { key: 'self-heal', label: '自愈规则', children: <SelfHealRules /> },
        { key: 'approval', label: '审批流程', children: <ApprovalFlows /> },
      ]}
    />
  )
}
