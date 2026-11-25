// Agent 运行轨迹页：诊断/自愈调用链时间轴（只读展示，数据复用现有表）
// 结构：顶部统计 + 筛选 + 左列表（轨迹索引）+ 右详情（时间轴 + 证据包）
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Button, Drawer, Empty, Input, Pagination, Select, Space, Tag } from 'antd'
import type { TraceDetail, TraceItem, TraceStats, TraceStep } from '../components/trace/api'
import { getAgentTraceDetail, getAgentTraces, getAgentTraceStats } from '../components/trace/api'

// ============ 类型映射（颜色与 demo 一致） ============
const KIND_META: Record<string, { label: string; color: string; bg: string }> = {
  trigger: { label: '告警触发', color: '#8c8c8c', bg: '#f5f5f5' },
  think: { label: '推理', color: '#bfbfbf', bg: '#fff' },
  tool: { label: '工具调用', color: '#13c2c2', bg: '#13c2c2' },
  decide: { label: '决策', color: '#722ed1', bg: '#722ed1' },
  exec: { label: '执行', color: '#1677ff', bg: '#1677ff' },
  approve: { label: '审批', color: '#eb2f96', bg: '#eb2f96' },
  verify: { label: '验证', color: '#52c41a', bg: '#52c41a' },
  err: { label: '失败/转单', color: '#ff4d4f', bg: '#ff4d4f' },
}

const RESULT_META: Record<string, { label: string; color: string }> = {
  ok: { label: '已自愈', color: 'green' },
  partial: { label: '部分完成', color: 'orange' },
  failed: { label: '失败', color: 'red' },
  ticket: { label: '转工单', color: 'red' },
}

const ACTION_META: Record<string, { label: string; color: string }> = {
  auto: { label: '自动执行', color: 'cyan' },
  approval: { label: '审批后执行', color: 'magenta' },
  plan_only: { label: '仅给方案', color: 'default' },
}

const SEVERITY_COLOR: Record<string, string> = {
  critical: 'red', high: 'orange', medium: 'gold', low: 'blue',
}

// ============ 工具函数 ============
function fmtTime(iso: string): string {
  if (!iso) return '—'
  return iso.slice(0, 19).replace('T', ' ')
}

function stepDepth(steps: TraceStep[], key: string): number {
  let d = 0
  let cur = steps.find((s) => s.key === key)
  while (cur && cur.parent_key) {
    d += 1
    cur = steps.find((s) => s.key === cur?.parent_key)
  }
  return d
}

export default function AgentTrace() {
  const [stats, setStats] = useState<TraceStats | null>(null)
  const [items, setItems] = useState<TraceItem[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const pageSize = 20
  const [loading, setLoading] = useState(false)

  // 筛选
  const [domain, setDomain] = useState('')
  const [result, setResult] = useState('')
  const [hasApproval, setHasApproval] = useState<string>('')
  const [hasTicket, setHasTicket] = useState<string>('')
  const [search, setSearch] = useState('')

  const [current, setCurrent] = useState<string>('')
  const [detail, setDetail] = useState<TraceDetail | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [evidenceOpen, setEvidenceOpen] = useState(false)

  const load = useCallback(async (p: number, filters: Record<string, unknown>) => {
    setLoading(true)
    try {
      const r = await getAgentTraces({
        page: p, page_size: pageSize,
        domain: filters.domain, result: filters.result,
        has_approval: filters.hasApproval === '' ? undefined : filters.hasApproval === '1',
        has_ticket: filters.hasTicket === '' ? undefined : filters.hasTicket === '1',
        search: filters.search,
      })
      setItems(r.items || [])
      setTotal(r.total || 0)
    } finally {
      setLoading(false)
    }
  }, [])

  const filters = useMemo(() => ({ domain, result, hasApproval, hasTicket, search }), [domain, result, hasApproval, hasTicket, search])

  useEffect(() => {
    getAgentTraceStats().then(setStats).catch(() => setStats(null))
  }, [])

  useEffect(() => {
    load(page, filters)
  }, [page, filters, load])

  const openTrace = useCallback(async (traceId: string) => {
    setCurrent(traceId)
    setDetailLoading(true)
    try {
      const d = await getAgentTraceDetail(traceId)
      setDetail(d)
    } catch {
      setDetail(null)
    } finally {
      setDetailLoading(false)
    }
  }, [])

  const depthMap = useMemo(() => {
    if (!detail) return {}
    const m: Record<string, number> = {}
    detail.steps.forEach((s) => { m[s.key] = stepDepth(detail.steps, s.key) })
    return m
  }, [detail])

  return (
    <div style={{ padding: '0 0 16px' }}>
      {/* 顶部统计 */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', gap: 12, marginBottom: 16 }}>
        {[
          { k: '今日诊断', v: stats ? String(stats.today_total) : '—', suffix: '次', color: '' },
          { k: '自动闭环', v: stats ? String(stats.auto_closed) : '—', suffix: `次 · ${stats?.auto_ratio ?? '—'}%`, color: '#389e0d' },
          { k: '走审批', v: stats ? String(stats.approved) : '—', suffix: '单', color: '#c41d7f' },
          { k: '平均轮数 / 耗时', v: stats ? String(stats.avg_rounds) : '—', suffix: `轮 · ${stats?.avg_duration_text ?? '—'}`, color: '' },
          { k: '转人工工单', v: stats ? String(stats.ticketed) : '—', suffix: '单', color: '#d46b08' },
        ].map((s) => (
          <div key={s.k} style={{ background: '#fff', border: '1px solid #f0f0f0', borderRadius: 8, padding: '12px 14px' }}>
            <div style={{ color: '#8c8c8c', fontSize: 12 }}>{s.k}</div>
            <div style={{ fontSize: 22, fontWeight: 600, color: s.color || '#1f2328' }}>
              {s.v}<small style={{ fontSize: 12, fontWeight: 400, color: '#8c8c8c', marginLeft: 4 }}>{s.suffix}</small>
            </div>
          </div>
        ))}
      </div>

      {/* 筛选 */}
      <Space wrap style={{ marginBottom: 16 }}>
        <Select
          allowClear placeholder="域" style={{ width: 110 }} value={domain || undefined}
          onChange={(v) => { setDomain(v || ''); setPage(1) }}
          options={[{ value: 'app', label: '应用层' }, { value: 'db', label: '数据库' }]}
        />
        <Select
          allowClear placeholder="结论" style={{ width: 110 }} value={result || undefined}
          onChange={(v) => { setResult(v || ''); setPage(1) }}
          options={[
            { value: 'ok', label: '已自愈' }, { value: 'partial', label: '部分完成' },
            { value: 'failed', label: '失败' }, { value: 'ticket', label: '转工单' },
          ]}
        />
        <Select
          allowClear placeholder="是否走审批" style={{ width: 130 }} value={hasApproval || undefined}
          onChange={(v) => { setHasApproval(v || ''); setPage(1) }}
          options={[{ value: '1', label: '走审批' }, { value: '0', label: '未走审批' }]}
        />
        <Select
          allowClear placeholder="是否转工单" style={{ width: 130 }} value={hasTicket || undefined}
          onChange={(v) => { setHasTicket(v || ''); setPage(1) }}
          options={[{ value: '1', label: '转工单' }, { value: '0', label: '未转工单' }]}
        />
        <Input.Search
          placeholder="目标 / 故障 / 结论" style={{ width: 220 }} allowClear
          onSearch={(v) => { setSearch(v); setPage(1) }}
        />
      </Space>

      {/* 列表 + 详情 */}
      <div style={{ display: 'grid', gridTemplateColumns: '430px 1fr', gap: 16, alignItems: 'start' }}>
        <div style={{ background: '#fff', border: '1px solid #f0f0f0', borderRadius: 8, overflow: 'hidden' }}>
          <div style={{ padding: '12px 14px', borderBottom: '1px solid #f0f0f0', fontWeight: 600, fontSize: 13 }}>
            轨迹列表 <span style={{ color: '#8c8c8c', fontWeight: 400, fontSize: 12 }}>共 {total} 条</span>
          </div>
          <div style={{ maxHeight: 'calc(100vh - 340px)', overflow: 'auto' }}>
            {items.length === 0 && <Empty style={{ padding: '40px 0' }} description={loading ? '加载中…' : '暂无轨迹'} />}
            {items.map((t) => (
              <div
                key={t.trace_id}
                onClick={() => openTrace(t.trace_id)}
                style={{
                  padding: '11px 14px', borderBottom: '1px solid #f5f5f5', cursor: 'pointer',
                  background: current === t.trace_id ? '#f0f6ff' : '#fff',
                  boxShadow: current === t.trace_id ? 'inset 3px 0 0 #1677ff' : 'none',
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <span style={{ fontWeight: 600, fontSize: 13, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                    {t.resource_name || t.target}
                  </span>
                  <span style={{ marginLeft: 'auto', color: '#8c8c8c', fontSize: 11, flexShrink: 0 }}>{fmtTime(t.started_at).slice(11, 19)}</span>
                </div>
                <div style={{ color: '#8c8c8c', fontSize: 12, marginTop: 2, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                  {t.cluster_name}{t.namespace ? ` / ${t.namespace}` : ''} · {t.trigger}
                </div>
                <div style={{ display: 'flex', gap: 6, marginTop: 6, flexWrap: 'wrap' }}>
                  <Tag color={t.domain === 'app' ? 'blue' : 'purple'} style={{ marginInlineEnd: 0 }}>
                    {t.domain === 'app' ? '应用层' : '数据库'}
                  </Tag>
                  <Tag color={RESULT_META[t.result]?.color} style={{ marginInlineEnd: 0 }}>{RESULT_META[t.result]?.label}</Tag>
                  <Tag style={{ marginInlineEnd: 0 }}>{t.rounds} 轮 · {t.duration_text}</Tag>
                </div>
              </div>
            ))}
          </div>
          <div style={{ padding: '10px 14px', borderTop: '1px solid #f0f0f0' }}>
            <Pagination
              size="small" current={page} pageSize={pageSize} total={total} showSizeChanger={false}
              onChange={(p) => setPage(p)}
            />
          </div>
        </div>

        {/* 详情 */}
        <div style={{ background: '#fff', border: '1px solid #f0f0f0', borderRadius: 8, minHeight: 640 }}>
          {!detail && (
            <Empty style={{ paddingTop: 200 }} description={detailLoading ? '加载中…' : '点击左侧轨迹查看调用链'} />
          )}
          {detail && (
            <>
              <div style={{ padding: '14px 16px', borderBottom: '1px solid #f0f0f0' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                  <h2 style={{ fontSize: 16, margin: 0, fontWeight: 600 }}>{detail.target}</h2>
                  <Tag color={detail.domain === 'app' ? 'blue' : 'purple'}>{detail.domain === 'app' ? '应用层' : '数据库'}</Tag>
                  <Tag color={RESULT_META[detail.result]?.color}>{RESULT_META[detail.result]?.label}</Tag>
                  <Tag color={ACTION_META[detail.action]?.color}>{ACTION_META[detail.action]?.label}</Tag>
                  {detail.severity && <Tag color={SEVERITY_COLOR[detail.severity] || 'default'}>{detail.severity}</Tag>}
                  <div style={{ marginLeft: 'auto', display: 'flex', gap: 8 }}>
                    <Button size="small" onClick={() => setEvidenceOpen(true)}>证据包</Button>
                  </div>
                </div>
                <div style={{ color: '#8c8c8c', fontSize: 12, marginTop: 6, display: 'flex', gap: 14, flexWrap: 'wrap' }}>
                  <span>触发：<b style={{ color: '#3d4451' }}>{detail.trigger}</b></span>
                  <span>开始：<b style={{ color: '#3d4451' }}>{fmtTime(detail.started_at)}</b></span>
                  <span>耗时：<b style={{ color: '#3d4451' }}>{detail.duration_text}</b></span>
                  <span>轮数：<b style={{ color: '#3d4451' }}>{detail.rounds}</b></span>
                  <span>动作：<b style={{ color: '#3d4451' }}>{ACTION_META[detail.action]?.label}</b></span>
                </div>
              </div>

              {/* 三行摘要 */}
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', borderBottom: '1px solid #f0f0f0' }}>
                {(['判定', '动作', '结论'] as const).map((k, i) => (
                  <div key={k} style={{ padding: '12px 16px', borderRight: i < 2 ? '1px solid #f0f0f0' : 'none' }}>
                    <div style={{ color: '#8c8c8c', fontSize: 11, marginBottom: 2 }}>{k}</div>
                    <div style={{ fontWeight: 500 }}>{detail.summary[k] || '—'}</div>
                  </div>
                ))}
              </div>

              {/* 时间轴 */}
              <div style={{ padding: '16px 16px 8px' }}>
                {detail.steps.map((s, idx) => {
                  const meta = KIND_META[s.kind] || KIND_META.think
                  const depth = depthMap[s.key] || 0
                  const isLast = idx === detail.steps.length - 1
                  return (
                    <div key={s.key} style={{ position: 'relative', padding: `0 0 18px ${30 + depth * 20}px` }}>
                      {!isLast && <div style={{ position: 'absolute', left: 9 + depth * 20, top: 20, bottom: 0, width: 2, background: '#eef0f3' }} />}
                      <div style={{
                        position: 'absolute', left: 2 + depth * 20, top: 4, width: 16, height: 16, borderRadius: '50%',
                        background: meta.bg, border: `2px solid ${meta.color}`, color: '#fff', fontSize: 9,
                        display: 'flex', alignItems: 'center', justifyContent: 'center',
                      }}>
                        {s.kind === 'think' || s.kind === 'trigger' ? '·' : ''}
                      </div>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                        <span style={{ color: '#a8aeb8', fontSize: 11 }}>{(s.at_ms / 1000).toFixed(1)}s</span>
                        <span style={{ fontWeight: 600, fontSize: 13 }}>{s.title}</span>
                        <span style={{ color: '#8c8c8c', fontSize: 11, marginLeft: 'auto' }}>{s.duration_text}</span>
                      </div>
                      {s.tool_name && (
                        <div style={{ marginTop: 4, fontSize: 12, color: '#5b6472' }}>
                          <code style={{ background: '#f7f8fa', padding: '1px 6px', borderRadius: 4, fontSize: 12 }}>{s.tool_name}</code>
                        </div>
                      )}
                      {s.args != null && (
                        <div style={{ marginTop: 4, fontSize: 12, color: '#5b6472', wordBreak: 'break-all' }}>
                          入参：
                          <code style={{ background: '#f7f8fa', padding: '1px 6px', borderRadius: 4, fontSize: 11 }}>
                            {(typeof s.args === 'string' ? s.args : JSON.stringify(s.args)).slice(0, 300)}
                          </code>
                        </div>
                      )}
                      {s.result_summary && (
                        <div style={{
                          marginTop: 6, color: '#3d4451', fontFamily: 'SFMono-Regular, Menlo, Consolas, monospace', fontSize: 12,
                          background: '#f7f8fa', border: '1px solid #eef0f3', borderRadius: 6, padding: '8px 10px',
                          whiteSpace: 'pre-wrap', wordBreak: 'break-all',
                        }}>{s.result_summary}</div>
                      )}
                      {s.status === 'error' && s.error && <div style={{ marginTop: 6, color: '#cf1322', fontSize: 12 }}>{s.error}</div>}
                      {s.evidence_refs.length > 0 && (
                        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 8 }}>
                          {s.evidence_refs.map((e) => (
                            <span key={e} style={{ border: '1px dashed #d9dde3', borderRadius: 6, padding: '4px 10px', fontSize: 12, color: '#3d4451' }}>{e}</span>
                          ))}
                        </div>
                      )}
                    </div>
                  )
                })}
              </div>

              {/* 底部 */}
              <div style={{ padding: '12px 16px', borderTop: '1px solid #f0f0f0', display: 'flex', alignItems: 'center', gap: 10, color: '#8c8c8c', fontSize: 12 }}>
                <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
                  {Object.entries(KIND_META).map(([k, m]) => (
                    <span key={k} style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}>
                      <i style={{ width: 9, height: 9, borderRadius: '50%', background: m.bg, border: `1px solid ${m.color}`, display: 'inline-block' }} />
                      {m.label}
                    </span>
                  ))}
                </div>
                <span style={{ marginLeft: 'auto' }}>trace_id: <code style={{ background: '#f7f8fa', padding: '1px 6px', borderRadius: 4 }}>{detail.trace_id}</code></span>
              </div>
            </>
          )}
        </div>
      </div>

      {/* 证据包抽屉 */}
      <Drawer title="证据包" open={evidenceOpen} onClose={() => setEvidenceOpen(false)} width={720}>
        {detail?.evidence?.length === 0 && <Empty description="暂无证据" />}
        {detail?.evidence?.map((e, i) => (
          <div key={i} style={{ marginBottom: 16 }}>
            <div style={{ fontWeight: 600, marginBottom: 6 }}>{e.label}</div>
            <pre style={{ background: '#f7f8fa', border: '1px solid #eef0f3', borderRadius: 6, padding: 10, fontSize: 12, whiteSpace: 'pre-wrap', wordBreak: 'break-all', margin: 0 }}>{e.content || '—'}</pre>
          </div>
        ))}
      </Drawer>
    </div>
  )
}
