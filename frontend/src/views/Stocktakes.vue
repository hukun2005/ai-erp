<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { stocktakeApi, templateApi, warehouseApi } from '../api'
import ScanInput from '../components/ScanInput.vue'
import { printDocument } from '../utils/print'

const loading = ref(false)
const list = ref([])
const total = ref(0)
const page = ref(1)
const query = reactive({ warehouse_id: null, status: '' })
const warehouses = ref([])
const printTemplate = ref(null)

const createVisible = ref(false)
const submitting = ref(false)
const form = reactive({ warehouse_id: null, remark: '' })

const detailVisible = ref(false)
const detail = ref(null)
const saving = ref(false)
const itemPage = ref(1)
const itemPageSize = ref(100)
const itemTotal = ref(0)

const statusMap = {
  draft: { text: '盘点中', type: 'warning' },
  pending: { text: '待审批', type: 'primary' },
  completed: { text: '已完成', type: 'success' },
  cancelled: { text: '已取消', type: 'info' },
}

const editable = computed(() => detail.value && detail.value.status === 'draft')

function datetime(value) {
  return String(value || '').replace('T', ' ').slice(0, 16)
}

function money(value) {
  return Number(value || 0).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

async function load() {
  loading.value = true
  try {
    const data = await stocktakeApi.list({ ...query, page: page.value, page_size: 20 })
    list.value = data.items
    total.value = data.total
  } finally {
    loading.value = false
  }
}

async function loadWarehouses() {
  warehouses.value = await warehouseApi.options()
}

async function openCreate() {
  Object.assign(form, { warehouse_id: null, remark: '' })
  await loadWarehouses()
  createVisible.value = true
}

async function create() {
  if (!form.warehouse_id) return ElMessage.warning('请选择仓库')
  submitting.value = true
  try {
    const result = await stocktakeApi.create({ ...form })
    ElMessage.success(`盘点单 ${result.stocktake_no} 已生成，共 ${result.items.length} 个库位商品`)
    createVisible.value = false
    await load()
    await openDetail(result)
  } finally {
    submitting.value = false
  }
}

async function openDetail(row) {
  clearScanLog()
  itemPage.value = 1
  // 草稿需要整单明细做录入/扫码编辑；已过账单据按页加载明细，避免大盘点单一次性全量返回
  const params = row.status === 'draft' ? { all_items: '1' } : { page: 1, page_size: itemPageSize.value }
  detail.value = await stocktakeApi.detail(row.id, params)
  itemTotal.value = detail.value.item_total || 0
  detailVisible.value = true
}

async function loadItems() {
  const data = await stocktakeApi.detail(detail.value.id, { page: itemPage.value, page_size: itemPageSize.value })
  detail.value.items = data.items
  itemTotal.value = data.item_total || 0
}

function fillBook() {
  detail.value.items.forEach((item) => {
    if (item.actual_qty === null || item.actual_qty === undefined) item.actual_qty = item.book_qty
  })
}

function recalc(item) {
  item.diff_qty = (item.actual_qty ?? item.book_qty) - item.book_qty
}

const diffSummary = computed(() => ({
  count: detail.value?.diff_count || 0,
  qty: detail.value?.diff_quantity || 0,
}))

async function save() {
  const items = detail.value.items
    .filter((item) => item.actual_qty !== null && item.actual_qty !== undefined)
    .map((item) => ({ item_id: item.id, actual_qty: Number(item.actual_qty) }))
  if (!items.length) return ElMessage.warning('请至少录入一项实盘数量')
  saving.value = true
  try {
    detail.value = await stocktakeApi.saveItems(detail.value.id, items)
    ElMessage.success('实盘数量已保存')
    load()
  } finally {
    saving.value = false
  }
}

async function complete() {
  await ElMessageBox.confirm(
    `将按实盘结果生成 ${diffSummary.value.count} 条差异调整（共 ${diffSummary.value.qty} 件）；` +
      '若开启了差异审批，会先进入待审批，由其他人复核后才调整库存。',
    '提交盘点',
    { type: 'warning' }
  )
  const result = await stocktakeApi.changeStatus(detail.value.id, 'complete')
  detail.value = result
  ElMessage.success(
    result.status === 'pending'
      ? '差异已提交，等待复核'
      : `盘点已完成，库存差异已调整${adjustmentTip(result)}`
  )
  load()
}

function adjustmentTip(result) {
  const docs = result?.adjustments || []
  if (!docs.length) return ''
  return `，已生成调整单 ${docs.map((doc) => doc.adjust_no).join('、')}`
}

async function approveDiff() {
  const { value } = await ElMessageBox.prompt('可填写复核意见（选填）', '通过差异审批', {
    inputPlaceholder: '差异属实',
    confirmButtonText: '通过',
    cancelButtonText: '取消',
    inputValidator: () => true,
  }).catch(() => ({ value: null }))
  if (value === null) return
  detail.value = await stocktakeApi.changeStatus(detail.value.id, 'approve', value)
  ElMessage.success(`已通过，库存差异已调整${adjustmentTip(detail.value)}`)
  load()
}

async function generateAdjustment() {
  const result = await stocktakeApi.generateAdjustment(detail.value.id)
  const docs = result?.adjustments || []
  ElMessage.success(
    docs.length ? `已生成调整单 ${docs.map((doc) => doc.adjust_no).join('、')}` : '本次盘点没有差异，无需生成调整单'
  )
  detail.value = await stocktakeApi.detail(detail.value.id, { page: 1, page_size: itemPageSize.value })
  itemPage.value = 1
  itemTotal.value = detail.value.item_total || 0
  load()
}

async function rejectDiff() {
  const { value } = await ElMessageBox.prompt('请填写驳回原因', '驳回差异审批', {
    inputPlaceholder: '例如：实盘数量录错，请重盘',
    confirmButtonText: '驳回',
    cancelButtonText: '取消',
  }).catch(() => ({ value: null }))
  if (value === null) return
  detail.value = await stocktakeApi.changeStatus(detail.value.id, 'reject', value)
  ElMessage.success('已驳回，可重新录入实盘')
  load()
}

async function cancel() {
  await ElMessageBox.confirm('确定取消该盘点单吗？不会产生库存调整。', '取消盘点', { type: 'warning' })
  detail.value = await stocktakeApi.changeStatus(detail.value.id, 'cancel')
  ElMessage.success('盘点单已取消')
  load()
}

async function remove(row) {
  await ElMessageBox.confirm(`确定删除草稿盘点单 ${row.stocktake_no} 吗？`, '删除确认', { type: 'warning' })
  await stocktakeApi.remove(row.id)
  ElMessage.success('已删除')
  load()
}

function rowClass({ row }) {
  if (scanHit.value && row.id === scanHit.value) return 'row-scan'
  if (!row.diff_qty) return ''
  return row.diff_qty > 0 ? 'row-profit' : 'row-loss'
}

const scanHit = ref(null)
// 扫码盘点汇总：按商品累计本次扫了多少件
const scanLog = reactive({})
const scanSummary = computed(() => {
  const rows = Object.values(scanLog)
  return { types: rows.length, total: rows.reduce((sum, row) => sum + row.count, 0) }
})

function clearScanLog() {
  Object.keys(scanLog).forEach((key) => delete scanLog[key])
  scanHit.value = null
}

/** 扫码盘点：每扫一次给对应商品实盘 +1，并累计到本次扫码汇总。 */
async function onScan(product) {
  const row = detail.value.items.find((it) => it.product_id === product.id)
  if (!row) {
    ElMessage.warning(`「${product.name}」不在本次盘点范围内`)
    return
  }
  row.actual_qty = Number(row.actual_qty ?? 0) + 1
  recalc(row)
  scanHit.value = row.id
  scanLog[product.id] = {
    name: product.name,
    count: (scanLog[product.id]?.count || 0) + 1,
  }
}

async function printStocktake(row) {
  const data = await stocktakeApi.detail(row.id, { all_items: '1' })
  const diffRows = (data.items || []).filter((item) => item.diff_qty)
  const ok = printDocument({
    title: '库存盘点单',
    subtitle: data.stocktake_no,
    meta: [
      ['仓库', data.warehouse_name],
      ['状态', statusMap[data.status]?.text || data.status],
      ['创建人', data.created_by],
      ['创建时间', datetime(data.created_at)],
    ],
    columns: [
      { key: 'product_sku', label: 'SKU' },
      { key: 'product_name', label: '商品名称' },
      { key: 'location_code', label: '库位' },
      { key: 'book_qty', label: '账面数量', align: 'right' },
      { key: 'actual_qty', label: '实盘数量', align: 'right' },
      { key: 'diff_qty', label: '差异', align: 'right' },
    ],
    rows: data.items || [],
    totals: [
      { label: '差异行数', value: `${diffRows.length} 项` },
      { label: '差异件数', value: `${diffRows.reduce((sum, item) => sum + Math.abs(item.diff_qty), 0)} 件`, bold: true },
    ],
    remark: data.remark,
    template: printTemplate.value,
  })
  if (!ok) ElMessage.warning('浏览器拦截了打印窗口，请允许弹出窗口')
}

onMounted(async () => {
  load()
  printTemplate.value = await templateApi.defaultFor('stocktake').catch(() => null)
})
</script>

<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h1 class="page-title">库存盘点</h1>
        <p class="page-subtitle">建单时冻结账面数量，录入实盘后自动计算盈亏并生成库存调整流水。</p>
      </div>
      <el-button v-perm="'stocktake:manage'" type="primary" @click="openCreate">
        <el-icon><Plus /></el-icon>&nbsp;新建盘点单
      </el-button>
    </div>

    <div class="card">
      <div class="toolbar">
        <el-select v-model="query.warehouse_id" placeholder="全部仓库" clearable style="width: 160px" @change="load">
          <el-option v-for="wh in warehouses" :key="wh.id" :label="wh.name" :value="wh.id" />
        </el-select>
        <el-select v-model="query.status" placeholder="全部状态" clearable style="width: 130px" @change="load">
          <el-option v-for="(v, k) in statusMap" :key="k" :label="v.text" :value="k" />
        </el-select>
        <el-button @click="load"><el-icon><Refresh /></el-icon>&nbsp;刷新</el-button>
      </div>

      <el-table :data="list" v-loading="loading">
        <el-table-column prop="stocktake_no" label="盘点单号" width="180" />
        <el-table-column prop="warehouse_name" label="仓库" min-width="130" />
        <el-table-column label="差异" width="140" align="center">
          <template #default="{ row }">
            <el-tag v-if="row.diff_count" type="danger" size="small">{{ row.diff_count }} 项 / {{ row.diff_quantity }} 件</el-tag>
            <span v-else class="muted">无差异</span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="90" align="center">
          <template #default="{ row }">
            <el-tag :type="statusMap[row.status]?.type" size="small">{{ statusMap[row.status]?.text }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="created_by" label="创建人" width="100" />
        <el-table-column label="创建时间" width="150">
          <template #default="{ row }">{{ datetime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="完成时间" width="150">
          <template #default="{ row }">{{ row.completed_at ? datetime(row.completed_at) : '—' }}</template>
        </el-table-column>
        <el-table-column label="操作" width="190" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="openDetail(row)">{{ row.status === 'draft' ? '录入盘点' : '查看明细' }}</el-button>
            <el-button link type="primary" @click="printStocktake(row)">打印</el-button>
            <el-button v-if="row.status === 'draft'" v-perm="'stocktake:manage'" link type="danger" @click="remove(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-pagination v-model:current-page="page" :page-size="20" :total="total" layout="total, prev, pager, next" class="pager" @current-change="load" />
    </div>

    <el-dialog v-model="createVisible" title="新建盘点单" width="480px">
      <el-form label-width="80px">
        <el-form-item label="盘点仓库" required>
          <el-select v-model="form.warehouse_id" placeholder="选择仓库" style="width: 100%">
            <el-option v-for="wh in warehouses" :key="wh.id" :label="`${wh.name}（${wh.code}）`" :value="wh.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="form.remark" placeholder="例如：月度全盘 / 循环盘点" />
        </el-form-item>
      </el-form>
      <div class="dialog-tip">建单时会冻结该仓库当前有库存的库位商品，共形成盘点明细。</div>
      <template #footer>
        <el-button @click="createVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="create">生成盘点单</el-button>
      </template>
    </el-dialog>

    <el-drawer v-model="detailVisible" :title="detail ? `${detail.stocktake_no} · ${detail.warehouse_name}` : '盘点明细'" size="720px">
      <div v-if="detail">
        <div class="detail-toolbar">
          <el-tag :type="statusMap[detail.status]?.type">{{ statusMap[detail.status]?.text }}</el-tag>
          <span class="muted">创建人 {{ detail.created_by }} · {{ datetime(detail.created_at) }}</span>
          <div class="spacer" />
          <el-button size="small" @click="printStocktake(detail)">打印</el-button>
          <el-button
            v-if="detail.status === 'completed'"
            size="small"
            type="primary"
            plain
            @click="generateAdjustment"
          >生成调整单</el-button>
          <template v-if="detail.status === 'pending'">
            <el-button size="small" type="success" @click="approveDiff">通过差异</el-button>
            <el-button size="small" type="danger" plain @click="rejectDiff">驳回</el-button>
          </template>
          <template v-if="editable">
            <ScanInput placeholder="扫码盘点：每扫一次实盘 +1" @select="onScan" />
            <el-button size="small" @click="fillBook">差异按账面填充</el-button>
            <el-button size="small" type="primary" :loading="saving" @click="save">保存实盘</el-button>
            <el-button size="small" type="success" @click="complete">完成盘点</el-button>
            <el-button size="small" @click="cancel">取消盘点</el-button>
          </template>
        </div>

        <div class="summary">
          差异 {{ diffSummary.count }} 项，合计 {{ diffSummary.qty }} 件
          <span class="muted">（正数盘盈，负数盘亏；未录入视为账实一致）</span>
        </div>

        <div v-if="editable && scanSummary.total" class="scan-summary">
          <span class="scan-summary-title">本次扫码：{{ scanSummary.types }} 种 / {{ scanSummary.total }} 件</span>
          <el-tag v-for="(item, pid) in scanLog" :key="pid" size="small" effect="plain">
            {{ item.name }} × {{ item.count }}
          </el-tag>
          <el-button link size="small" type="info" @click="clearScanLog">清空统计</el-button>
        </div>
        <el-alert
          v-if="detail.status === 'pending'"
          type="warning"
          :closable="false"
          show-icon
          title="差异待复核"
          :description="`提交人 ${detail.submitted_by || '—'}，通过后才会调整库存`"
          style="margin-bottom: 10px"
        />

        <el-table :data="detail.items" size="small" max-height="560" :row-class-name="rowClass">
          <el-table-column prop="product_name" label="商品" min-width="150" show-overflow-tooltip />
          <el-table-column prop="product_sku" label="SKU" width="110" />
          <el-table-column prop="location_code" label="库位" width="100" />
          <el-table-column prop="book_qty" label="账面" width="70" align="center" />
          <el-table-column label="实盘" width="140">
            <template #default="{ row }">
              <el-input-number
                v-if="editable"
                v-model="row.actual_qty"
                :min="0"
                size="small"
                controls-position="right"
                style="width: 110px"
                @change="recalc(row)"
              />
              <span v-else>{{ row.actual_qty ?? '—' }}</span>
            </template>
          </el-table-column>
          <el-table-column label="差异" width="80" align="center">
            <template #default="{ row }">
              <span v-if="row.diff_qty" :class="row.diff_qty > 0 ? 'up' : 'down'">
                {{ row.diff_qty > 0 ? '+' : '' }}{{ row.diff_qty }}
              </span>
              <span v-else class="muted">0</span>
            </template>
          </el-table-column>
        </el-table>

        <el-pagination
          v-if="!editable && itemTotal > itemPageSize"
          v-model:current-page="itemPage"
          :page-size="itemPageSize"
          :total="itemTotal"
          layout="total, prev, pager, next"
          class="pager"
          @current-change="loadItems"
        />

        <div v-if="(detail.adjustments || []).length" class="adjust-list">
          <div class="adjust-title">关联库存调整单（追溯凭证，不再重复改库存）</div>
          <div v-for="doc in detail.adjustments" :key="doc.id" class="adjust-row">
            <el-tag size="small" type="success" effect="plain">{{ doc.adjust_type_name }}</el-tag>
            <router-link :to="{ name: 'stock-adjustments' }" class="adjust-no">{{ doc.adjust_no }}</router-link>
            <span class="muted">金额 ¥{{ money(doc.total_amount) }}</span>
          </div>
        </div>
      </div>
    </el-drawer>
  </div>
</template>

<style scoped>
.toolbar {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 10px;
}

.muted {
  color: var(--text-secondary);
  font-size: 12px;
}

.dialog-tip {
  font-size: 12px;
  color: var(--text-secondary);
}

.detail-toolbar {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 10px;
}

.spacer {
  flex: 1;
}

.summary {
  margin-bottom: 10px;
  font-size: 13px;
}

.scan-summary {
  display: flex;
  align-items: center;
  flex-wrap: wrap;  gap: 8px;
  margin-bottom: 10px;
  padding: 8px 10px;
  border-radius: 8px;
  background: #eff6ff;
  border: 1px solid #bfdbfe;
}

.scan-summary-title {
  font-size: 13px;
  font-weight: 600;
  color: #1d4ed8;
}

.adjust-list {
  margin-top: 14px;
  padding: 10px 12px;
  border-radius: 8px;
  background: #f0fdf4;
  border: 1px solid #bbf7d0;
}

.adjust-title {
  font-size: 13px;
  font-weight: 600;
  color: #166534;
  margin-bottom: 8px;
}

.adjust-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
  font-size: 13px;
}

.adjust-no {
  color: #1d4ed8;
  text-decoration: none;
}

.up {
  color: #16a34a;
  font-weight: 600;
}

.down {
  color: #dc2626;
  font-weight: 600;
}

:deep(.row-profit) {
  background: #f0fdf4;
}

:deep(.row-loss) {
  background: #fef2f2;
}

:deep(.row-scan) {
  background: #eff6ff;
  outline: 1px solid #93c5fd;
}
</style>
