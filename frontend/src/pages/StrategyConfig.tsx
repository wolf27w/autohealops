// 策略配置（合并页）：告警策略 / 自愈规则 / 审批流程
import { Tabs } from 'antd'
import AlertPolicies from './AlertPolicies'
import SelfHealRules from './SelfHealRules'
import ApprovalFlows from './ApprovalFlows'

export default function StrategyConfig() {
  return (
    <Tabs
      defaultActiveKey="alert-policies"
      destroyInactiveTabPane
      items={[
        { key: 'alert-policies', label: '告警策略', children: <AlertPolicies /> },
        { key: 'self-heal-rules', label: '自愈规则', children: <SelfHealRules /> },
        { key: 'approval-flows', label: '审批流程', children: <ApprovalFlows /> },
      ]}
    />
  )
}
