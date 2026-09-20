// 风险评分策略页：四维评分权重 + 等级分段 + 审核阈值（admin）
import { useEffect, useState } from 'react'
import { Card, Button, Form, InputNumber, Space, message, Divider, Tag, Alert } from 'antd'
import { SaveOutlined } from '@ant-design/icons'
import { getRiskPolicy, updateRiskPolicy } from '../services/api'

const OP_LABELS = ['READ', 'UPDATE', 'CREATE', 'DELETE']
const RES_LABELS = ['pod', 'configmap', 'secret', 'deployment', 'statefulset', 'daemonset', 'service', 'ingress', 'namespace', 'node']
const IMPACT_LABELS = ['multi_replica', 'unknown', 'stateful', 'single_replica']
const ENV_LABELS = ['qa', 'release', 'prod']

interface RiskPolicy {
  approval_threshold?: number
  timeout_minutes?: number
  levels?: number[]
  operation?: Record<string, number>
  resource?: Record<string, number>
  impact?: Record<string, number>
  env?: Record<string, number>
}

export default function RiskPolicy() {
  const [loading, setLoading] = useState(false)
  const [policy, setPolicy] = useState<RiskPolicy | null>(null)
  const [form] = Form.useForm()

  const load = () => {
    setLoading(true)
    getRiskPolicy().then((d) => {
      const p = d as RiskPolicy
      setPolicy(p)
      // 展平嵌套结构到 form 字段
      const flat: Record<string, unknown> = {
        approval_threshold: p.approval_threshold,
        timeout_minutes: p.timeout_minutes,
        level_1: p.levels?.[0], level_2: p.levels?.[1], level_3: p.levels?.[2], level_4: p.levels?.[3],
      }
      OP_LABELS.forEach((op) => { flat[`op_${op}`] = p.operation?.[op] })
      RES_LABELS.forEach((r) => { flat[`res_${r}`] = p.resource?.[r] })
      IMPACT_LABELS.forEach((i) => { flat[`imp_${i}`] = p.impact?.[i] })
      ENV_LABELS.forEach((e) => { flat[`env_${e}`] = p.env?.[e] })
      form.setFieldsValue(flat)
    }).finally(() => setLoading(false))
  }

  useEffect(() => { load() }, [])

  const handleSave = async () => {
    const v = await form.validateFields()
    const payload: {
      operation: Record<string, number>
      resource: Record<string, number>
      impact: Record<string, number>
      env: Record<string, number>
      levels: number[]
      approval_threshold: number
      timeout_minutes: number
    } = {
      operation: {}, resource: {}, impact: {}, env: {},
      levels: [v.level_1, v.level_2, v.level_3, v.level_4],
      approval_threshold: v.approval_threshold,
      timeout_minutes: v.timeout_minutes,
    }
    OP_LABELS.forEach((op) => { payload.operation[op] = v[`op_${op}`] })
    RES_LABELS.forEach((r) => { payload.resource[r] = v[`res_${r}`] })
    IMPACT_LABELS.forEach((i) => { payload.impact[i] = v[`imp_${i}`] })
    ENV_LABELS.forEach((e) => { payload.env[e] = v[`env_${e}`] })
    await updateRiskPolicy(payload)
    message.success('风险评分策略已保存')
    load()
  }

  if (!policy) return <Card loading={loading} />

  return (
    <div>
      <Alert
        style={{ marginBottom: 16 }}
        type="info"
        showIcon
        message="四维风险评分：操作类型 × 资源级别 × 影响面 × 环境权重，总分 → 等级1-5，等级≥阈值需审核（4/5级双人审）。"
      />
      <Form form={form} layout="vertical">
        <Card title="操作类型分值" size="small" style={{ marginBottom: 16 }}>
          <Space size="large" wrap>
            {OP_LABELS.map((op) => (
              <Form.Item key={op} name={`op_${op}`} label={op}><InputNumber min={0} max={100} /></Form.Item>
            ))}
          </Space>
        </Card>

        <Card title="资源级别分值" size="small" style={{ marginBottom: 16 }}>
          <Space size="large" wrap>
            {RES_LABELS.map((r) => (
              <Form.Item key={r} name={`res_${r}`} label={r}><InputNumber min={0} max={100} /></Form.Item>
            ))}
          </Space>
        </Card>

        <Card title="影响面分值" size="small" style={{ marginBottom: 16 }}>
          <Space size="large" wrap>
            {IMPACT_LABELS.map((i) => (
              <Form.Item key={i} name={`imp_${i}`} label={i}><InputNumber min={0} max={100} /></Form.Item>
            ))}
          </Space>
        </Card>

        <Card title="环境权重" size="small" style={{ marginBottom: 16 }}>
          <Space size="large" wrap>
            {ENV_LABELS.map((e) => (
              <Form.Item key={e} name={`env_${e}`} label={e}><InputNumber min={0} max={100} /></Form.Item>
            ))}
          </Space>
        </Card>

        <Card title="等级分段与审核阈值" size="small" style={{ marginBottom: 16 }}>
          <Space size="large" wrap>
            {[1, 2, 3, 4].map((i) => (
              <Form.Item key={i} name={`level_${i}`} label={`等级 ${i} 上限分`}><InputNumber min={0} max={200} /></Form.Item>
            ))}
            <Form.Item name="approval_threshold" label="审核阈值（等级≥此值需审）"><InputNumber min={1} max={5} /></Form.Item>
            <Form.Item name="timeout_minutes" label="审批超时（分钟）"><InputNumber min={1} max={1440} /></Form.Item>
          </Space>
          <Divider />
          <div style={{ color: '#999', fontSize: 12 }}>
            等级分段说明：得分 ≤ 等级1上限 → 1级；≤ 等级2上限 → 2级；以此类推；超过等级4上限 → 5级。
            <Tag style={{ marginLeft: 8 }}>1-3级</Tag> 单人审批（或免审），<Tag color="orange">4-5级</Tag> 双人审批。
          </div>
        </Card>

        <Button type="primary" icon={<SaveOutlined />} onClick={handleSave}>保存策略</Button>
      </Form>
    </div>
  )
}
