// R75.8 诊断深度与结构化输出：统一四段渲染（结论 / 分析过程 / 建议 / 证据）
// 兼容旧数据：没有新字段（root_cause_chain/analysis/suggestions_detail/evidence_refs）时，
// 回退到 root_cause + suggestions 展示，不白屏。
import { Tag } from 'antd'
import Markdown from './Markdown'

interface Chain { trigger?: string; direct?: string; root?: string }
interface Hypothesis {
  cause?: string
  evidence_for?: string[]
  evidence_against?: string[]
  verdict?: string
}
interface EvidenceGap { missing?: string; how_to_collect?: string }
interface Analysis {
  hypotheses?: Hypothesis[]
  eliminated?: string[]
  evidence_gaps?: EvidenceGap[]
}
interface Detail {
  immediate?: string[]
  root_fix?: string[]
  verification?: string[]
  prevention?: string[]
}

const VERDICT_MAP: Record<string, { text: string; color: string }> = {
  adopted: { text: '采纳', color: 'green' },
  eliminated: { text: '排除', color: 'red' },
  unverified: { text: '待验证', color: 'orange' },
}

const sectionTitle = (t: string) => (
  <div style={{ fontWeight: 600, margin: '10px 0 4px', fontSize: 14, color: '#1f1f1f' }}>{t}</div>
)

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      {sectionTitle(title)}
      {children}
    </div>
  )
}

function asStr(v: unknown): string {
  if (typeof v === 'string') return v
  if (v == null) return ''
  return JSON.stringify(v)
}

function renderChain(chain: Chain | undefined): React.ReactNode {
  if (!chain || typeof chain !== 'object') return null
  const parts: string[] = []
  if ((chain.trigger || '').trim()) parts.push(`触发：${chain.trigger}`)
  if ((chain.direct || '').trim()) parts.push(`直接原因：${chain.direct}`)
  if ((chain.root || '').trim()) parts.push(`根因：${chain.root}`)
  if (!parts.length) return null
  return <div style={{ marginTop: 6, padding: '6px 10px', background: '#f5f8ff', borderRadius: 6, fontSize: 13 }}>
    {parts.join(' → ')}
  </div>
}

function renderAnalysis(analysis: Analysis | undefined): React.ReactNode {
  if (!analysis || typeof analysis !== 'object') return null
  const hyps = Array.isArray(analysis.hypotheses) ? analysis.hypotheses : []
  const eliminated = Array.isArray(analysis.eliminated) ? analysis.eliminated : []
  const gaps = Array.isArray(analysis.evidence_gaps) ? analysis.evidence_gaps : []
  if (!hyps.length && !eliminated.length && !gaps.length) return null
  return (
    <div>
      {hyps.length > 0 && (
        <div style={{ marginTop: 4 }}>
          <div style={{ fontSize: 13, color: '#555', marginBottom: 4 }}>假设对比</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {hyps.map((h, i) => {
              const verdict = VERDICT_MAP[String(h.verdict || 'unverified')] || VERDICT_MAP.unverified
              return (
                <div key={i} style={{ border: '1px solid #e8e8e8', borderRadius: 6, padding: '6px 10px', fontSize: 13 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                    <span>{asStr(h.cause) || '（未命名假设）'}</span>
                    <Tag color={verdict.color} style={{ marginInlineEnd: 0 }}>{verdict.text}</Tag>
                  </div>
                  {(h.evidence_for?.length) ? (
                    <div style={{ color: '#389e0d', marginTop: 2 }}>支持：{h.evidence_for.join('；')}</div>
                  ) : null}
                  {(h.evidence_against?.length) ? (
                    <div style={{ color: '#cf1322', marginTop: 2 }}>反证：{h.evidence_against.join('；')}</div>
                  ) : null}
                </div>
              )
            })}
          </div>
        </div>
      )}
      {eliminated.length > 0 && (
        <div style={{ marginTop: 6 }}>
          <div style={{ fontSize: 13, color: '#555' }}>已排除</div>
          <ul style={{ paddingLeft: 20, margin: '2px 0', fontSize: 13 }}>
            {eliminated.map((e, i) => <li key={i}>{e}</li>)}
          </ul>
        </div>
      )}
      {gaps.length > 0 && (
        <div style={{ marginTop: 6 }}>
          <div style={{ fontSize: 13, color: '#555' }}>证据缺口</div>
          <ul style={{ paddingLeft: 20, margin: '2px 0', fontSize: 13 }}>
            {gaps.map((g, i) => (
              <li key={i}>
                {asStr(g.missing) || '（未说明缺失内容）'}
                {(g.how_to_collect || '').trim() && <span style={{ color: '#1890ff' }}> 补采：{g.how_to_collect}</span>}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}

function renderSuggestions(suggestions: unknown, detail: Detail | undefined): React.ReactNode {
  const d = detail && typeof detail === 'object' ? detail : {}
  const hasDetail = ['immediate', 'root_fix', 'verification', 'prevention'].some(
    (k) => Array.isArray((d as Record<string, unknown>)[k]) && ((d as Record<string, unknown>)[k] as unknown[]).length > 0,
  )
  if (hasDetail) {
    const sections: Array<[string, string]> = [
      ['immediate', '立即处置'],
      ['root_fix', '根因修复'],
      ['verification', '验证方法'],
      ['prevention', '防复发'],
    ]
    return (
      <div>
        {sections.map(([key, label]) => {
          const items = ((d as Record<string, unknown>)[key] as unknown[]) || []
          if (!items.length) return null
          return (
            <div key={key} style={{ marginTop: 6 }}>
              <div style={{ fontSize: 13, color: '#555' }}>{label}</div>
              <ul style={{ paddingLeft: 20, margin: '2px 0', fontSize: 13 }}>
                {items.map((s, i) => <li key={i}>{asStr(s)}</li>)}
              </ul>
            </div>
          )
        })}
      </div>
    )
  }
  // 旧数据：suggestions 字符串数组
  const list = Array.isArray(suggestions) ? suggestions : []
  if (list.length) {
    return (
      <ul style={{ paddingLeft: 20, margin: '2px 0' }}>
        {list.map((s, i) => <li key={i}>{asStr(s)}</li>)}
      </ul>
    )
  }
  if (typeof suggestions === 'string' && suggestions.trim()) {
    return <Markdown text={suggestions} />
  }
  return null
}

export default function DiagnosisSections({ j }: { j: Record<string, unknown> | null | undefined }) {
  const data = j && typeof j === 'object' ? j : {}
  const rootCause = data.root_cause
  const chain = (data.root_cause_chain || {}) as Chain
  const analysis = (data.analysis || {}) as Analysis
  const suggestions = data.suggestions
  const detail = (data.suggestions_detail || {}) as Detail
  const refs = Array.isArray(data.evidence_refs) ? (data.evidence_refs as unknown[]) : []

  const hasNewFields = !!(
    (chain && (chain.trigger || chain.direct || chain.root)) ||
    (analysis && (analysis.hypotheses?.length || analysis.eliminated?.length || analysis.evidence_gaps?.length)) ||
    (detail && ['immediate', 'root_fix', 'verification', 'prevention'].some(
      (k) => Array.isArray((detail as Record<string, unknown>)[k]) && ((detail as Record<string, unknown>)[k] as unknown[]).length > 0,
    )) ||
    refs.length > 0
  )

  const sugContent = renderSuggestions(suggestions, detail)

  // 完全旧数据：只有 root_cause + suggestions
  if (!hasNewFields) {
    return (
      <div>
        <Section title="结论">
          <Markdown text={typeof rootCause === 'string' ? rootCause : asStr(rootCause)} />
        </Section>
        {sugContent && <Section title="建议">{sugContent}</Section>}
      </div>
    )
  }

  return (
    <div>
      <Section title="结论">
        <Markdown text={typeof rootCause === 'string' ? rootCause : asStr(rootCause)} />
        {renderChain(chain)}
      </Section>
      {renderAnalysis(analysis) && <Section title="分析过程">{renderAnalysis(analysis)}</Section>}
      {sugContent && <Section title="建议">{sugContent}</Section>}
      {refs.length > 0 && (
        <Section title="证据">
          <ul style={{ paddingLeft: 20, margin: '2px 0', fontSize: 13 }}>
            {refs.map((r, i) => <li key={i}>{asStr(r)}</li>)}
          </ul>
        </Section>
      )}
    </div>
  )
}
