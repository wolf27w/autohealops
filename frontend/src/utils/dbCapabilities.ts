// 直连库能力矩阵（与后端 dialects CAPABILITIES 保持一致，口径单一来源在后端）
// 用途：前端按能力隐藏/禁用入口，避免点了才在后端报 400/500。
export type DbCapability =
  | 'query' | 'exec' | 'schema' | 'ddl' | 'list_databases'
  | 'create_database' | 'drop_database' | 'export' | 'import' | 'backup'

const MYSQL_CAPS: DbCapability[] = [
  'query', 'exec', 'schema', 'ddl', 'list_databases',
  'create_database', 'drop_database', 'export', 'import', 'backup',
]
// 2026-11-19（R18/R16）：Oracle 与 PG 目前只做「查询/执行/结构/DDL/库列表」，
// 导入导出备份建删库一律显式不支持（后端 UnsupportedError）。
const READONLY_CAPS: DbCapability[] = ['query', 'exec', 'schema', 'ddl', 'list_databases']

const MATRIX: Record<string, DbCapability[]> = {
  mysql: MYSQL_CAPS,
  oracle: READONLY_CAPS,
  pg: READONLY_CAPS,
  postgres: READONLY_CAPS,
}

/** 该 db_type 是否支持某项直连能力（空类型按 mysql 处理，与后端一致） */
export function supportsCapability(dbType: string | undefined, cap: DbCapability): boolean {
  const t = (dbType || 'mysql').toLowerCase()
  return (MATRIX[t] || MATRIX.mysql).includes(cap)
}

/** 该库是否支持「平台直连执行 SQL」（agent 库走 agent，不看这个） */
export function canDirectExecute(dbType: string | undefined): boolean {
  return supportsCapability(dbType, 'query')
}

/** PG 用双引号、MySQL 用反引号；Oracle 用双引号 */
export function quoteIdent(dbType: string | undefined, name: string): string {
  const t = (dbType || 'mysql').toLowerCase()
  return t === 'mysql' ? `\`${name}\`` : `"${name}"`
}
