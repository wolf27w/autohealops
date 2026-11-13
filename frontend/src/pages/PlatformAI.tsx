// AI 配置与知识库（合并页，R34）：模型配置 / 提示词模板 / 运行成本 / 知识库 / 提示词建议
import { Tabs } from 'antd'
import { useSearchParams } from 'react-router-dom'
import AIModelConfig from './AIModelConfig'
import PromptTemplates from './PromptTemplates'
import AICallLogs from './AICallLogs'
import KnowledgeBase from './KnowledgeBase'
import PromptSuggestions from './PromptSuggestions'

export default function PlatformAI() {
  const [sp, setSp] = useSearchParams()
  const tab = sp.get('tab') || 'models'
  return (
    <Tabs
      activeKey={tab}
      onChange={(k) => setSp({ tab: k }, { replace: true })}
      destroyInactiveTabPane
      items={[
        { key: 'models', label: '模型配置', children: <AIModelConfig /> },
        { key: 'prompts', label: '提示词模板', children: <PromptTemplates /> },
        { key: 'logs', label: '运行/成本看板', children: <AICallLogs /> },
        { key: 'knowledge', label: '知识库', children: <KnowledgeBase /> },
        { key: 'suggestions', label: '提示词建议', children: <PromptSuggestions /> },
      ]}
    />
  )
}
