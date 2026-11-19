// 日志下载文件名生成（R56，纯函数）
// 时间语义：按本地时区（Asia/Shanghai）格式化为 yyyyMMdd-HHmmss，
// 分隔符用 `_` / `-`，不含中文、空格、冒号，避免部分浏览器/系统下载失败。
import type { Dayjs } from 'dayjs'

export interface LogFilenameInput {
  namespace: string
  name: string
  /** 容器名：单容器填该容器名；多容器合并成一个文件时填 'multi' */
  container?: string
  /** 未选区间（tail 下载）时的行数，如 DOWNLOAD_TAIL */
  tail?: number
  /** 选择了开始+结束时间时传 [起, 止]（Date 或 Dayjs） */
  range?: [Date | Dayjs, Date | Dayjs]
  /** 是否带关键字筛选（追加 _filtered） */
  filtered?: boolean
  /** 下载完成时刻（tail 场景结尾用），默认取当前时间 */
  now?: Date
}

const pad2 = (n: number): string => (n < 10 ? `0${n}` : String(n))

function isDayjs(v: Date | Dayjs): v is Dayjs {
  return typeof (v as { year?: unknown }).year === 'function'
}

/** 本地时区格式化为 yyyyMMdd-HHmmss */
export function formatLogTimestamp(v: Date | Dayjs): string {
  if (isDayjs(v)) {
    return `${v.year()}${pad2(v.month() + 1)}${pad2(v.date())}-${pad2(v.hour())}${pad2(v.minute())}${pad2(v.second())}`
  }
  return `${v.getFullYear()}${pad2(v.getMonth() + 1)}${pad2(v.getDate())}-${pad2(v.getHours())}${pad2(v.getMinutes())}${pad2(v.getSeconds())}`
}

export function buildLogFilename(input: LogFilenameInput): string {
  const { namespace, name, container, tail, range, filtered, now } = input
  const parts: string[] = [namespace, name]
  if (container) parts.push(container)
  if (range && range[0] && range[1]) {
    parts.push(formatLogTimestamp(range[0]))
    parts.push(formatLogTimestamp(range[1]))
  } else {
    if (tail != null) parts.push(`tail${tail}`)
    parts.push(formatLogTimestamp(now ?? new Date()))
  }
  if (filtered) parts.push('filtered')
  return `${parts.join('_')}.log.gz`
}
