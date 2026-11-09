<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { inboundApi, productApi, supplierApi, warehouseApi } from '../api'
import DocRecognize from '../components/DocRecognize.vue'
import PrintDialog from '../components/PrintDialog.vue'
import QuickPartnerDialog from '../components/QuickPartnerDialog.vue'
import QuickProductDialog from '../components/QuickProductDialog.vue'
import ScanInput from '../components/ScanInput.vue'
import { saveBlob } from '../utils/download'
import { useQuickCreate } from '../utils/quickCreate'
import {
  adoptCandidates,
  itemPayload,
  mapRecognizedItems,
  markMatched,
  matchTag,
  photoFields,
} from '../utils/recognize'

const loading = ref(false)
const list = ref([])
const printVisible = ref(false)
const printIds = ref([])

function openPrint(ids) {
  const rows = (Array.isArray(ids) ? ids : [ids]).filter(Boolean)
  if (!rows.length) {
    ElMessage.warning('没有可打印的入库单')
    return
  }
  printIds.value = rows
  printVisible.value = true
}
const total = ref(0)
const page = ref(1)
const query = reactive({ keyword: '', status: '', supplier_id: null })

const suppliers = ref([])
const warehouses = ref([])
const products = ref([])
const printingId = ref(null)

const statusMap = {
  draft: { text: '草稿', type: 'warning' },
  confirmed: { text: '已入库', type: 'success' },
  cancelled: { text: '已取消', type: 'info' },
}

const editorVisible = ref(false)
const saving = ref(false)
const editingId = ref(null)
const form = reactive({
  supplier_id: null,
  warehouse_id: null,
  receipt_date: '',
  remark: '',
  photo_path: '',
  photo_name: '',
  photo_paths: [],
  ocr_status: 'manual',
  ocr_raw: '',
  raw_supplier_name: '',
  items: [],
})

const recognizeWarnings = ref([])
const recognizedPartner = ref(null)
const quickProductVisible = ref(false)
const quickProductPreset = ref({})
const quickProductTarget = ref(null)
const quickPartnerVisible = ref(false)
const quickPartnerName = ref('')

const detailVisible = ref(false)
const detail = ref(null)

const editable = computed(() => !editingId.value || detail.value?.status === 'draft')

function datetime(value) {
  return String(value || '').replace('T', ' ').slice(0, 16)
}

function amountOf(item) {
  return round(Number(item.quantity || 0) * Number(item.unit_price || 0))
}

function round(value) {
  return Math.round((Number(value) || 0) * 100) / 100
}

function recalc(item) {
  item.amount = amountOf(item)
}

const formTotal = computed(() => round(form.items.reduce((sum, item) => sum + amountOf(item), 0)))

async function load() {
  loading.value = true
  try {
    const data = await inboundApi.list({ ...query, page: page.value, page_size: 20 })
    list.value = data.items
    total.value = data.total
  } finally {
    loading.value = false
  }
}

async function loadOptions() {
  const [supplierRows, warehouseRows, productRows] = await Promise.all([
    supplierApi.all(),
    warehouseApi.options(),
    // 后端 page_size 上限 200，超过会直接 422，入库页会一进来就弹错误提示
    productApi.list({ page_size: 200, product_type: 'raw_material' }),
  ])
  suppliers.value = supplierRows
  warehouses.value = warehouseRows
  products.value = productRows.items
}

function resetForm() {
  Object.assign(form, {
    supplier_id: null,
    warehouse_id: warehouses.value.find((item) => item.is_default)?.id || warehouses.value[0]?.id || null,
    receipt_date: new Date().toISOString().slice(0, 10),
    remark: '',
    photo_path: '',
    photo_name: '',
    photo_paths: [],
    ocr_status: 'manual',
    ocr_raw: '',
    raw_supplier_name: '',
    items: [],
  })
  recognizeWarnings.value = []
  recognizedPartner.value = null
}

// 顶栏「新建」入口带 ?new=1 时直接打开入库单编辑器
useQuickCreate(openCreate)

function openCreate() {
  editingId.value = null
  resetForm()
  clearBatch()
  editorVisible.value = true
}

async function openEdit(row) {
  const data = await inboundApi.detail(row.id)
  if (data.status !== 'draft') {
    openDetail(row)
    return
  }
  clearBatch()
  editingId.value = data.id
  Object.assign(form, {
    supplier_id: data.supplier_id,
    warehouse_id: data.warehouse_id,
    receipt_date: String(data.receipt_date || '').slice(0, 10),
    remark: data.remark || '',
    photo_path: data.photo_path || '',
    photo_name: data.photo_name || '',
    photo_paths: data.photo_paths || [],
    ocr_status: data.ocr_status || 'manual',
    ocr_raw: data.ocr_raw || '',
    raw_supplier_name: '',
    items: data.items.map((item) => ({ ...item })),
  })
  recognizeWarnings.value = []
  recognizedPartner.value = null
  editorVisible.value = true
}

function addItem() {
  form.items.push({ product_id: null, quantity: 1, unit_price: 0, amount: 0, batch_no: '', expiry_date: null, remark: '' })
}

/** 扫码录单：同一商品重复扫就累加数量，否则按成本价新增一行。 */
// 连续批量扫码：先把扫到的商品累计起来，再一次性写入明细行
const batchMode = ref(true)
const scanBatch = reactive({})
const batchSummary = computed(() => {
  const rows = Object.values(scanBatch)
  return { types: rows.length, total: rows.reduce((sum, row) => sum + row.count, 0) }
})

function applyScanned(product, count = 1) {
  const existing = form.items.find((item) => item.product_id === product.id)
  if (existing) {
    existing.quantity = Number(existing.quantity || 0) + count
    recalc(existing)
    return
  }
  const price = Number(product.cost_price || 0)
  form.items.push({
    product_id: product.id,
    quantity: count,
    unit_price: price,
    amount: round(price * count),
    batch_no: '',
    expiry_date: null,
    remark: '',
    matched: true,
  })
}

function onScan(product) {
  if (batchMode.value) {
    const hit = scanBatch[product.id]
    scanBatch[product.id] = { product, count: (hit?.count || 0) + 1 }
    return
  }
  applyScanned(product, 1)
  ElMessage.success(`「${product.name}」数量 +1`)
}

function applyBatch() {
  const rows = Object.values(scanBatch)
  if (!rows.length) return
  rows.forEach((row) => applyScanned(row.product, row.count))
  ElMessage.success(`已写入 ${rows.length} 种商品，共 ${batchSummary.value.total} 件`)
  clearBatch()
}

function clearBatch() {
  Object.keys(scanBatch).forEach((key) => delete scanBatch[key])
}

function removeItem(index) {
  form.items.splice(index, 1)
}

function onRecognized(result) {
  Object.assign(form, photoFields(result))
  form.raw_supplier_name = result.partner?.raw_name || ''
  recognizedPartner.value = result.partner || null
  if (result.supplier_id) form.supplier_id = result.supplier_id
  if (result.receipt_date) form.receipt_date = result.receipt_date
  form.items = mapRecognizedItems(result.items, (item) => ({
    amount: round(item.quantity * item.unit_price),
    batch_no: '',
    expiry_date: null,
  }))
  recognizeWarnings.value = result.warnings || []
  if (!form.items.length) {
    ElMessage.warning('未识别到商品行，请手动添加或换一张更清晰的照片')
    return
  }
  const seconds = ((result.elapsed_ms || 0) / 1000).toFixed(1)
  ElMessage.success(`识别完成，共 ${form.items.length} 行，用时 ${seconds} 秒，请核对商品与数量`)
}

function adoptAll() {
  const adopted = adoptCandidates(form.items)
  if (adopted) ElMessage.success(`已按相似度补全 ${adopted} 行，请重点核对标黄的行`)
  else ElMessage.info('没有需要补全的行')
}

function openQuickProduct(item) {
  quickProductTarget.value = item
  quickProductPreset.value = {
    name: item.raw_name || '',
    spec: item.raw_spec || '',
    unit: item.raw_unit || '件',
  }
  quickProductVisible.value = true
}

function onProductCreated(product) {
  products.value.unshift(product)
  const item = quickProductTarget.value
  if (item) {
    item.product_id = product.id
    markMatched(item)
  }
  quickProductTarget.value = null
}

function openQuickSupplier() {
  quickPartnerName.value = resultPartnerName()
  quickPartnerVisible.value = true
}

function resultPartnerName() {
  const supplier = suppliers.value.find((row) => row.id === form.supplier_id)
  return supplier?.name || recognizedPartner.value?.raw_name || ''
}

function onSupplierCreated(supplier) {
  suppliers.value.push(supplier)
  form.supplier_id = supplier.id
}

async function save() {
  if (batchSummary.value.total) applyBatch() // 还有未写入的扫码结果，先落进明细
  if (!form.supplier_id) return ElMessage.warning('请选择供应商')
  if (!form.warehouse_id) return ElMessage.warning('请选择入库仓库')
  if (!form.items.length) return ElMessage.warning('请至少添加一条明细')
  const missing = form.items.find((item) => !item.product_id)
  if (missing) return ElMessage.warning('有明细未选择商品，请先匹配商品')
  saving.value = true
  try {
    const payload = {
      supplier_id: form.supplier_id,
      warehouse_id: form.warehouse_id,
      receipt_date: form.receipt_date ? `${form.receipt_date}T00:00:00` : null,
      remark: form.remark,
      photo_path: form.photo_path,
      photo_name: form.photo_name,
      photo_paths: form.photo_paths,
      ocr_status: form.ocr_status,
      ocr_raw: form.ocr_raw,
      raw_supplier_name: form.raw_supplier_name,
      items: form.items.map((item) => ({
        ...itemPayload(item),
        batch_no: item.batch_no || '',
        expiry_date: item.expiry_date || null,
        remark: item.remark || '',
      })),
    }
    const result = editingId.value
      ? await inboundApi.update(editingId.value, payload)
      : await inboundApi.create(payload)
    ElMessage.success(editingId.value ? '入库单已保存' : `入库单 ${result.inbound_no} 已生成`)
    editorVisible.value = false
    await load()
  } finally {
    saving.value = false
  }
}

async function openDetail(row) {
  detail.value = await inboundApi.detail(row.id)
  detailVisible.value = true
}

async function confirmInbound(row) {
  await ElMessageBox.confirm(`确认入库单 ${row.inbound_no} 吗？确认后将增加库存。`, '确认入库', { type: 'warning' })
  await inboundApi.changeStatus(row.id, 'confirm')
  ElMessage.success('已确认入库，库存已增加')
  load()
  if (detail.value?.id === row.id) detail.value = await inboundApi.detail(row.id)
}

async function cancelInbound(row) {
  await ElMessageBox.confirm('确定取消该入库单吗？已确认的入库单会回滚库存。', '取消入库单', { type: 'warning' })
  await inboundApi.changeStatus(row.id, 'cancel')
  ElMessage.success('入库单已取消')
  load()
  if (detail.value?.id === row.id) detail.value = await inboundApi.detail(row.id)
}

async function remove(row) {
  await ElMessageBox.confirm(`确定删除草稿 ${row.inbound_no} 吗？`, '删除入库单', { type: 'warning' })
  await inboundApi.remove(row.id)
  ElMessage.success('已删除')
  load()
}

// 打印输出真实 .docx：优先套用入库单模板，没有则用系统内置版式
async function printInbound(row) {
  if (printingId.value) return
  printingId.value = row.id
  try {
    const blob = await inboundApi.printDocx(row.id)
    saveBlob(blob, `入库单_${row.inbound_no}.docx`)
    ElMessage.success('Word 单据已生成，用 Word 打开即可打印')
  } finally {
    printingId.value = null
  }
}

function productLabel(id) {
  const product = products.value.find((item) => item.id === id)
  return product ? `${product.sku} ${product.name}` : ''
}

onMounted(async () => {
  await loadOptions()
  await load()
})
</script>

<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h1 class="page-title">入库单</h1>
        <p class="page-subtitle">支持上传送货单照片自动识别并生成草稿，核对后确认入库；照片与识别原文一并留存。</p>
      </div>
      <el-button v-perm="'inbound:manage'" type="primary" @click="openCreate">
        <el-icon><Plus /></el-icon>&nbsp;新建入库单
      </el-button>
    </div>

    <div class="card">
      <div class="toolbar">
        <el-input v-model="query.keyword" placeholder="搜索单号 / 供应商" clearable style="width: 220px" @keyup.enter="load" @clear="load" />
        <el-select v-model="query.supplier_id" placeholder="全部供应商" clearable filterable style="width: 180px" @change="load">
          <el-option v-for="s in suppliers" :key="s.id" :label="s.name" :value="s.id" />
        </el-select>
        <el-select v-model="query.status" placeholder="全部状态" clearable style="width: 130px" @change="load">
          <el-option v-for="(v, k) in statusMap" :key="k" :label="v.text" :value="k" />
        </el-select>
        <el-button @click="load"><el-icon><Refresh /></el-icon>&nbsp;刷新</el-button>
        <el-button plain @click="openPrint(list.map((row) => row.id))">打印本页 PDF</el-button>
      </div>

      <el-table :data="list" v-loading="loading">
        <el-table-column prop="inbound_no" label="入库单号" width="170" />
        <el-table-column prop="supplier_name" label="供应商" min-width="160" show-overflow-tooltip />
        <el-table-column prop="warehouse_name" label="仓库" width="120" />
        <el-table-column label="金额" width="120" align="right">
          <template #default="{ row }">¥ {{ Number(row.total_amount || 0).toFixed(2) }}</template>
        </el-table-column>
        <el-table-column label="识别" width="90" align="center">
          <template #default="{ row }">
            <el-tag v-if="row.photo_path" type="primary" size="small">{{ row.ocr_status === 'recognized' ? '照片识别' : '有照片' }}</el-tag>
            <span v-else class="muted">手工</span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="90" align="center">
          <template #default="{ row }">
            <el-tag :type="statusMap[row.status]?.type" size="small">{{ statusMap[row.status]?.text }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="created_by" label="创建人" width="100" />
        <el-table-column label="入库日期" width="120">
          <template #default="{ row }">{{ String(row.receipt_date || '').slice(0, 10) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="280" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="openDetail(row)">详情</el-button>
            <el-button link type="primary" :loading="printingId === row.id" @click="printInbound(row)">打印 Word</el-button>
            <el-button v-if="row.status === 'draft'" v-perm="'inbound:manage'" link type="primary" @click="openEdit(row)">编辑</el-button>
            <el-button v-if="row.status === 'draft'" v-perm="'inbound:manage'" link type="success" @click="confirmInbound(row)">确认入库</el-button>
            <el-button v-if="row.status !== 'cancelled'" v-perm="'inbound:manage'" link type="warning" @click="cancelInbound(row)">取消</el-button>
            <el-button v-if="row.status === 'draft'" v-perm="'inbound:manage'" link type="danger" @click="remove(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-pagination v-model:current-page="page" :page-size="20" :total="total" layout="total, prev, pager, next" class="pager" @current-change="load" />
    </div>

    <el-dialog v-model="editorVisible" :title="editingId ? '编辑入库单' : '新建入库单'" width="920px" top="5vh">
      <div class="photo-row">
        <DocRecognize doc-type="inbound" @recognized="onRecognized" />
        <el-tag v-if="form.ocr_status === 'recognized'" type="success" size="small">已识别</el-tag>
        <span v-for="page in form.photo_paths" :key="page.path" class="photo-preview">
          <el-image :src="page.path" :preview-src-list="form.photo_paths.map((item) => item.path)" fit="cover" />
        </span>
        <span v-if="form.photo_path && !form.photo_paths.length" class="photo-preview">
          <el-image :src="form.photo_path" :preview-src-list="[form.photo_path]" fit="cover" />
        </span>
        <el-button v-if="form.photo_path" link type="danger" size="small" @click="form.photo_path = ''; form.photo_name = ''; form.photo_paths = []">
          移除照片
        </el-button>
      </div>

      <el-alert
        v-if="recognizeWarnings.length"
        class="recognize-warnings"
        type="warning"
        :closable="true"
        show-icon
        title="识别结果需要核对"
        @close="recognizeWarnings = []"
      >
        <p v-for="warning in recognizeWarnings" :key="warning" class="warning-line">{{ warning }}</p>
        <el-button size="small" type="primary" plain @click="adoptAll">按相似度补全未匹配行</el-button>
      </el-alert>

      <el-form label-width="80px">
        <div class="form-grid">
          <el-form-item label="供应商" required>
            <div class="field-with-action">
              <el-select v-model="form.supplier_id" filterable placeholder="选择供应商" style="width: 100%">
                <el-option v-for="s in suppliers" :key="s.id" :label="s.name" :value="s.id" />
              </el-select>
              <el-button link type="primary" @click="openQuickSupplier">新建</el-button>
            </div>
            <div v-if="recognizedPartner && !form.supplier_id" class="field-hint">
              识别为「{{ recognizedPartner.raw_name || '未识别' }}」，系统里没有匹配的供应商
            </div>
          </el-form-item>
          <el-form-item label="入库仓库" required>
            <el-select v-model="form.warehouse_id" placeholder="选择仓库" style="width: 100%">
              <el-option v-for="w in warehouses" :key="w.id" :label="`${w.name}（${w.code}）`" :value="w.id" />
            </el-select>
          </el-form-item>
          <el-form-item label="入库日期">
            <el-date-picker v-model="form.receipt_date" type="date" value-format="YYYY-MM-DD" style="width: 100%" />
          </el-form-item>
          <el-form-item label="备注">
            <el-input v-model="form.remark" placeholder="例如：随货同行单编号" />
          </el-form-item>
        </div>
      </el-form>

      <div class="items-head">
        <span>入库明细（{{ form.items.length }} 行，合计 ¥ {{ formTotal.toFixed(2) }}）</span>
        <div class="items-actions">
          <ScanInput
            product-type="raw_material"
            :placeholder="batchMode ? '连续扫码：扫完点「应用到明细」' : '扫码添加入库商品（原料）'"
            @select="onScan"
          />
          <el-switch v-model="batchMode" active-text="批量扫码" inline-prompt style="margin: 0 6px" />
          <el-button size="small" @click="addItem">添加一行</el-button>
        </div>

        <div v-if="batchMode && batchSummary.total" class="scan-batch">
          <span class="scan-batch-title">待写入：{{ batchSummary.types }} 种 / {{ batchSummary.total }} 件</span>
          <el-tag v-for="(row, pid) in scanBatch" :key="pid" size="small" effect="plain">
            {{ row.product.name }} × {{ row.count }}
          </el-tag>
          <el-button size="small" type="primary" @click="applyBatch">应用到明细</el-button>
          <el-button size="small" text @click="clearBatch">清空</el-button>
        </div>
      </div>
      <el-table :data="form.items" size="small" max-height="360">
        <el-table-column label="商品" min-width="240">
          <template #default="{ row }">
            <el-select
              v-model="row.product_id"
              filterable
              placeholder="选择商品"
              style="width: 100%"
              @change="markMatched(row)"
            >
              <el-option-group v-if="row.candidates?.length" label="识别建议">
                <el-option
                  v-for="candidate in row.candidates"
                  :key="`c-${candidate.product_id}`"
                  :label="`${candidate.name}（相似 ${candidate.score}）`"
                  :value="candidate.product_id"
                />
              </el-option-group>
              <el-option-group label="全部商品">
                <el-option v-for="p in products" :key="p.id" :label="`${p.sku} ${p.name}`" :value="p.id" />
              </el-option-group>
            </el-select>
            <div class="product-meta">
              <el-tag v-if="row.match_status" size="small" :type="matchTag(row).type">{{ matchTag(row).text }}</el-tag>
              <span v-if="row.raw_name" class="muted">{{ row.raw_name }}</span>
              <el-button v-if="!row.product_id" link type="primary" size="small" @click="openQuickProduct(row)">新建商品</el-button>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="数量" width="120">
          <template #default="{ row }">
            <el-input-number v-model="row.quantity" :min="1" size="small" controls-position="right" style="width: 100%" @change="recalc(row)" />
          </template>
        </el-table-column>
        <el-table-column label="单价" width="130">
          <template #default="{ row }">
            <el-input-number v-model="row.unit_price" :min="0" :precision="2" size="small" controls-position="right" style="width: 100%" @change="recalc(row)" />
          </template>
        </el-table-column>
        <el-table-column label="金额" width="100" align="right">
          <template #default="{ row }">¥ {{ amountOf(row).toFixed(2) }}</template>
        </el-table-column>
        <el-table-column label="批次号" width="120">
          <template #default="{ row }"><el-input v-model="row.batch_no" size="small" /></template>
        </el-table-column>
        <el-table-column label="效期" width="150">
          <template #default="{ row }">
            <el-date-picker
              v-model="row.expiry_date"
              type="date"
              value-format="YYYY-MM-DD"
              placeholder="选填"
              size="small"
              style="width: 100%"
            />
          </template>
        </el-table-column>
        <el-table-column label="备注" min-width="130">
          <template #default="{ row }"><el-input v-model="row.remark" size="small" /></template>
        </el-table-column>
        <el-table-column label="" width="60" align="center">
          <template #default="{ $index }">
            <el-button link type="danger" @click="removeItem($index)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <div v-if="form.ocr_raw" class="ocr-raw">
        <el-collapse>
          <el-collapse-item title="查看识别原文（用于核对）">
            <pre>{{ form.ocr_raw }}</pre>
          </el-collapse-item>
        </el-collapse>
      </div>

      <template #footer>
        <el-button @click="editorVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存草稿</el-button>
      </template>
    </el-dialog>

    <el-drawer v-model="detailVisible" :title="detail ? `${detail.inbound_no} · ${detail.supplier_name}` : '入库单详情'" size="760px">
      <div v-if="detail">
        <div class="detail-toolbar">
          <el-tag :type="statusMap[detail.status]?.type">{{ statusMap[detail.status]?.text }}</el-tag>
          <span class="muted">{{ detail.supplier_name }} · {{ detail.warehouse_name }} · {{ String(detail.receipt_date || '').slice(0, 10) }}</span>
          <div class="spacer" />
          <el-button size="small" @click="printInbound(detail)">打印</el-button>
          <el-button v-if="detail.status === 'draft'" v-perm="'inbound:manage'" size="small" type="success" @click="confirmInbound(detail)">确认入库</el-button>
        </div>

        <el-descriptions :column="3" border size="small" class="detail-meta">
          <el-descriptions-item label="入库单号">{{ detail.inbound_no }}</el-descriptions-item>
          <el-descriptions-item label="创建人">{{ detail.created_by }}</el-descriptions-item>
          <el-descriptions-item label="创建时间">{{ datetime(detail.created_at) }}</el-descriptions-item>
          <el-descriptions-item label="确认时间">{{ detail.confirmed_at ? datetime(detail.confirmed_at) : '—' }}</el-descriptions-item>
          <el-descriptions-item label="识别方式">{{ detail.ocr_status === 'recognized' ? '照片识别' : '手工录入' }}</el-descriptions-item>
          <el-descriptions-item label="合计金额">¥ {{ Number(detail.total_amount || 0).toFixed(2) }}</el-descriptions-item>
        </el-descriptions>

        <div v-if="detail.photo_path || detail.photo_paths?.length" class="detail-photo">
          <span class="muted">随货单据照片（点击可放大）</span>
          <div class="detail-photo-list">
            <el-image
              v-for="(page, index) in (detail.photo_paths?.length ? detail.photo_paths : [{ path: detail.photo_path }])"
              :key="index"
              :src="page.path"
              :preview-src-list="(detail.photo_paths?.length ? detail.photo_paths : [{ path: detail.photo_path }]).map((item) => item.path)"
              :initial-index="index"
              fit="cover"
            />
          </div>
        </div>

        <el-table :data="detail.items" size="small" max-height="360">
          <el-table-column prop="product_sku" label="SKU" width="110" />
          <el-table-column prop="product_name" label="商品" min-width="160" />
          <el-table-column prop="quantity" label="数量" width="70" align="right" />
          <el-table-column prop="unit_price" label="单价" width="90" align="right" />
          <el-table-column prop="amount" label="金额" width="100" align="right" />
          <el-table-column prop="batch_no" label="批次号" width="110" />
        </el-table>

        <div v-if="detail.remark" class="detail-remark">备注：{{ detail.remark }}</div>
      </div>
    </el-drawer>

    <PrintDialog v-model="printVisible" doc-type="inbound" :ids="printIds" title="打印入库单" />

    <QuickProductDialog
      v-model="quickProductVisible"
      :preset="quickProductPreset"
      product-type="raw_material"
      @created="onProductCreated"
    />
    <QuickPartnerDialog
      v-model="quickPartnerVisible"
      kind="supplier"
      :preset-name="quickPartnerName"
      @created="onSupplierCreated"
    />
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

.photo-row,
.items-head {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 10px;
}

.items-head {
  justify-content: space-between;
  font-size: 13px;
  font-weight: 600;
}

.items-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.scan-batch {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  margin: 8px 0;
  padding: 8px 10px;
  border-radius: 8px;
  background: #eff6ff;
  border: 1px solid #bfdbfe;
}

.scan-batch-title {
  font-size: 13px;
  font-weight: 600;
  color: #1d4ed8;
}

.photo-preview {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}

.photo-preview .el-image {
  width: 56px;
  height: 56px;
  border-radius: 4px;
  border: 1px solid var(--border-color);
}

.field-with-action {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
}

.field-hint {
  margin-top: 4px;
  font-size: 12px;
  color: #b45309;
}

.product-meta {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 4px;
  flex-wrap: wrap;
}

.recognize-warnings {
  margin-bottom: 10px;
}

.warning-line {
  margin: 2px 0;
  font-size: 12px;
}

.detail-photo-list {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.form-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  column-gap: 18px;
}

.ocr-raw pre {
  white-space: pre-wrap;
  word-break: break-all;
  font-size: 12px;
  color: var(--text-secondary);
  margin: 0;
}

.detail-toolbar {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 12px;
}

.spacer {
  flex: 1;
}

.detail-meta {
  margin-bottom: 12px;
}

.detail-photo {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-bottom: 12px;
}

.detail-photo .el-image {
  width: 220px;
  height: 150px;
  border-radius: 4px;
  border: 1px solid var(--border-color);
}

.detail-remark {
  margin-top: 12px;
  color: var(--text-secondary);
}
</style>


