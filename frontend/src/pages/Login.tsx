// 登录页：蓝色渐变背景 + 品牌区 + 登录表单（LDAP 勾选方式）
import { useState } from 'react'
import { Form, Input, Button, message, Checkbox } from 'antd'
import { UserOutlined, LockOutlined, SafetyCertificateOutlined } from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'
import { login } from '../services/api'

export default function Login() {
  const navigate = useNavigate()
  const [ldapLogin, setLdapLogin] = useState(false)

  const onFinish = async (values: { username: string; password: string }) => {
    try {
      const data = await login({ ...values, auth_type: ldapLogin ? 'ldap' : 'local' })
      localStorage.setItem('token', data.token)
      localStorage.setItem('user', JSON.stringify(data.user))
      // 供 iframe（Netdata 监控反代）自动携带：设置 cookie，浏览器对同源子请求自动带
      document.cookie = `apiops_token=${data.token}; path=/; max-age=86400; samesite=lax`
      message.success('登录成功')
      navigate('/dashboard')
    } catch (e) {
      // 错误已由拦截器提示
    }
  }

  return (
    <div style={{
      minHeight: '100vh',
      display: 'flex',
      background: 'linear-gradient(135deg, #1677ff 0%, #0958d9 40%, #003eb3 100%)',
      position: 'relative',
      overflow: 'hidden',
    }}>
      {/* 装饰圆 */}
      <div style={{ position: 'absolute', top: -120, right: -120, width: 400, height: 400, borderRadius: '50%', background: 'rgba(255,255,255,0.06)' }} />
      <div style={{ position: 'absolute', bottom: -160, left: -80, width: 500, height: 500, borderRadius: '50%', background: 'rgba(255,255,255,0.04)' }} />

      {/* 左侧品牌区 */}
      <div style={{
        flex: 1, display: 'flex', flexDirection: 'column', justifyContent: 'center',
        padding: '0 60px', color: '#fff', zIndex: 1,
      }}>
        <div style={{ fontSize: 40, fontWeight: 'bold', marginBottom: 16, display: 'flex', alignItems: 'center', gap: 12 }}>
          <SafetyCertificateOutlined />
          API-OPS 自愈平台
        </div>
        <div style={{ fontSize: 20, opacity: 0.9, marginBottom: 8 }}>多集群智能运维 · AI 诊断自愈</div>
        <div style={{ fontSize: 14, opacity: 0.7, lineHeight: 1.8 }}>
          统一管理 Kubernetes 集群，AI 智能诊断故障、生成修复方案，
          <br />决策审批后自动执行修复，全链路审计可追溯。
        </div>
      </div>

      {/* 右侧登录卡片（居中，不贴边） */}
      <div style={{
        flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1,
      }}>
        <div style={{
          width: 360, background: '#fff', borderRadius: 12, padding: '36px 32px',
          boxShadow: '0 8px 32px rgba(0,0,0,0.2)',
        }}>
          <div style={{ fontSize: 22, fontWeight: 'bold', textAlign: 'center', marginBottom: 8, color: '#1677ff' }}>
            欢迎登录
          </div>
          <div style={{ textAlign: 'center', color: '#999', fontSize: 13, marginBottom: 24 }}>
            请输入账号信息登录
          </div>
          <Form onFinish={onFinish} size="large">
            <Form.Item name="username" rules={[{ required: true, message: '请输入用户名' }]}>
              <Input prefix={<UserOutlined style={{ color: '#bfbfbf' }} />} placeholder="用户名" />
            </Form.Item>
            <Form.Item name="password" rules={[{ required: true, message: '请输入密码' }]}>
              <Input.Password prefix={<LockOutlined style={{ color: '#bfbfbf' }} />} placeholder="密码" />
            </Form.Item>
            <Form.Item style={{ marginBottom: 12 }}>
              <Checkbox checked={ldapLogin} onChange={(e) => setLdapLogin(e.target.checked)}>
                LDAP 登录
              </Checkbox>
            </Form.Item>
            <Form.Item style={{ marginBottom: 0 }}>
              <Button type="primary" htmlType="submit" block style={{ height: 44, borderRadius: 6 }}>
                登 录
              </Button>
            </Form.Item>
          </Form>
        </div>
      </div>
    </div>
  )
}
