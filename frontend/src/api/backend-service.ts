import { request } from './client'
import { moduleMeta } from '@/data/modules'
import type { ActionResult, EntryRow, OverviewResult, PageResult } from '@/data/types'

export { moduleMeta }

export async function listEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<PageResult> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}?${query}`)
  if (!response.ok) throw new Error('业务数据读取失败')
  return await response.json()
}

export async function createEntry(
  key: string,
  values: Record<string, string>,
): Promise<ActionResult> {
  const response = await request(`/api/${key}`, {
    method: 'POST',
    body: JSON.stringify({ values }),
  })
  return await response.json()
}

export async function runAction(
  key: string,
  id: number,
  action: string,
): Promise<ActionResult> {
  const response = await request(`/api/${key}/${id}/actions`, {
    method: 'POST',
    body: JSON.stringify({ action }),
  })
  return await response.json()
}

export async function downloadEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<void> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}/export?${query}`)
  if (!response.ok) throw new Error('业务数据导出失败')
  const url = URL.createObjectURL(await response.blob())
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `${key}.csv`
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
  URL.revokeObjectURL(url)
}

export async function loadOverview(): Promise<OverviewResult> {
  const response = await request('/api/overview')
  if (!response.ok) throw new Error('运营概览读取失败')
  return await response.json()
}

export type DependentStatus = {
  module: string
  entry_id: number
  entry: Record<string, unknown>
  upstream: { module: string; id: number; name: string; status: string }[]
  downstream: { module: string; id: number; name: string; status: string }[]
}

export async function fetchDependentStatus(
  moduleKey: string,
  entryId: number,
): Promise<DependentStatus> {
  const response = await request(`/api/cross-module/dependent-status/${moduleKey}/${entryId}`)
  if (!response.ok) throw new Error('跨模块状态查询失败')
  return await response.json()
}

export async function fetchDependencyGraph(): Promise<{ nodes: unknown[]; edges: unknown[] }> {
  const response = await request('/api/cross-module/dependency-graph')
  if (!response.ok) throw new Error('依赖图加载失败')
  return await response.json()
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

/** 封存边界：工匠重新打开修缮日志时查询本条是否已纳入档案、下次是否需补交。 */
export async function fetchSealBoundary(logId: number): Promise<SealBoundary> {
  const response = await request(`/api/cross-module/seal-boundary/restoration_log/${logId}`)
  if (!response.ok) throw new Error('封存边界查询失败')
  return await response.json()
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

/** 台账核对：档案台账与修缮日志交班清单按同一封存凭据核对一致性。 */
export async function fetchArchiveCheck(params: { archiveId?: number; sealNo?: string }): Promise<ArchiveCheck> {
  const query = new URLSearchParams()
  if (params.archiveId !== undefined) query.set('archive_id', String(params.archiveId))
  if (params.sealNo) query.set('seal_no', params.sealNo)
  const response = await request(`/api/cross-module/archive-check?${query.toString()}`)
  if (!response.ok) throw new Error('台账核对失败')
  return await response.json()
}

/** 为档案补登显式关联工序：消解仅有标题的老档案的工序归属歧义。 */
export async function bindArchiveProcess(id: number, process: string): Promise<ActionResult> {
  const response = await request(`/api/archive_mgmt/${id}/bind-process`, {
    method: 'POST',
    body: JSON.stringify({ process }),
  })
  return await response.json()
}
