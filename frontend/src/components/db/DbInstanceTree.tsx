// 数据库实例树（左树）：分组 → 实例 → 库 → 表/视图/函数/事件，懒加载。
// 纯展示 + 懒加载：只负责树结构与 schema 懒加载，不做连接/诊断等业务动作。
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { Key, ReactNode } from 'react'
import { Tree, Empty, Input, Button, Tag, Dropdown } from 'antd'
import type { TreeDataNode } from 'antd'
import {
  DatabaseOutlined, TableOutlined, EyeOutlined, FunctionOutlined,
  FieldTimeOutlined, FolderOutlined, ReloadOutlined, SearchOutlined, LoadingOutlined,
} from '@ant-design/icons'
import { getDBSchema } from '../../services/api'

// ---------- 对外契约（父任务按此签名调用，勿改） ----------
export interface DbTreeConn {
  id: number
  name: string
  db_type?: string
  database?: string
  group_name?: string
  status?: string
  source?: string
}
export interface DbInstanceTreeProps {
  conns: DbTreeConn[]
  activeId?: number | null      // 当前打开实例（高亮）
  onOpen: (id: number) => void  // 双击/点击实例 → 打开页签
  keyword?: string              // 过滤实例名/库名
  onRefresh?: (id: number) => void // 右键/行操作「刷新 schema」
  /** 点击库/表/视图/函数/事件节点回调（可选，不传则不影响现有行为） */
  onSelectNode?: (node: DbTreeSelectNode) => void
}

// 树节点点击回调的对外类型：kind 标识节点类型，db/name 为具体对象
export interface DbTreeSelectNode {
  kind: 'db' | 'table' | 'view' | 'routine' | 'event'
  /** 节点所属实例（跨实例点击时父组件据此切换当前实例，避免操作打到别的库上） */
  connId?: number
  db?: string
  name?: string
}

// ---------- schema 形状（与 DbWorkbench.SchemaData 一致） ----------
interface SchemaData {
  databases: string[]
  tables: { db: string; name: string; type?: string }[]
  views: { db: string; name: string }[]
  routines: { db: string; name: string; type: string }[]
  events: { db: string; name: string; extra?: string }[]
}
const EMPTY_SCHEMA: SchemaData = { databases: [], tables: [], views: [], routines: [], events: [] }
const SYS_DBS = new Set(['information_schema', 'performance_schema', 'mysql', 'sys'])

type ObjKind = 'table' | 'view' | 'function' | 'event'
type NodeKind = 'group' | 'instance' | 'database' | 'objgroup' | 'leaf'

interface DbTreeNode extends TreeDataNode {
  kind?: NodeKind
  connId?: number
  dbName?: string
  objKind?: ObjKind
  objType?: string
  children?: DbTreeNode[]
}

// 实例状态点颜色：connected 绿 / failed 红 / 其它（unknown/removed）灰
const STATUS_COLOR: Record<string, string> = {
  connected: '#52c41a',
  failed: '#ff4d4f',
}
const statusColor = (s?: string) => STATUS_COLOR[s || ''] || '#d9d9d9'

const OBJ_ICONS: Record<ObjKind, ReactNode> = {
  table: <TableOutlined style={{ color: '#8c8c8c' }} />,
  view: <EyeOutlined style={{ color: '#8c8c8c' }} />,
  function: <FunctionOutlined style={{ color: '#8c8c8c' }} />,
  event: <FieldTimeOutlined style={{ color: '#8c8c8c' }} />,
}

// 递归把某 key 节点的 children 替换为新值（WebTerminal 同款写法）
function updateTreeData(list: DbTreeNode[], key: Key, children: DbTreeNode[]): DbTreeNode[] {
  return list.map((n) => {
    if (n.key === key) return { ...n, children }
    if (n.children) return { ...n, children: updateTreeData(n.children, key, children) }
    return n
  })
}

// 由 schema 生成某实例下的「库 → 对象分组 → 叶子」整棵子树
function buildDbChildren(id: number, s: SchemaData): DbTreeNode[] {
  return s.databases.map((db) => {
    const groups: { kind: ObjKind; label: string; items: { name: string; type?: string }[] }[] = [
      { kind: 'table', label: '表', items: s.tables.filter((t) => t.db === db) },
      { kind: 'view', label: '视图', items: s.views.filter((v) => v.db === db) },
      { kind: 'function', label: '函数', items: s.routines.filter((r) => r.db === db && (r.type || '').toUpperCase() === 'FUNCTION') },
      { kind: 'event', label: '事件', items: s.events.filter((e) => e.db === db) },
    ]
    return {
      key: `d:${id}:${db}`,
      kind: 'database' as NodeKind,
      connId: id,
      dbName: db,
      title: db,
      isLeaf: false,
      icon: <DatabaseOutlined style={{ color: '#1677ff' }} />,
      children: groups.map((g) => ({
        key: `o:${id}:${db}:${g.kind}`,
        kind: 'objgroup' as NodeKind,
        connId: id,
        dbName: db,
        objKind: g.kind,
        title: `${g.label}（${g.items.length}）`,
        isLeaf: false,
        icon: OBJ_ICONS[g.kind],
        children: g.items.map((it) => ({
          key: `l:${id}:${db}:${g.kind}:${it.name}`,
          kind: 'leaf' as NodeKind,
          connId: id,
          dbName: db,
          objKind: g.kind,
          objType: it.type,
          title: it.name,
          isLeaf: true,
          icon: OBJ_ICONS[g.kind],
        })),
      })),
    }
  })
}

export default function DbInstanceTree(props: DbInstanceTreeProps): JSX.Element {
  const { conns, activeId, onOpen, keyword, onRefresh, onSelectNode } = props

  // 内部搜索 state；外部传了 keyword 则以外部为准
  const [internalKw, setInternalKw] = useState('')
  const kw = keyword !== undefined ? keyword : internalKw

  // 懒加载缓存（按 brief 要求用 useRef Map）+ 进行中请求去重
  const schemaCache = useRef<Map<number, SchemaData>>(new Map())
  const inflight = useRef<Map<number, Promise<void>>>(new Map())
  const [loadingIds, setLoadingIds] = useState<Set<number>>(new Set())

  const connById = useMemo(() => new Map(conns.map((c) => [c.id, c])), [conns])

  // 关键词过滤：实例名 / 库名 / 类型 包含匹配
  const visibleConns = useMemo(() => {
    const k = kw.trim().toLowerCase()
    return k
      ? conns.filter((c) => `${c.name} ${c.database || ''} ${c.db_type || ''}`.toLowerCase().includes(k))
      : conns
  }, [conns, kw])

  // 基础树：分组 → 实例（库/对象子节点懒加载，见 loadInstance）
  const buildBaseTree = useCallback((list: DbTreeConn[]): DbTreeNode[] => {
    const m = new Map<string, DbTreeConn[]>()
    for (const c of list) {
      const g = (c.group_name || '').trim() || '未分组'
      if (!m.has(g)) m.set(g, [])
      m.get(g)!.push(c)
    }
    const entries = [...m.entries()].sort((a, b) => {
      if (a[0] === '未分组') return 1
      if (b[0] === '未分组') return -1
      return a[0].localeCompare(b[0], 'zh')
    })
    return entries.map(([gname, items]) => ({
      key: `g:${gname}`,
      kind: 'group' as NodeKind,
      title: `${gname}（${items.length}）`,
      isLeaf: false,
      icon: <FolderOutlined style={{ color: '#8c8c8c' }} />,
      children: items.map((c) => ({
        key: `i:${c.id}`,
        kind: 'instance' as NodeKind,
        connId: c.id,
        title: c.name,
        isLeaf: false,
      })),
    }))
  }, [])

  const [treeData, setTreeData] = useState<DbTreeNode[]>(() => buildBaseTree(visibleConns))
  const [expandedKeys, setExpandedKeys] = useState<Key[]>([])

  // 实例列表 / 关键词变化 → 重建基础树（已加载的 schema 子节点丢失，但缓存保留，再展开走缓存不重复请求）
  useEffect(() => {
    setTreeData(buildBaseTree(visibleConns))
  }, [visibleConns, buildBaseTree])

  // 懒加载单个实例的 schema：命中缓存直接返回；失败缓存空结构（不再重试，直到刷新）
  const loadInstance = useCallback((id: number): Promise<void> => {
    if (schemaCache.current.has(id)) return Promise.resolve()
    const running = inflight.current.get(id)
    if (running) return running
    const p = (async () => {
      setLoadingIds((s) => new Set(s).add(id))
      try {
        const d = await getDBSchema(id)
        const raw = d as unknown as SchemaData
        const schema: SchemaData = {
          databases: (raw?.databases || []).filter((x) => !SYS_DBS.has(x)),
          tables: raw?.tables || [],
          views: raw?.views || [],
          routines: raw?.routines || [],
          events: raw?.events || [],
        }
        schemaCache.current.set(id, schema)
        setTreeData((prev) => updateTreeData(prev, `i:${id}`, buildDbChildren(id, schema)))
      } catch {
        schemaCache.current.set(id, EMPTY_SCHEMA)
        setTreeData((prev) => updateTreeData(prev, `i:${id}`, []))
      } finally {
        setLoadingIds((s) => { const n = new Set(s); n.delete(id); return n })
        inflight.current.delete(id)
      }
    })()
    inflight.current.set(id, p)
    return p
  }, [])

  // 关键词过滤时自动展开命中实例（visibleConns 已经是被过滤后的命中集）
  useEffect(() => {
    const k = kw.trim()
    if (!k) { setExpandedKeys([]); return }
    const keys: Key[] = visibleConns.map((c) => `i:${c.id}`)
    Promise.all(visibleConns.map((c) => loadInstance(c.id))).then(() => {
      setExpandedKeys((prev) => Array.from(new Set([...prev, ...keys])))
    })
  }, [kw, visibleConns, loadInstance])

  // 展开实例时懒加载 schema（WebTerminal 同款：onExpand 里手动触发，受控 expandedKeys 下更可靠）
  const onExpand = (keys: Key[], info: { node: TreeDataNode; expanded: boolean }) => {
    setExpandedKeys(keys)
    const n = info.node as DbTreeNode
    if (info.expanded && n.kind === 'instance' && n.connId != null) void loadInstance(n.connId)
  }
  const onSelect = (_keys: Key[], info: { node: TreeDataNode }) => {
    const n = info.node as DbTreeNode
    if (n.kind === 'instance' && n.connId != null) { onOpen(n.connId); return }
    if (!onSelectNode) return
    if (n.kind === 'database' && n.dbName) {
      onSelectNode({ kind: 'db', connId: n.connId, db: n.dbName })
      return
    }
    if (n.kind === 'leaf' && n.dbName && n.title) {
      const kindMap: Record<string, 'table' | 'view' | 'routine' | 'event'> = {
        table: 'table', view: 'view', function: 'routine', event: 'event',
      }
      const kind = kindMap[n.objKind || '']
      if (kind) onSelectNode({ kind, connId: n.connId, db: n.dbName, name: String(n.title) })
    }
  }

  // 全局刷新：清缓存并重载当前已展开的实例
  const handleRefresh = () => {
    schemaCache.current.clear()
    inflight.current.clear()
    setLoadingIds(new Set())
    const ids = expandedKeys
      .map((k) => String(k))
      .filter((k) => k.startsWith('i:'))
      .map((k) => Number(k.slice(2)))
    setTreeData(buildBaseTree(visibleConns))
    ids.forEach((id) => { void loadInstance(id) })
  }

  // 单实例刷新 schema（右键菜单）：清该实例缓存 + 回调父层
  const reloadInstance = (id: number) => {
    schemaCache.current.delete(id)
    inflight.current.delete(id)
    setTreeData((prev) => updateTreeData(prev, `i:${id}`, []))
    void loadInstance(id)
    onRefresh?.(id)
  }

  // 动态注入实例图标：加载中转 spinner，否则按状态上色
  const renderTree = useMemo(() => treeData.map((g) => ({
    ...g,
    children: (g.children || []).map((n) => {
      if (n.kind !== 'instance') return n
      const id = n.connId!
      const c = connById.get(id)
      return {
        ...n,
        icon: loadingIds.has(id)
          ? <LoadingOutlined spin style={{ color: '#1677ff' }} />
          : <DatabaseOutlined style={{ color: statusColor(c?.status) }} />,
      }
    }),
  })), [treeData, loadingIds, connById])

  const renderTitle = (node: DbTreeNode): ReactNode => {
    switch (node.kind) {
      case 'instance': {
        const c = connById.get(node.connId!)
        const color = statusColor(c?.status)
        return (
          <Dropdown
            trigger={['contextMenu']}
            menu={{
              items: [
                { key: 'open', label: '打开', onClick: () => onOpen(node.connId!) },
                { key: 'refresh', label: '刷新 schema', onClick: () => reloadInstance(node.connId!) },
              ],
            }}
          >
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, width: '100%', minWidth: 0 }}>
              <span style={{ width: 7, height: 7, borderRadius: 4, flex: '0 0 7px', background: color }} />
              <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{c?.name ?? String(node.title)}</span>
              <span style={{ marginLeft: 'auto', flexShrink: 0, display: 'inline-flex', gap: 2 }}>
                {c?.db_type ? (
                  <Tag bordered={false} style={{ marginInlineEnd: 0, fontSize: 10, lineHeight: '16px' }}>{c.db_type}</Tag>
                ) : null}
              </span>
            </span>
          </Dropdown>
        )
      }
      case 'database':
        return (
          <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={String(node.title)}>
            {String(node.title)}
          </span>
        )
      case 'objgroup':
        return (
          <span style={{ fontWeight: 600, color: '#595959' }}>{String(node.title)}</span>
        )
      case 'leaf': {
        const typeLabel = node.objKind === 'table'
          ? ((node.objType && node.objType.toUpperCase() !== 'BASE TABLE') ? node.objType : '表')
          : null
        return (
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, width: '100%', minWidth: 0 }}>
            <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{String(node.title)}</span>
            {typeLabel ? (
              <span style={{ marginLeft: 'auto', flexShrink: 0 }}>
                <Tag bordered={false} color="geekblue" style={{ marginInlineEnd: 0, fontSize: 10, lineHeight: '16px' }}>{typeLabel}</Tag>
              </span>
            ) : null}
          </span>
        )
      }
      default:
        return <span>{String(node.title)}</span>
    }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0 }}>
      {/* 顶部工具条：搜索 + 刷新（清缓存并重载） */}
      <div style={{ display: 'flex', gap: 6, marginBottom: 8, flexShrink: 0 }}>
        <Input
          size="small"
          allowClear
          prefix={<SearchOutlined style={{ color: '#bfbfbf' }} />}
          placeholder="搜索实例 / 库"
          value={kw}
          onChange={(e) => { if (keyword === undefined) setInternalKw(e.target.value) }}
          style={{ flex: 1 }}
        />
        <Button size="small" icon={<ReloadOutlined />} title="清缓存并重载" onClick={handleRefresh} />
      </div>

      <div style={{ flex: 1, overflow: 'auto', minHeight: 0 }}>
        {visibleConns.length === 0 ? (
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无数据库实例" style={{ marginTop: 40 }} />
        ) : (
          <Tree
            blockNode
            showIcon
            defaultExpandAll={false}
            treeData={renderTree}
            expandedKeys={expandedKeys}
            selectedKeys={activeId != null ? [`i:${activeId}`] : []}
            onExpand={onExpand}
            onSelect={onSelect}
            titleRender={(node) => renderTitle(node as DbTreeNode)}
          />
        )}
      </div>
    </div>
  )
}
