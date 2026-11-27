<template>
  <section class="page" :data-module="meta.key">
    <header class="page-head">
      <div>
        <h2>{{ meta.name }}</h2>
        <p class="page-desc">{{ meta.desc }}</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" data-testid="open-create" type="button" @click="openCreate">
          登记{{ meta.entity }}
        </button>
        <button class="btn" data-testid="export" type="button" @click="exportRows">
          导出{{ meta.name }}清单
        </button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <p class="status-legend">
      <span v-for="item in statusSummary" :key="item.status" class="legend-item">
        {{ item.status }}：{{ item.count }}
      </span>
    </p>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :data-testid="`filter-${field}`" :placeholder="`按${field}检索`" />
      </label>
      <label v-if="meta.key === 'archive_mgmt'" class="filter-item">
        <span>封存凭据</span>
        <input v-model="sealFilter" data-testid="filter-seal-no" placeholder="按封存凭据核对" />
      </label>
      <button class="btn" data-testid="query" type="submit">查询</button>
      <button class="btn ghost" data-testid="reset" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in meta.fields" :key="column">{{ column }}</th>
          <th>当前状态</th>
          <th>封存凭据</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="row in rows"
          :key="String(row.id)"
          :data-testid="`row-${row.id}`"
          :data-status="String(row.status)"
        >
          <td v-for="column in meta.fields" :key="column">{{ row[column] ?? '—' }}</td>
          <td>{{ row.status }}</td>
          <td>
            <span v-if="row['封存凭据']" class="seal-badge" :data-testid="`seal-${row.id}`">
              {{ row['封存凭据'] }}
            </span>
            <span v-else-if="row['待补交凭据']" class="seal-badge pending" :data-testid="`seal-pending-${row.id}`">
              待补交 {{ row['待补交凭据'] }}
            </span>
            <span v-else>—</span>
          </td>
          <td class="row-actions">
            <button
              v-for="action in meta.actions"
              :key="action"
              class="link"
              type="button"
              :data-testid="`action-${row.id}-${action}`"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
            <button
              v-if="meta.key === 'restoration_log'"
              class="link"
              type="button"
              :data-testid="`boundary-${row.id}`"
              @click="openBoundary(row)"
            >
              封存边界
            </button>
            <button
              v-if="meta.key === 'archive_mgmt' && !row['封存凭据']"
              class="link"
              type="button"
              :data-testid="`bind-${row.id}`"
              @click="bindProcess(row)"
            >
              指定工序
            </button>
            <button
              v-if="meta.key === 'archive_mgmt'"
              class="link"
              type="button"
              :data-testid="`check-${row.id}`"
              @click="openArchiveCheck(row)"
            >
              台账核对
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="meta.fields.length + 3" class="empty-state">
            暂无{{ meta.name }}数据，可先登记{{ meta.entity }}
          </td>
        </tr>
      </tbody>
    </table>

    <div v-if="showCreate" class="modal-backdrop" data-testid="create-modal">
      <form class="modal" @submit.prevent="submitCreate">
        <h3>登记{{ meta.entity }}</h3>
        <label v-for="field in createFormFields" :key="field" class="create-field">
          <span>{{ field }}<em v-if="field === '关联工序'" class="field-hint">（选填，明确档案归属工序，避免同名歧义）</em></span>
          <input v-model="createValues[field]" :data-testid="`create-${field}`" :required="isRequired(field)" />
        </label>
        <div class="page-actions">
          <button class="btn" type="button" @click="showCreate = false">取消</button>
          <button class="btn primary" data-testid="submit-create" type="submit" :disabled="creating">
            {{ creating ? '提交中…' : '确认登记' }}
          </button>
        </div>
      </form>
    </div>

    <div v-if="detailModal.show" class="modal-backdrop" data-testid="detail-modal">
      <div class="modal detail-modal">
        <h3>{{ detailModal.title }}</h3>
        <p class="detail-message" :class="{ 'detail-ok': detailModal.ok, 'detail-warn': !detailModal.ok }">
          {{ detailModal.message }}
        </p>
        <template v-if="detailModal.boundary">
          <dl class="detail-list">
            <dt>所属工序</dt><dd>{{ detailModal.boundary.process }}</dd>
            <dt>是否已纳入档案</dt>
            <dd>
              <span :class="detailModal.boundary.included ? 'seal-badge' : 'seal-badge pending'">
                {{ detailModal.boundary.included ? '已纳入' : '未纳入' }}
              </span>
            </dd>
            <dt>封存凭据</dt><dd>{{ detailModal.boundary.seal_no || detailModal.boundary.pending_seal_no || '—' }}</dd>
            <dt>关联档案</dt><dd>{{ detailModal.boundary.archive_name || '—' }}</dd>
            <dt>下一次是否需补交</dt>
            <dd>{{ detailModal.boundary.need_resubmit ? '需要，审核通过后按原凭据补纳入' : '不需要' }}</dd>
          </dl>
        </template>
        <template v-if="detailModal.check">
          <dl class="detail-list">
            <dt>封存凭据</dt><dd>{{ detailModal.check.seal_no }}</dd>
            <dt>归属工序</dt><dd>{{ detailModal.check.process }}</dd>
            <dt>档案台账</dt>
            <dd>{{ detailModal.check.archive?.id }} 号 {{ detailModal.check.archive?.档案名称 }}（{{ detailModal.check.archive?.status }}）</dd>
            <dt>交班清单日志</dt>
            <dd>
              <ul class="check-list">
                <li v-for="log in detailModal.check.logs" :key="log.id">
                  #{{ log.id }} {{ log.施工日期 }} {{ log.status }} · {{ log.封存凭据 }} · {{ log.补交状态 }}
                </li>
                <li v-if="!detailModal.check.logs.length" class="empty-state">暂无纳入日志</li>
              </ul>
            </dd>
            <dt>验收记录</dt>
            <dd>
              <ul class="check-list">
                <li v-for="acc in detailModal.check.acceptance_records" :key="acc.id">
                  #{{ acc.id }} {{ acc.验收工序 }} · {{ acc.封存凭据 }}
                </li>
                <li v-if="!detailModal.check.acceptance_records.length" class="empty-state">暂无关联验收记录</li>
              </ul>
            </dd>
            <dt>核对结论</dt>
            <dd>
              <ul v-if="detailModal.check.mismatches.length" class="check-list warn">
                <li v-for="(m, i) in detailModal.check.mismatches" :key="i">{{ m }}</li>
              </ul>
              <span v-else class="seal-badge">一致</span>
            </dd>
          </dl>
        </template>
        <div class="page-actions">
          <button class="btn primary" type="button" @click="detailModal.show = false">关闭</button>
        </div>
      </div>
    </div>

    <footer class="page-foot">
      <span>共 {{ total }} 条{{ meta.name }}记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import {
  bindArchiveProcess,
  createEntry,
  downloadEntries,
  fetchArchiveCheck,
  fetchSealBoundary,
  listEntries,
  moduleMeta,
  runAction as applyAction,
} from '@/api/backend-service'
import type { ActionResult, ArchiveCheck, EntryRow, SealBoundary } from '@/data/types'
const props = defineProps<{ moduleKey: string }>()
const meta = moduleMeta(props.moduleKey)
const rows = ref<EntryRow[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const sealFilter = ref('')
const filterFields = meta.fields.slice(0, 3)
const showCreate = ref(false)
const creating = ref(false)
const createValues = ref<Record<string, string>>({})

// 档案归档登记时可显式指定归属工序，替代仅凭标题猜测
const createFormFields = computed(() =>
  meta.key === 'archive_mgmt' ? [...meta.fields, '关联工序'] : meta.fields,
)

const detailModal = ref<{
  show: boolean
  title: string
  ok: boolean
  message: string
  boundary: SealBoundary | null
  check: ArchiveCheck | null
}>({ show: false, title: '', ok: false, message: '', boundary: null, check: null })

const stats = computed(() => [
  { label: meta.metrics[0], value: total.value },
  { label: meta.metrics[1], value: rows.value.filter((r) => r.status === meta.statuses[1]).length },
  { label: meta.metrics[2], value: rows.value.filter((r) => String(r.status) === meta.statuses[meta.statuses.length - 1]).length },
])

const statusSummary = computed(() =>
  meta.statuses.map((s) => ({ status: s, count: rows.value.filter((r) => String(r.status) === s).length })),
)

function isRequired(field: string): boolean {
  // 关联工序为选填，其余沿用登记表单默认必填
  return field !== '关联工序'
}

function resetFilters() {
  filters.value = {}
  sealFilter.value = ''
  void reload()
}

async function exportRows() {
  errorMessage.value = ''
  try { await downloadEntries(meta.key, filters.value) }
  catch (e) { errorMessage.value = e instanceof Error ? e.message : '导出失败' }
}

function openCreate() {
  createValues.value = Object.fromEntries(createFormFields.value.map((f) => [f, '']))
  errorMessage.value = ''
  showCreate.value = true
}

async function submitCreate() {
  creating.value = true; errorMessage.value = ''
  try {
    const r: ActionResult = await createEntry(meta.key, createValues.value)
    if (!r.ok) throw new Error(r.message)
    showCreate.value = false; await reload()
  } catch (e) { errorMessage.value = e instanceof Error ? e.message : '登记失败' }
  finally { creating.value = false }
}

async function runAction(action: string, row: EntryRow) {
  errorMessage.value = ''
  try {
    const r = await applyAction(meta.key, Number(row.id), action)
    if (!r.ok) throw new Error(r.message)
    await reload()
  } catch (e) { errorMessage.value = e instanceof Error ? e.message : '操作失败' }
}

async function openBoundary(row: EntryRow) {
  errorMessage.value = ''
  try {
    const boundary = await fetchSealBoundary(Number(row.id))
    detailModal.value = {
      show: true,
      title: `封存边界 · 日志 #${row.id}（${row['对应工序'] ?? ''}）`,
      ok: boundary.ok,
      message: boundary.message,
      boundary,
      check: null,
    }
  } catch (e) {
    errorMessage.value = e instanceof Error ? e.message : '封存边界查询失败'
  }
}

async function openArchiveCheck(row: EntryRow) {
  errorMessage.value = ''
  try {
    const check = await fetchArchiveCheck({
      archiveId: Number(row.id),
      sealNo: row['封存凭据'] ? String(row['封存凭据']) : undefined,
    })
    detailModal.value = {
      show: true,
      title: `台账核对 · 档案 #${row.id}`,
      ok: check.ok,
      message: check.message,
      boundary: null,
      check,
    }
  } catch (e) {
    errorMessage.value = e instanceof Error ? e.message : '台账核对失败'
  }
}

async function bindProcess(row: EntryRow) {
  errorMessage.value = ''
  const process = window.prompt('请输入该档案显式归属的工序名称（如：北廊 / 北廊拆棚）：', String(row['关联工序'] ?? ''))
  if (process === null) return
  if (!process.trim()) {
    errorMessage.value = '关联工序不能为空'
    return
  }
  try {
    const r = await bindArchiveProcess(Number(row.id), process.trim())
    if (!r.ok) throw new Error(r.message)
    await reload()
  } catch (e) {
    errorMessage.value = e instanceof Error ? e.message : '关联工序指定失败'
  }
}

async function reload() {
  errorMessage.value = ''
  try {
    const query: Record<string, string> = { ...filters.value }
    if (meta.key === 'archive_mgmt' && sealFilter.value.trim()) {
      query.seal_no = sealFilter.value.trim()
    }
    const payload = await listEntries(meta.key, query)
    rows.value = payload.items; total.value = payload.total
  } catch (e) { errorMessage.value = e instanceof Error ? e.message : '业务列表读取失败' }
}

onMounted(reload)
</script>

<style scoped>
.modal-backdrop { position:fixed; inset:0; background:rgba(0,0,0,.35); display:flex; align-items:center; justify-content:center; z-index:100; }
.modal { background:#fff; border-radius:8px; padding:24px; width:480px; max-height:80vh; overflow-y:auto; }
.detail-modal { width:640px; }
.create-field { display:flex; flex-direction:column; gap:4px; margin-bottom:12px; }
.create-field span { font-size:13px; color:var(--text-secondary,#4a5568); }
.create-field input { padding:6px 8px; border:1px solid var(--border-color,#e2e8f0); border-radius:4px; font-size:13px; }
.field-hint { font-style:normal; color:#718096; font-size:12px; }
.seal-badge { display:inline-block; padding:2px 8px; border-radius:10px; background:#e6f4ea; color:#1e7e34; font-size:12px; white-space:nowrap; }
.seal-badge.pending { background:#fff4e0; color:#b45309; }
.detail-message { margin:8px 0 16px; font-size:14px; }
.detail-ok { color:#1e7e34; }
.detail-warn { color:#b45309; }
.detail-list { display:grid; grid-template-columns:120px 1fr; gap:8px 12px; margin:0; font-size:13px; }
.detail-list dt { color:#4a5568; }
.detail-list dd { margin:0; }
.check-list { margin:0; padding-left:18px; }
.check-list.warn { color:#c53030; }
</style>
