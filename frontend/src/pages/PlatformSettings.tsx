// 系统设置页：平台级配置（Prometheus/ES/LDAP）+ 密码修改
import { useEffect, useState } from 'react'
import { Card, Tabs, Form, Input, InputNumber, Switch, Button, message, Divider, Select, Space } from 'antd'
import { getSettings, updateSettings, changePassword, testEmbedding } from '../services/api'
import RiskPolicy from './RiskPolicy'

export default function PlatformSettings() {
  const [settings, setSettings] = useState<Record<string, unknown>>({})
  // Embedding 连通性测试状态（N: 配置就绪后可直接验证）
  const [embTest, setEmbTest] = useState<{ ok: boolean; msg: string } | null>(null)
  const [embTesting, setEmbTesting] = useState(false)
  const [form] = Form.useForm()
  const [pwdForm] = Form.useForm()

  useEffect(() => {
    getSettings().then((d) => {
      const s = d as { ldap_enabled?: string; ldap_port?: string | number }
      setSettings(d)
      form.setFieldsValue({
        ...d,
        ldap_enabled: s.ldap_enabled === 'true',
        ldap_port: Number(s.ldap_port),
      })
    })
  }, [])

  const handleSave = async () => {
    const values = await form.validateFields()
    // LDAP 字段在未展开的页签里，validateFields 拿不到 → 用当前 settings 兜底，避免存成 "undefined"
    const payload = {
      ...values,
      ldap_enabled: values.ldap_enabled === undefined
        ? String(settings.ldap_enabled ?? 'false')
        : (values.ldap_enabled ? 'true' : 'false'),
      ldap_port: values.ldap_port === undefined
        ? String(settings.ldap_port ?? '389')
        : String(values.ldap_port),
    }
    await updateSettings(payload)
    message.success('设置已保存')
  }

  const handleChangePwd = async () => {
    const values = await pwdForm.validateFields()
    await changePassword(values)
    message.success('密码已修改')
    pwdForm.resetFields()
  }

  return (
    <div>
      <Tabs
        items={[
          {
            key: 'observability',
            label: '可观测性配置',
            children: (
              <Card>
                <Divider orientation="left">监控（Prometheus）</Divider>
                <Form form={form} layout="vertical">
                  <Form.Item name="prometheus_url" label="Prometheus 地址">
                    <Input placeholder="http://prometheus:9090" />
                  </Form.Item>
                  <Divider orientation="left">飞书应用（个人消息通知）</Divider>
                  <div style={{ color: '#8c8c8c', fontSize: 13, marginBottom: 8 }}>
                    这是<strong>飞书应用凭证</strong>（用于给值班人发<strong>个人消息</strong>与长连接事件）；<strong>群通知渠道</strong>（群机器人 webhook / 卡片）在「通知配置」页维护。两处用途不同，不重复。
                  </div>
                  <Form.Item name="feishu_app_id" label="飞书 App ID">
                    <Input placeholder="cli_xxxxx" />
                  </Form.Item>
                  <Form.Item name="feishu_app_secret" label="飞书 App Secret">
                    <Input.Password placeholder="飞书应用凭证" />
                  </Form.Item>
                  <Button type="primary" onClick={handleSave}>保存配置</Button>
                </Form>
              </Card>
            ),
          },
          {
            key: 'embedding',
            label: 'Embedding 配置',
            children: (
              <Card>
                <Divider orientation="left">知识库向量模型（远程 API，可随时切换）</Divider>
                <div style={{ marginBottom: 8, fontSize: 13, color: settings.embedding_base_url && settings.embedding_model ? '#52c41a' : '#faad14' }}>
                  当前状态：{settings.embedding_base_url && settings.embedding_model
                    ? `已配置（${String(settings.embedding_base_url)} / ${String(settings.embedding_model)}）`
                    : '未配置 —— 未配置时知识库向量检索自动跳过（不影响告警诊断主流程），配置好保存即可启用'}
                </div>
                <Form form={form} layout="vertical">
                  <Form.Item name="embedding_provider" label="供应商">
                    <Input placeholder="siliconflow / zhipu / dashscope / openai 等" />
                  </Form.Item>
                  <Form.Item name="embedding_base_url" label="API 地址（OpenAI 兼容）">
                    <Input placeholder="https://api.siliconflow.cn/v1" />
                  </Form.Item>
                  <Form.Item name="embedding_model" label="模型名">
                    <Input placeholder="BAAI/bge-m3 / text-embedding-v3" />
                  </Form.Item>
                  <Form.Item name="embedding_api_key" label="API Key（可选：本地/内网服务可留空）">
                    <Input.Password placeholder="知识库检索用，非 LLM Key；本地服务无需密钥可留空" />
                  </Form.Item>
                  <Space>
                    <Button type="primary" onClick={handleSave}>保存配置</Button>
                    <Button loading={embTesting} onClick={async () => {
                      setEmbTesting(true) ; setEmbTest(null)
                      try {
                        const v = form.getFieldsValue(['embedding_provider', 'embedding_base_url', 'embedding_model', 'embedding_api_key'])
                        const d = await testEmbedding({
                          provider: v.embedding_provider, base_url: v.embedding_base_url,
                          model: v.embedding_model, api_key: v.embedding_api_key,
                        }) as { ok?: boolean; dimension?: number; error?: string }
                        setEmbTest(d?.ok ? { ok: true, msg: `连通正常，向量维度 ${d.dimension}` } : { ok: false, msg: d?.error || '测试失败' })
                      } catch { setEmbTest({ ok: false, msg: '测试请求失败（详情见右上角提示）' }) } finally { setEmbTesting(false) }
                    }}>测试连通性</Button>
                  </Space>
                  {embTest && (
                    <div style={{ marginTop: 8, fontSize: 13, color: embTest.ok ? '#52c41a' : '#ff4d4f' }}>
                      {embTest.ok ? '✅ ' : '❌ '}{embTest.msg}
                    </div>
                  )}
                </Form>
              </Card>
            ),
          },
          {
            key: 'ldap',
            label: 'LDAP 设置',
            children: (
              <Card>
                <Form form={form} layout="vertical">
                  <Form.Item name="ldap_enabled" label="开启 LDAP 登录" valuePropName="checked">
                    <Switch />
                  </Form.Item>
                  <Form.Item name="ldap_host" label="LDAP 服务器地址">
                    <Input placeholder="ldap.example.com" />
                  </Form.Item>
                  <Form.Item name="ldap_port" label="LDAP 端口">
                    <InputNumber min={1} max={65535} />
                  </Form.Item>
                  <Form.Item name="ldap_base_dn" label="Base DN">
                    <Input placeholder="dc=example,dc=com" />
                  </Form.Item>
                  <Form.Item name="ldap_bind_dn" label="绑定 DN">
                    <Input placeholder="cn=admin,dc=example,dc=com" />
                  </Form.Item>
                  <Form.Item name="ldap_bind_password" label="绑定密码">
                    <Input.Password />
                  </Form.Item>
                  <Form.Item name="ldap_user_filter" label="用户过滤模板">
                    <Input placeholder="(uid={username})" />
                  </Form.Item>
                  <Form.Item name="ldap_default_role" label="新用户默认角色" initialValue="viewer">
                    <Select options={[
                      { value: 'viewer', label: 'viewer（只读）' },
                      { value: 'operator', label: 'operator（运维）' },
                      { value: 'admin', label: 'admin（管理员）' },
                    ]} />
                  </Form.Item>
                  <Button type="primary" onClick={handleSave}>保存 LDAP 配置</Button>
                </Form>
              </Card>
            ),
          },
          {
            key: 'ai-diag',
            label: 'AI 诊断',
            children: (
              <Card>
                <Divider orientation="left">深度诊断</Divider>
                <Form form={form} layout="vertical">
                  <Form.Item name="agent_max_tool_steps" label="单次对话最大工具步数（1~100）"
                    tooltip="深度诊断模式下，单次对话最多执行的「有效」工具步数；重复调用不占额。默认 12。">
                    <InputNumber min={1} max={100} />
                  </Form.Item>
                  <Button type="primary" onClick={handleSave}>保存配置</Button>
                </Form>
              </Card>
            ),
          },
          {
            key: 'risk-policy',
            label: '风险评分策略',
            children: <RiskPolicy />,
          },
          {
            key: 'password',
            label: '修改密码',
            children: (
              <Card style={{ maxWidth: 420 }}>
                <Form form={pwdForm} layout="vertical" onFinish={handleChangePwd}>
                  <Form.Item name="old_password" label="旧密码" rules={[{ required: true }]}>
                    <Input.Password />
                  </Form.Item>
                  <Form.Item name="new_password" label="新密码" rules={[{ required: true, min: 6 }]}>
                    <Input.Password />
                  </Form.Item>
                  <Form.Item name="confirm" label="确认新密码" dependencies={['new_password']}
                    rules={[{ required: true }, ({ getFieldValue }) => ({
                      validator(_, value) {
                        return !value || getFieldValue('new_password') === value ? Promise.resolve() : Promise.reject(new Error('两次密码不一致'))
                      },
                    })]}>
                    <Input.Password />
                  </Form.Item>
                  <Button type="primary" htmlType="submit">修改密码</Button>
                </Form>
              </Card>
            ),
          },
        ]}
      />
    </div>
  )
}
