// 代码块：深色背景 + 复制按钮
import { useState } from 'react'
import { Button } from 'antd'
import { CopyOutlined } from '@ant-design/icons'

export default function CodeBlock({ code }: { code: string }) {
  const [copied, setCopied] = useState(false)
  const handleCopy = async () => {
    await navigator.clipboard.writeText(code)
    setCopied(true)
    setTimeout(() => setCopied(false), 1500)
  }
  return (
    <div style={{ position: 'relative', background: '#1e1e1e', borderRadius: 4, padding: 12 }}>
      <Button
        size="small"
        type="text"
        icon={<CopyOutlined />}
        style={{ position: 'absolute', top: 4, right: 4, color: '#fff' }}
        onClick={handleCopy}
      >
        {copied ? '已复制' : '复制'}
      </Button>
      <pre style={{ color: '#fff', fontFamily: 'monospace', margin: 0, whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>
        {code}
      </pre>
    </div>
  )
}
