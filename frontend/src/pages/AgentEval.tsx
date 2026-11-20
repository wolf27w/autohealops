// 诊断质量评估页（R42 二期）：标注正确答案 → 评估集 → 回放评分 → 指标
// 左：评估集 + 候选 trace 列表（带标注状态）；右：指标卡片 + 逐条结果表
// 风格照抄 AgentTrace.tsx；接口走 components/eval/api.ts（自包含 fetch，带 Authorization 头）
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Button, Empty, Input, Modal, Radio, Select, Space, Table, Tag, message } from 'antd'
import type { CandidateItem, EvalItem, EvalSet, EvalSummary, Judgment } from '../components/eval/api'
import { createEvalSet, getEvalSets, postAnnotation, runEval } from '../components/eval/api'

const JUDGMENT_META: Record<string, { label: string; color: string }> = {
  correct: { label: '正确', color: 'green' },
  partial: { label: '部分正确', color: 'orange' },
  wrong: { label: '错误', color: 'red' },
}

const RESULT_META: Record<string, { label: string; color: string }> = {
  ok: { label: '已自愈', color: 'green' },
  partial: { label: '部分完成', color: 'orange' },
  failed: { label: '失败', color: 'red' },
  ticket: { label: '转工单', color: 'red' },
}

function fmtTime(iso: string): string {
  if (!iso) return '—'
  return iso.slice(0, 19).replace('T', ' ')
}

export default function AgentEval() {
  // 列表
  const [sets, setSets] = useState<EvalSet[]>([])
  const [candidates, setCandidates] = useState<CandidateItem[]>([])
  const [candTotal, setCandTotal] = useState(0)
  const [listLoading, setListLoading] = useState(false)

  // 筛选
  const [domain, setDomain] = useState('')
  const [faultType, setFaultType] = useState('')
  const [annotatedOnly, setAnnotatedOnly] = useState(false)

  // 选中评估集 + 最近一次结果
  const [selectedSetId, setSelectedSetId] = useState<string | undefined>(undefined)
  const [lastRun, setLastRun] = useState<{ summary: EvalSummary; items: EvalItem[] } | null>(null)
  const [running, setRunning] = useState(false)

  // 标注 Modal
  const [annotateOpen, setAnnotateOpen] = useState(false)
  const [annotateTarget, setAnnotateTarget] = useState<CandidateItem | null>(null)
  const [judgment, setJudgment] = useState<Judgment>('correct')
  const [rootCause, setRootCause] = useState('')
  const [expectedTools, setExpectedTools] = useState<string[]>([])
  const [note, setNote] = useState('')
  const [saving, setSaving] = useState(false)

  // 新建评估集 Modal
  const [setModalOpen, setSetModalOpen] = useState(false)
  const [setName, setSetName] = useState('')
  const [setDesc, setSetDesc] = useState('')

  const load = useCallback(async () => {
    setListLoading(true)
    try {
      const r = await getEvalSets({ domain, fault_type: faultType, annotated_only: annotatedOnly })
      setSets(r.sets || [])
      setCandidates(r.candidates?.items || [])
      setCandTotal(r.candidates?.total || 0)
    } catch (e) {
      message.error((e as Error).message || '加载失败')
    } finally {
      setListLoading(false)
    }
  }, [domain, faultType, annotatedOnly])

  useEffect(() => { load() }, [load])

  const openAnnotate = useCallback((c: CandidateItem) => {
    setAnnotateTarget(c)
    const ann = c.annotation
    setJudgment(ann?.judgment ?? 'correct')
    setRootCause(ann?.correct_root_cause ?? '')
    setExpectedTools(ann?.expected_tools ?? [])
    setNote(ann?.note ?? '')
    setAnnotateOpen(true)
  }, [])

  const saveAnnotation = useCallback(async () => {
    if (!annotateTarget) return
    setSaving(true)
    try {
      await postAnnotation({
        trace_id: annotateTarget.trace_id,
        judgment,
        correct_root_cause: rootCause,
        expected_tools: expectedTools,
        note,
      })
      message.success('标注已保存')
      setAnnotateOpen(false)
      load()
    } catch (e) {
      message.error((e as Error).message || '保存失败')
    } finally {
      setSaving(false)
    }
  }, [annotateTarget, judgment, rootCause, expectedTools, note, load])

  const doCreateSet = useCallback(async () => {
    if (!setName.trim()) {
      message.warning('请输入评估集名称')
      return
    }
    try {
      const annotated = candidates.filter((c) => c.annotated).map((c) => c.trace_id)
      await createEvalSet({ name: setName.trim(), description: setDesc, trace_ids: annotated })
      message.success('评估集已创建（含当前候选里已标注的轨迹）')
      setSetModalOpen(false)
      setSetName('')
      setSetDesc('')
      load()
    } catch (e) {
      message.error((e as Error).message || '创建失败')
    }
  }, [setName, setDesc, candidates, load])

  const doRun = useCallback(async () => {
    setRunning(true)
    try {
      const r = await runEval({ set_id: selectedSetId ?? null, partial_weight: 0.5 })
      setLastRun({ summary: r.summary, items: r.items })
      message.success(`评估完成：${r.summary.scored} 条，准确率 ${r.summary.accuracy_pct}%`)
    } catch (e) {
      message.error((e as Error).message || '运行失败')
    } finally {
      setRunning(false)
    }
  }, [selectedSetId])

  const summary = lastRun?.summary

  const metricCards = useMemo(() => [
    { k: '诊断准确率', v: summary != null ? `${summary.accuracy_pct}%` : '—', sub: summary != null ? `${summary.correct} 正确 / ${summary.partial} 部分 / ${summary.wrong} 错误` : '运行评估后展示', color: '#389e0d' },
    { k: '平均工具步数', v: summary != null ? String(summary.avg_tool_steps) : '—', sub: summary != null ? `平均 ${summary.avg_rounds} 轮` : '运行评估后展示', color: '' },
    { k: '平均耗时', v: summary != null ? summary.avg_duration_text : '—', sub: summary != null ? `${summary.avg_duration_ms} ms` : '运行评估后展示', color: '' },
    { k: '工具命中率', v: summary?.tool_hit_rate_pct != null ? `${summary.tool_hit_rate_pct}%` : '—', sub: summary != null ? `${summary.tool_traces} 条有期望工具` : '运行评估后展示', color: '#1677ff' },
  ], [summary])

  const columns = useMemo(() => [
    { title: '轨迹', dataIndex: 'target', key: 'target', render: (_: unknown, r: EvalItem) => (
      <div>
        <div style={{ fontWeight: 600, fontSize: 13 }}>{r.target || r.trace_id}</div>
        <div style={{ color: '#8c8c8c', fontSize: 11 }}>{r.trace_id}</div>
      </div>
    ) },
    { title: '域', dataIndex: 'domain', key: 'domain', width: 70, render: (d: string) => <Tag color={d === 'app' ? 'blue' : 'purple'}>{d === 'app' ? '应用层' : '数据库'}</Tag> },
    { title: '故障', dataIndex: 'fault_type', key: 'fault_type', width: 150 },
    { title: '人工判定', dataIndex: 'judgment', key: 'judgment', width: 100, render: (j: Judgment | null) => j ? <Tag color={JUDGMENT_META[j]?.color}>{JUDGMENT_META[j]?.label}</Tag> : <Tag>未标注</Tag> },
    { title: '得分', dataIndex: 'score', key: 'score', width: 70, render: (s: number | null) => s == null ? '—' : s.toFixed(2) },
    { title: '工具步数', dataIndex: 'tool_steps', key: 'tool_steps', width: 80 },
    { title: '工具命中', dataIndex: 'tool_hit', key: 'tool_hit', width: 80, render: (h: number | null) => h == null ? '—' : `${(h * 100).toFixed(0)}%` },
    { title: '耗时', dataIndex: 'duration_text', key: 'duration_text', width: 90 },
    { title: '结论', dataIndex: 'result', key: 'result', width: 90, render: (res: string) => <Tag color={RESULT_META[res]?.color}>{RESULT_META[res]?.label ?? res}</Tag> },
  ], [])

  return (
    <div style={{ padding: '0 0 16px' }}>
      {/* 工具栏 */}
      <Space wrap style={{ marginBottom: 12 }}>
        <Select
          allowClear placeholder="域" style={{ width: 110 }} value={domain || undefined}
          onChange={(v) => setDomain(v || '')}
          options={[{ value: 'app', label: '应用层' }, { value: 'db', label: '数据库' }]}
        />
        <Input.Search placeholder="故障类型" style={{ width: 160 }} allowClear onSearch={(v) => setFaultType(v)} />
        <Select
          style={{ width: 130 }} value={annotatedOnly ? '1' : '0'}
          onChange={(v) => setAnnotatedOnly(v === '1')}
          options={[{ value: '0', label: '全部轨迹' }, { value: '1', label: '只看已标注' }]}
        />
        <Button onClick={() => setSetModalOpen(true)}>新建评估集</Button>
        <Select
          allowClear placeholder="评估集（空=全部已标注）" style={{ width: 240 }}
          value={selectedSetId}
          onChange={(v) => setSelectedSetId(v)}
          options={sets.map((s) => ({ value: s.id, label: `${s.name}（${s.count}）` }))}
        />
        <Button type="primary" loading={running} onClick={doRun}>运行评估</Button>
      </Space>

      <div style={{ display: 'grid', gridTemplateColumns: '430px 1fr', gap: 16, alignItems: 'start' }}>
        {/* 左：评估集 + 候选列表 */}
        <div>
          <div style={{ background: '#fff', border: '1px solid #f0f0f0', borderRadius: 8, overflow: 'hidden', marginBottom: 12 }}>
            <div style={{ padding: '12px 14px', borderBottom: '1px solid #f0f0f0', fontWeight: 600, fontSize: 13 }}>
              评估集 <span style={{ color: '#8c8c8c', fontWeight: 400, fontSize: 12 }}>共 {sets.length} 个</span>
            </div>
            {sets.length === 0 && <Empty style={{ padding: '20px 0' }} image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无评估集" />}
            {sets.map((s) => (
              <div
                key={s.id}
                onClick={() => setSelectedSetId(s.id)}
                style={{
                  padding: '10px 14px', borderBottom: '1px solid #f5f5f5', cursor: 'pointer',
                  background: selectedSetId === s.id ? '#f0f6ff' : '#fff',
                  boxShadow: selectedSetId === s.id ? 'inset 3px 0 0 #1677ff' : 'none',
                }}
              >
                <div style={{ fontWeight: 600, fontSize: 13 }}>{s.name}</div>
                <div style={{ color: '#8c8c8c', fontSize: 12, marginTop: 2 }}>
                  {s.count} 条轨迹 · {s.created_by || '—'} · {fmtTime(s.created_at).slice(0, 10)}
                </div>
              </div>
            ))}
          </div>

          <div style={{ background: '#fff', border: '1px solid #f0f0f0', borderRadius: 8, overflow: 'hidden' }}>
            <div style={{ padding: '12px 14px', borderBottom: '1px solid #f0f0f0', fontWeight: 600, fontSize: 13 }}>
              候选轨迹 <span style={{ color: '#8c8c8c', fontWeight: 400, fontSize: 12 }}>共 {candTotal} 条</span>
            </div>
            <div style={{ maxHeight: 'calc(100vh - 480px)', overflow: 'auto' }}>
              {candidates.length === 0 && <Empty style={{ padding: '40px 0' }} description={listLoading ? '加载中…' : '暂无轨迹'} />}
              {candidates.map((c) => (
                <div key={c.trace_id} style={{ padding: '11px 14px', borderBottom: '1px solid #f5f5f5' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <span style={{ fontWeight: 600, fontSize: 13, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                      {c.resource_name || c.target}
                    </span>
                    <span style={{ marginLeft: 'auto', color: '#8c8c8c', fontSize: 11, flexShrink: 0 }}>{fmtTime(c.started_at).slice(11, 19)}</span>
                  </div>
                  <div style={{ color: '#8c8c8c', fontSize: 12, marginTop: 2, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                    {c.cluster_name}{c.namespace ? ` / ${c.namespace}` : ''} · {c.fault_type}
                  </div>
                  <div style={{ display: 'flex', gap: 6, marginTop: 6, flexWrap: 'wrap', alignItems: 'center' }}>
                    <Tag color={c.domain === 'app' ? 'blue' : 'purple'} style={{ marginInlineEnd: 0 }}>{c.domain === 'app' ? '应用层' : '数据库'}</Tag>
                    <Tag color={RESULT_META[c.result]?.color} style={{ marginInlineEnd: 0 }}>{RESULT_META[c.result]?.label}</Tag>
                    {c.annotated && c.annotation
                      ? <Tag color={JUDGMENT_META[c.annotation.judgment]?.color} style={{ marginInlineEnd: 0 }}>{JUDGMENT_META[c.annotation.judgment]?.label}</Tag>
                      : <Tag style={{ marginInlineEnd: 0 }}>未标注</Tag>}
                    <Button size="small" type="link" style={{ marginLeft: 'auto' }} onClick={() => openAnnotate(c)}>标注</Button>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* 右：指标卡片 + 逐条结果表 */}
        <div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12, marginBottom: 12 }}>
            {metricCards.map((m) => (
              <div key={m.k} style={{ background: '#fff', border: '1px solid #f0f0f0', borderRadius: 8, padding: '12px 14px' }}>
                <div style={{ color: '#8c8c8c', fontSize: 12 }}>{m.k}</div>
                <div style={{ fontSize: 22, fontWeight: 600, color: m.color || '#1f2328' }}>{m.v}</div>
                <div style={{ color: '#8c8c8c', fontSize: 11, marginTop: 2 }}>{m.sub}</div>
              </div>
            ))}
          </div>

          <div style={{ background: '#fff', border: '1px solid #f0f0f0', borderRadius: 8, overflow: 'hidden' }}>
            <div style={{ padding: '12px 14px', borderBottom: '1px solid #f0f0f0', fontWeight: 600, fontSize: 13 }}>
              逐条结果 <span style={{ color: '#8c8c8c', fontWeight: 400, fontSize: 12 }}>
                {summary ? `${summary.scored} / ${summary.total} 条已标注` : '运行评估后展示'}
              </span>
            </div>
            <Table
              rowKey="trace_id"
              columns={columns}
              dataSource={lastRun?.items || []}
              size="small"
              pagination={false}
              locale={{ emptyText: <Empty description="暂无结果，运行评估后展示" image={Empty.PRESENTED_IMAGE_SIMPLE} /> }}
            />
          </div>
        </div>
      </div>

      {/* 标注 Modal */}
      <Modal
        title={`标注 · ${annotateTarget?.target || annotateTarget?.trace_id || ''}`}
        open={annotateOpen}
        onOk={saveAnnotation}
        confirmLoading={saving}
        onCancel={() => setAnnotateOpen(false)}
        okText="保存标注"
      >
        <div style={{ display: 'grid', gap: 12 }}>
          <div>
            <div style={{ marginBottom: 4, color: '#3d4451' }}>人工判定</div>
            <Radio.Group value={judgment} onChange={(e) => setJudgment(e.target.value)}>
              <Radio.Button value="correct">正确</Radio.Button>
              <Radio.Button value="partial">部分正确</Radio.Button>
              <Radio.Button value="wrong">错误</Radio.Button>
            </Radio.Group>
          </div>
          <div>
            <div style={{ marginBottom: 4, color: '#3d4451' }}>正确根因</div>
            <Input.TextArea rows={3} value={rootCause} onChange={(e) => setRootCause(e.target.value)} placeholder="标注正确根因（用于后续对比）" />
          </div>
          <div>
            <div style={{ marginBottom: 4, color: '#3d4451' }}>期望工具（可选，用于工具命中率）</div>
            <Select
              mode="tags" style={{ width: '100%' }} value={expectedTools}
              onChange={(v) => setExpectedTools(v as string[])}
              placeholder="输入期望调用的工具名后回车"
              open={false}
              tokenSeparators={[',', '，']}
            />
          </div>
          <div>
            <div style={{ marginBottom: 4, color: '#3d4451' }}>备注</div>
            <Input.TextArea rows={2} value={note} onChange={(e) => setNote(e.target.value)} placeholder="可选" />
          </div>
        </div>
      </Modal>

      {/* 新建评估集 Modal */}
      <Modal
        title="新建评估集"
        open={setModalOpen}
        onOk={doCreateSet}
        onCancel={() => setSetModalOpen(false)}
        okText="创建"
      >
        <div style={{ display: 'grid', gap: 12 }}>
          <Input value={setName} onChange={(e) => setSetName(e.target.value)} placeholder="评估集名称（必填）" />
          <Input.TextArea rows={2} value={setDesc} onChange={(e) => setSetDesc(e.target.value)} placeholder="描述（可选）" />
          <div style={{ color: '#8c8c8c', fontSize: 12 }}>
            创建时会把「当前候选列表里已标注」的轨迹纳入评估集；也可运行前直接选评估集。
          </div>
        </div>
      </Modal>
    </div>
  )
}
