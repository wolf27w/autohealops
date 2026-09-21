// 轻量 Markdown 渲染（无第三方依赖；支持标题/粗体/行内代码/代码块/有序无序列表/表格/链接/分隔线）
import type { ReactNode } from 'react'

const CODE_INLINE = {
  background: '#f6f8fa', padding: '1px 5px', borderRadius: 4,
  fontFamily: 'Menlo, Consolas, monospace', fontSize: 12,
} as const

function renderInline(text: string, kp: string): ReactNode[] {
  const nodes: ReactNode[] = []
  const re = /(`[^`]+`)|(\*\*[^*]+\*\*)|(\[[^\]]+\]\([^)]+\))/
  const gre = new RegExp(re.source, 'g')
  let last = 0
  let idx = 0
  let m: RegExpExecArray | null
  while ((m = gre.exec(text)) !== null) {
    if (m.index > last) nodes.push(text.slice(last, m.index))
    const tok = m[0]
    if (tok.startsWith('`')) nodes.push(<code key={`${kp}-c${idx}`} style={CODE_INLINE}>{tok.slice(1, -1)}</code>)
    else if (tok.startsWith('**')) nodes.push(<strong key={`${kp}-b${idx}`}>{tok.slice(2, -2)}</strong>)
    else {
      const lm = /\[([^\]]+)\]\(([^)]+)\)/.exec(tok)
      nodes.push(<a key={`${kp}-a${idx}`} href={lm ? lm[2] : '#'} target="_blank" rel="noreferrer">{lm ? lm[1] : tok}</a>)
    }
    last = m.index + tok.length
    idx++
  }
  if (last < text.length) nodes.push(text.slice(last))
  return nodes
}

export default function Markdown({ text }: { text?: string }) {
  const lines = (text || '').replace(/\r\n/g, '\n').split('\n')
  const blocks: ReactNode[] = []
  let i = 0
  let k = 0
  while (i < lines.length) {
    const line = lines[i]
    if (/^```/.test(line)) {
      const buf: string[] = []
      i++
      while (i < lines.length && !/^```/.test(lines[i])) { buf.push(lines[i]); i++ }
      i++
      blocks.push(<pre key={`k${k++}`} style={{ background: '#f6f8fa', padding: 10, borderRadius: 6, overflowX: 'auto', whiteSpace: 'pre-wrap', margin: '8px 0' }}><code>{buf.join('\n')}</code></pre>)
      continue
    }
    if (/^\s*$/.test(line)) { i++; continue }
    if (/^#{1,6}\s+/.test(line)) {
      const lvl = (line.match(/^#+/) as RegExpMatchArray)[0].length
      const content = line.replace(/^#+\s+/, '')
      const kp = `k${k++}`
      const style = { margin: '10px 0 6px' }
      if (lvl === 1) blocks.push(<h2 key={kp} style={style}>{renderInline(content, kp)}</h2>)
      else if (lvl === 2) blocks.push(<h3 key={kp} style={style}>{renderInline(content, kp)}</h3>)
      else blocks.push(<h4 key={kp} style={style}>{renderInline(content, kp)}</h4>)
      i++
      continue
    }
    if (/^\s*([-*])\s+/.test(line)) {
      const items: string[] = []
      while (i < lines.length && /^\s*([-*])\s+/.test(lines[i])) { items.push(lines[i].replace(/^\s*[-*]\s+/, '')); i++ }
      const kp = `k${k++}`
      blocks.push(<ul key={kp} style={{ margin: '4px 0', paddingLeft: 20 }}>{items.map((t, x) => <li key={x}>{renderInline(t, `${kp}-${x}`)}</li>)}</ul>)
      continue
    }
    if (/^\s*\d+[.)]\s+/.test(line)) {
      const items: string[] = []
      while (i < lines.length && /^\s*\d+[.)]\s+/.test(lines[i])) { items.push(lines[i].replace(/^\s*\d+[.)]\s+/, '')); i++ }
      const kp = `k${k++}`
      blocks.push(<ol key={kp} style={{ margin: '4px 0', paddingLeft: 20 }}>{items.map((t, x) => <li key={x}>{renderInline(t, `${kp}-${x}`)}</li>)}</ol>)
      continue
    }
    if (/^\s*\|.*\|\s*$/.test(line) && i + 1 < lines.length && /^\s*\|[\s:|-]+\|\s*$/.test(lines[i + 1])) {
      const cells = (l: string) => l.trim().replace(/^\||\|$/g, '').split('|').map((c) => c.trim())
      const head = cells(line)
      i += 2
      const rows: string[][] = []
      while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) { rows.push(cells(lines[i])); i++ }
      const kp = `k${k++}`
      blocks.push(
        <table key={kp} style={{ borderCollapse: 'collapse', margin: '8px 0', width: '100%', fontSize: 12 }}>
          <thead><tr>{head.map((c, x) => <th key={x} style={{ border: '1px solid #e8e8e8', padding: '4px 8px', background: '#fafafa', textAlign: 'left' }}>{renderInline(c, `${kp}-h${x}`)}</th>)}</tr></thead>
          <tbody>{rows.map((r, y) => <tr key={y}>{r.map((c, x) => <td key={x} style={{ border: '1px solid #e8e8e8', padding: '4px 8px' }}>{renderInline(c, `${kp}-${y}-${x}`)}</td>)}</tr>)}</tbody>
        </table>)
      continue
    }
    if (/^\s*(-{3,}|\*{3,})\s*$/.test(line)) {
      blocks.push(<hr key={`k${k++}`} style={{ border: 'none', borderTop: '1px solid #eee', margin: '10px 0' }} />)
      i++
      continue
    }
    const buf = [line]
    i++
    while (i < lines.length && !/^\s*$/.test(lines[i]) && !/^(#{1,6}\s|```|\s*[-*]\s|\s*\d+[.)]\s|\s*\|)/.test(lines[i])) {
      buf.push(lines[i])
      i++
    }
    const kp = `k${k++}`
    blocks.push(<p key={kp} style={{ margin: '6px 0' }}>{renderInline(buf.join(' '), kp)}</p>)
  }
  return <div style={{ fontSize: 13, lineHeight: 1.7, wordBreak: 'break-word' }}>{blocks}</div>
}
