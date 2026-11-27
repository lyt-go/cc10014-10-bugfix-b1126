/** 纯前端数据层的公共类型：与全栈版后端返回的结构保持一致，换回后端时页面不用改。 */

export type EntryRow = {
  id: number
  status: string
  pending: boolean
  abnormal: boolean
  [field: string]: string | number | boolean
}

export type ModuleMeta = {
  key: string
  name: string
  entity: string
  desc: string
  fields: string[]
  statuses: string[]
  actions: string[]
  actionTargets: Record<string, string>
  metrics: string[]
}

export type PageResult = {
  items: EntryRow[]
  total: number
  page: number
  size: number
}

export type ActionResult = {
  ok: boolean
  message: string
}

export type SealBoundary = {
  ok: boolean
  message: string
  log_id: number
  process: string
  included: boolean
  seal_no: string
  pending_seal_no?: string
  archive_id?: number
  archive_name?: string
  补交状态?: string
  need_resubmit: boolean
  process_seals: { seal_no: string; archive_id: number; archive_name: string; process: string }[]
}

export type ArchiveCheck = {
  ok: boolean
  message: string
  seal_no: string
  process: string
  archive: { id: number; 档案名称: string; 关联工序: string; status: string }
  logs: { id: number; 施工日期: string; 对应工序: string; status: string; 封存凭据: string; 补交状态: string }[]
  acceptance_records: { id: number; 验收工序: string; 封存凭据: string }[]
  registry: Record<string, unknown> | null
  mismatches: string[]
}

export type OverviewResult = {
  cards: { label: string; value: number }[]
  modules: { name: string; created: number; pending: number; abnormal: number }[]
}
