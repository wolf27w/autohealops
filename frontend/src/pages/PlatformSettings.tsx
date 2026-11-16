// 系统设置页：平台级配置（Prometheus/ES/LDAP）+ 密码修改
import { useCallback, useEffect, useState } from 'react'
import { Card, Tabs, Form, Input, InputNumber, Switch, Button, message, Divider, Select, Space, Table } from 'antd'
import { getSettings, updateSettings, changePassword, testEmbedding, getExternalToken, regenerateExternalToken, getMonitoringSources, setMonitoringSource, getTerminalSecurity, updateTerminalSecurity } from '../services/api'
import type { MonitoringSource, TerminalSecurity } from '../services/api'
import RiskPolicy from './RiskPolicy'
import ReleaseSettings from './ReleaseSettings'
import OracleClientsPanel from '../components/settings/OracleClientsPanel'

const TERMINAL_SEC_DEFAULTS: TerminalSecurity = { block_dangerous: true, allow_paste: true, watermark: true, no_copy: true }

function TerminalSecurityPane() {
  const [sec, setSec] = useState<TerminalSecurity>(TERMINAL_SEC_DEFAULTS)
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const d = await getTerminalSecurity()
      if (d) setSec(d)
    } catch { /* 拦截器已提示 */ } finally { setLoading(false) }
  }, [])

  useEffect(() => { load() }, [load])

  const save = async () => {
    setSaving(true)
    try {
      await updateTerminalSecurity(sec)
      message.success('已保存')
    } catch { /* 拦截器已提示 */ } finally { setSaving(false) }
  }

  const rows: { key: keyof TerminalSecurity; label: string; desc: string }[] = [
    { key: 'block_dangerous', label: '危险命令拦截', desc: '命中危险命令（如 rm -rf /）时拒绝执行并弹窗；关闭后仅审计不拦截' },
    { key: 'allow_paste', label: '允许粘贴', desc: '允许在终端内粘贴文本；关闭后终端内粘贴被禁用' },
    { key: 'watermark', label: '显示水印', desc: '终端区域覆盖用户名/主机/时间水印（防截屏溯源），不挡鼠标操作' },
    { key: 'no_copy', label: '禁止复制', desc: '拦截复制/剪切并清空剪贴板，右键菜单禁用；仍可鼠标框选' },
  ]

  return (
    <Card loading={loading}>
      <Divider orientation="left">Web 终端安全</Divider>
      <div style={{ color: '#8c8c8c', fontSize: 13, marginBottom: 16 }}>
        终端建立 WebSocket 连接时按此配置生效；保存后新连接即时生效。
      </div>
      {rows.map((r) => (
        <div key={r.key} style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 16 }}>
          <Switch checked={sec[r.key]} onChange={(v) => setSec((p) => ({ ...p, [r.key]: v }))} />
          <div>
            <div style={{ fontWeight: 500 }}>{r.label}</div>
            <div style={{ color: '#8c8c8c', fontSize: 12 }}>{r.desc}</div>
          </div>
        </div>
      ))}
      <Button type="primary" loading={saving} onClick={save}>保存</Button>
    </Card>
  )
}

export default function PlatformSettings() {
  const [settings, setSettings] = useState<Record<string, unknown>>({})
  // Embedding 连通性测试状态（N: 配置就绪后可直接验证）
  const [embTest, setEmbTest] = useState<{ ok: boolean; msg: string } | null>(null)
  const [embTesting, setEmbTesting] = useState(false)
  const [extToken, setExtToken] = useState('')
  const [sources, setSources] = useState<MonitoringSource[]>([])
  const [srcUrls, setSrcUrls] = useState<Record<number, string>>({})
  const [form] = Form.useForm()
  const [pwdForm] = Form.useForm()

  const loadSources = () => getMonitoringSources().then((d) => {
    const arr = (d || []) as MonitoringSource[]
    setSources(arr)
    const m: Record<number, string> = {}
    arr.forEach((x) => { m[x.cluster_id] = x.url })
    setSrcUrls(m)
  })
  useEffect(() => { loadSources() }, [])

  const saveSource = async (cid: number) => {
    await setMonitoringSource(cid, (srcUrls[cid] || '').trim())
    message.success('已保存')
    loadSources()
  }

  useEffect(() => {
    getSettings().then((d) => {
      const s = d as { ldap_enabled?: string; ldap_port?: string | number }
      setSettings(d)
      form.setFieldsValue({
        ...d,
        ldap_enabled: s.ldap_enabled === 'true',
        ldap_port: Number(s.ldap_port),
        smtp_ssl: d.smtp_ssl !== 'false',
        smtp_port: Number(d.smtp_port),
        sftp_max_mb: Number(d.sftp_max_mb ?? 200),
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
      smtp_ssl: values.smtp_ssl === undefined
        ? String(settings.smtp_ssl ?? 'true')
        : (values.smtp_ssl ? 'true' : 'false'),
      smtp_port: values.smtp_port === undefined
        ? String(settings.smtp_port ?? '465')
        : String(values.smtp_port),
      sftp_max_mb: String(values.sftp_max_mb ?? settings.sftp_max_mb ?? 200),
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
                <Divider orientation="left">监控（Prometheus 数据源）</Divider>
                <div style={{ color: '#8c8c8c', fontSize: 13, marginBottom: 8 }}>
                  默认由 executor 自动发现「集群自带 Prometheus」。如需指定外部/已暴露地址：全局地址对所有集群生效；下表可按集群覆盖（优先级高于全局）。
                </div>
                <Form form={form} layout="vertical">
                  <Form.Item name="prometheus_url" label="全局 Prometheus 地址（可选，留空=自动发现）">
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
                  <Divider orientation="left">数据保留</Divider>
                  <Space wrap>
                    <Form.Item name="snapshot_retention_days" label="快照/指标保留(天)"><InputNumber min={1} max={365} /></Form.Item>
                    <Form.Item name="inspection_retention_days" label="巡检报告保留(天)"><InputNumber min={1} max={365} /></Form.Item>
                    <Form.Item name="log_retention_days" label="日志保留(天)"><InputNumber min={1} max={365} /></Form.Item>
                  </Space>
                  <Divider orientation="left">堡垒机 SFTP（文件传输）</Divider>
                  <div style={{ color: '#8c8c8c', fontSize: 13, marginBottom: 8 }}>
                    上传/下载的目录范围与大小上限（逗号分隔；黑名单优先于白名单）。
                  </div>
                  <Form.Item name="sftp_allow_paths" label="允许目录（逗号分隔）">
                    <Input placeholder="/tmp,/var/tmp,/home" />
                  </Form.Item>
                  <Form.Item name="sftp_deny_paths" label="禁止目录（逗号分隔）">
                    <Input placeholder="/etc,/boot,/dev,/proc,/sys,/root/.ssh" />
                  </Form.Item>
                  <Form.Item name="sftp_max_mb" label="单文件上限(MB)"><InputNumber min={1} max={10240} /></Form.Item>
                  <Divider orientation="left">诊断模板路由（R11）</Divider>
                  <div style={{ color: '#8c8c8c', fontSize: 13, marginBottom: 8 }}>
                    fault_type → 诊断模板 映射（JSON）。匹配顺序：exact 精确 → prefix 前缀 → default 兜底；留空用内置默认表。
                  </div>
                  <Form.Item name="diagnosis_template_routing" label="诊断模板路由 JSON">
                    <Input.TextArea rows={6} placeholder={'{"exact":{"ContainerMemHigh":"application_diagnosis"},"prefix":{"PodRestart":"pod_crash_diagnosis"},"default":"default_diagnosis"}'} style={{ fontFamily: 'monospace' }} />
                  </Form.Item>
                  <Divider orientation="left">镜像仓库（列标签 / 更新镜像）</Divider>
                  <div style={{ color: '#8c8c8c', fontSize: 13, marginBottom: 8 }}>
                    私有仓库拉取标签需凭据：填全局用户名/密码即可；多仓库可用下方 JSON 按 host 覆盖（优先生效）。
                  </div>
                  <Form.Item name="registry_username" label="仓库用户名（全局）">
                    <Input placeholder="如 robot$autohealops" />
                  </Form.Item>
                  <Form.Item name="registry_password" label="仓库密码 / Token（全局）">
                    <Input.Password placeholder="私有仓库凭据" />
                  </Form.Item>
                  <Form.Item name="registry_creds" label="按 host 凭据（JSON，可选，优先于全局）">
                    <Input.TextArea rows={3} placeholder='{"harbor.example.com":{"username":"robot$x","password":"..."}}' />
                  </Form.Item>
                  <Button type="primary" onClick={handleSave}>保存配置</Button>
                </Form>
                <Divider orientation="left">按集群覆盖 Prometheus 地址</Divider>
                <Table<MonitoringSource>
                  size="small" rowKey="cluster_id" pagination={false} dataSource={sources}
                  columns={[
                    { title: '集群', dataIndex: 'cluster_name', width: 180 },
                    { title: 'Prometheus 地址（留空=自动发现）', render: (_v: unknown, r: MonitoringSource) => (
                      <Input value={srcUrls[r.cluster_id] ?? ''} placeholder="http://prometheus:9090"
                        onChange={(e) => setSrcUrls((m) => ({ ...m, [r.cluster_id]: e.target.value }))} />
                    ) },
                    { title: '操作', width: 90, render: (_v: unknown, r: MonitoringSource) => (
                      <Button size="small" onClick={() => saveSource(r.cluster_id)}>保存</Button>
                    ) },
                  ]}
                />
              </Card>
            ),
          },
          {
            key: 'smtp',
            label: 'SMTP 邮件',
            children: (
              <Card>
                <Divider orientation="left">SMTP 邮件（告警邮件渠道，配好即可用；未配则邮件渠道静默跳过）</Divider>
                <Form form={form} layout="vertical">
                  <Form.Item name="smtp_host" label="SMTP 服务器地址">
                    <Input placeholder="smtp.qq.com / smtp.exmail.qq.com" />
                  </Form.Item>
                  <Form.Item name="smtp_port" label="SMTP 端口">
                    <InputNumber min={1} max={65535} style={{ width: 180 }} />
                  </Form.Item>
                  <Form.Item name="smtp_username" label="SMTP 用户名（通常=发件邮箱）">
                    <Input placeholder="noreply@example.com" />
                  </Form.Item>
                  <Form.Item name="smtp_password" label="SMTP 密码 / 授权码">
                    <Input.Password placeholder="QQ 邮箱用授权码" />
                  </Form.Item>
                  <Form.Item name="smtp_from" label="发件人地址">
                    <Input placeholder="noreply@example.com（留空=用户名）" />
                  </Form.Item>
                  <Form.Item name="smtp_ssl" label="SSL（465 端口选是；587 选否用 STARTTLS）" valuePropName="checked">
                    <Switch />
                  </Form.Item>
                  <Button type="primary" onClick={handleSave}>保存 SMTP 配置</Button>
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
            key: 'external',
            label: '外部接入',
            children: (
              <Card>
                <Divider orientation="left">外部工单接入（供外部监控/运维系统对接）</Divider>
                <div style={{ marginBottom: 12, fontSize: 13, color: '#666' }}>
                  外部系统携带此 token 调用 API 查询/创建工单（请求头 <code>X-External-Token</code>）。外部建单 source 记为「external」，平台内可见。
                </div>
                <Space>
                  <Button onClick={async () => {
                    const d = await getExternalToken() as { token?: string }
                    setExtToken(d?.token || '')
                  }}>查看 / 生成 Token</Button>
                  <Button danger onClick={async () => {
                    await regenerateExternalToken()
                    const d = await getExternalToken() as { token?: string }
                    setExtToken(d?.token || '')
                    message.success('Token 已重置')
                  }}>重置 Token</Button>
                </Space>
                {extToken && (
                  <div style={{ marginTop: 14 }}>
                    <div style={{ fontSize: 13, marginBottom: 4 }}>Token：</div>
                    <Input.TextArea readOnly value={extToken} autoSize={{ minRows: 1, maxRows: 3 }} style={{ fontFamily: 'monospace' }} />
                    <div style={{ marginTop: 10, fontSize: 12, color: '#999', lineHeight: 1.9 }}>
                      创建工单：<br />
                      <code>curl -X POST https://&lt;平台地址&gt;/api/v1/external/tickets -H "X-External-Token: {extToken}" -H "Content-Type: application/json" -d {'{"title":"磁盘告警","priority":"high","description":"..."}'}</code>
                    </div>
                  </div>
                )}
              </Card>
            ),
          },
          {
            key: 'risk-policy',
            label: '风险评分策略',
            children: <RiskPolicy />,
          },
          {
            key: 'release',
            label: '发布',
            children: <ReleaseSettings />,
          },
          {
            key: 'terminal',
            label: 'Web 终端',
            children: <TerminalSecurityPane />,
          },
          {
            key: 'oracle-clients',
            label: 'Oracle 客户端',
            children: <OracleClientsPanel />,
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
