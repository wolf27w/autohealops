// 策略配置（合并页）：告警策略 / 自愈规则 / 审批流程
import { Tabs } from 'antd'
import AlertPolicies from './AlertPolicies'
import SelfHealRules from './SelfHealRules'
import ApprovalFlows from './ApprovalFlows'

export default function StrategyConfig() {
  return (
    <>
      <div style={{ marginBottom: 8, color: '#8c8c8c', fontSize: 12 }}>
        三套策略的分工：<b>告警策略</b>=故障检测/分级/是否自动处理（A/B/C/D 类）；<b>自愈规则</b>=对自动处理的<em>决策覆盖</em>（强制人工 / 强制自动 / 影子模式）；<b>审批流程</b>=高风险操作走谁审、提醒节奏。风险评分（分值与等级阈值）在「决策策略」页配置。
      </div>
      <Tabs
      defaultActiveKey="alert-policies"
      destroyInactiveTabPane
      items={[
        { key: 'alert-policies', label: '告警策略', children: <AlertPolicies /> },
        { key: 'self-heal-rules', label: '自愈规则', children: <SelfHealRules /> },
        { key: 'approval-flows', label: '审批流程', children: <ApprovalFlows /> },
      ]}
    />
    </>
  )
}
