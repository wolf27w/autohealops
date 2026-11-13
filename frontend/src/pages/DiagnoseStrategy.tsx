// 自愈规则与审批（合并页，R34）：自愈规则 / 审批流程 / 决策策略（风险评分与时段规则）
// 告警策略已独立到 /alerts/policies
import { Tabs } from 'antd'
import { useSearchParams } from 'react-router-dom'
import SelfHealRules from './SelfHealRules'
import ApprovalFlows from './ApprovalFlows'
import DecisionPolicies from './DecisionPolicies'

const TAB_KEYS = ['self-heal', 'approval', 'decision'] as const

export default function DiagnoseStrategy() {
  const [sp, setSp] = useSearchParams()
  const tabParam = sp.get('tab')
  const tab = (TAB_KEYS as readonly string[]).includes(tabParam || '') ? (tabParam as string) : 'self-heal'
  return (
    <>
      <div style={{ marginBottom: 8, color: '#8c8c8c', fontSize: 12 }}>
        <b>自愈规则</b>=对自动处理的决策覆盖（强制人工 / 强制自动 / 影子模式）；<b>审批流程</b>=高风险操作走谁审、提醒节奏；<b>决策策略</b>=按时段/操作类型的自动放行规则与风险评分。
      </div>
      <Tabs
        activeKey={tab}
        onChange={(k) => setSp({ tab: k }, { replace: true })}
        destroyInactiveTabPane
        items={[
          { key: 'self-heal', label: '自愈规则', children: <SelfHealRules /> },
          { key: 'approval', label: '审批流程', children: <ApprovalFlows /> },
          { key: 'decision', label: '决策策略', children: <DecisionPolicies /> },
        ]}
      />
    </>
  )
}
