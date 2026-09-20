// AI 配置页（合并）：Tab = 模型配置 / 提示词模板 / 运行成本看板
import { Tabs } from 'antd'
import AIModelConfig from './AIModelConfig'
import PromptTemplates from './PromptTemplates'
import AICallLogs from './AICallLogs'

export default function AIConfig() {
  return (
    <Tabs
      defaultActiveKey="models"
      destroyInactiveTabPane
      items={[
        { key: 'models', label: '模型配置', children: <AIModelConfig /> },
        { key: 'prompts', label: '提示词模板', children: <PromptTemplates /> },
        { key: 'logs', label: '运行/成本看板', children: <AICallLogs /> },
      ]}
    />
  )
}
