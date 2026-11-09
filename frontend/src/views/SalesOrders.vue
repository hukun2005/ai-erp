<script setup>
import { computed, nextTick, onMounted, reactive, ref } from 'vue'
import { useRouteKeyword } from '../utils/routeQuery'
import { useQuickCreate } from '../utils/quickCreate'
import { ElMessage, ElMessageBox } from 'element-plus'
import { creditAdminApi, customerApi, outboundApi, productApi, salesApi } from '../api'
import DocRecognize from '../components/DocRecognize.vue'
import PrintDialog from '../components/PrintDialog.vue'
import QuickPartnerDialog from '../components/QuickPartnerDialog.vue'
import QuickProductDialog from '../components/QuickProductDialog.vue'
import { saveBlob } from '../utils/download'
import {
  adoptCandidates,
  itemPayload,
  mapRecognizedItems,
  markMatched,
  matchTag,
  photoFields,
} from '../utils/recognize'
import { useUserStore } from '../stores/user'

const userStore = useUserStore()

const loading = ref(false)
const list = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const query = reactive({ keyword: '', status: '', customer_id: null, sort: 'created_desc' })
// 日期与金额区间单独存：daterange 是数组，直接塞进 query 会污染请求参数
const dateRange = ref(null)
const amountRange = reactive({ min: null, max: null })
const amountSum = ref(0)
const sortOptions = [
  { value: 'created_desc', label: '创建时间（新→旧）' },
  { value: 'date_desc', label: '下单日期（新→旧）' },
  { value: 'date_asc', label: '下单日期（旧→新）' },
  { value: 'amount_desc', label: '金额（高→低）' },
  { value: 'amount_asc', label: '金额（低→高）' },
]
const route = useRouteKeyword(query, () => {
  page.value = 1
  load()
})
if (typeof route.query.status === 'string') query.status = route.query.status

const customers = ref([])
const products = ref([])

const dialogVisible = ref(false)
const detailVisible = ref(false)
const detailLoading = ref(false)
const printingId = ref(null)
const current = ref(null)
const approvalLogs = ref([])
const formRef = ref()
const editingId = ref(null)
const originalDate = ref('')
const submitting = ref(false)
const busyId = ref(null)
const tableRef = ref()
const selection = ref([])
const batchBusy = ref(false)

const dialogTitle = computed(() => (editingId.value ? '编辑销售订单' : '新建销售订单'))

function todayString() {
  const d = new Date()
  const pad = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

const blankOrder = () => ({
  customer_id: null,
  order_date: todayString(),
  discount_amount: 0,
  tax_rate: 0,
  remark: '',
  photo_path: '',
  photo_name: '',
  photo_paths: [],
  ocr_status: 'manual',
  ocr_raw: '',
  raw_customer_name: '',
  items: [{ product_id: null, quantity: 1, unit_price: 0 }],
})
const form = reactive(blankOrder())

const recognizeWarnings = ref([])
const recognizedPartner = ref(null)
const quickProductVisible = ref(false)
const quickProductPreset = ref({})
const quickProductTarget = ref(null)
const quickPartnerVisible = ref(false)

const rules = {
  customer_id: [{ required: true, message: '请选择客户', trigger: 'change' }],
}

const statusMap = {
  draft: { text: '草稿', type: 'info' },
  pending: { text: '待审批', type: 'warning' },
  confirmed: { text: '已确认', type: 'primary' },
  completed: { text: '已完成', type: 'success' },
  cancelled: { text: '已取消', type: 'danger' },
}

const statusFilters = [
  { label: '全部', value: '' },
  ...Object.entries(statusMap).map(([value, item]) => ({ label: item.text, value })),
]

const hasFilter = computed(
  () =>
    !!query.keyword.trim() ||
    !!query.status ||
    !!query.customer_id ||
    !!dateRange.value?.length ||
    amountRange.min != null ||
    amountRange.max != null,
)
const canManage = computed(() => userStore.hasPerm('sales:manage'))
const canApprove = computed(() => userStore.hasPerm('sales:approve'))

const grossAmount = computed(() =>
  form.items.reduce((sum, i) => sum + (i.quantity || 0) * (i.unit_price || 0), 0),
)
const totalAmount = computed(() => Math.max(grossAmount.value - (form.discount_amount || 0), 0))
const detailGross = computed(() => (current.value?.items || []).reduce((sum, i) => sum + (i.amount || 0), 0))
const pageTotal = computed(() => list.value.reduce((sum, row) => sum + (row.total_amount || 0), 0))
const dispatching = ref(false)
const requestingOverride = ref(false)
const printVisible = ref(false)
const printIds = ref([])

function openPrint(ids) {
  const list = (Array.isArray(ids) ? ids : [ids]).filter(Boolean)
  if (!list.length) {
    ElMessage.warning('没有可打印的订单')
    return
  }
  printIds.value = list
  printVisible.value = true
}
// 信用特批：已提交过申请就只展示状态，避免重复申请
const canRequestOverride = computed(() => {
  const row = current.value
  if (!row || !canManage.value) return false
  if (!['draft', 'pending', 'confirmed'].includes(row.status)) return false
  return !row.credit_override
})
const overrideStatusText = computed(() => {
  const status = current.value?.credit_override?.status
  return { pending: '待审批', approved: '已通过', rejected: '已驳回' }[status] || ''
})
// 有未发数量且订单已确认时，才允许生成出库单
const canDispatch = computed(() => {
  const row = current.value
  if (!row || !canManage.value) return false
  if (!['confirmed', 'completed'].includes(row.status)) return false
  if (row.shipping_mode !== 'outbound') return false
  return (row.shipment?.unshipped_quantity || 0) > 0
})
const drawerActionInfo = computed(() => {
  const row = current.value
  if (!row) return null
  if (row.status === 'draft' && canManage.value) return { key: 'submit', label: '提交审批', type: 'success' }
  if (row.status === 'pending' && canApprove.value) return { key: 'approve', label: '审批通过', type: 'success' }
  if (row.status === 'confirmed' && canManage.value) return { key: 'complete', label: '完成订单', type: 'primary' }
  return null
})
const stepActive = computed(() => {
  const status = current.value?.status
  if (status === 'completed') return 4
  return { draft: 0, pending: 1, confirmed: 2 }[status] ?? 0
})

function formatTime(value) {
  return value ? String(value).replace('T', ' ').slice(0, 16) : '—'
}

// 列表里用“今天 / 昨天”降低阅读成本，完整时间放在悬浮提示里
function smartTime(value) {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return formatTime(value)
  const now = new Date()
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  const target = new Date(date.getFullYear(), date.getMonth(), date.getDate())
  const diff = Math.round((today - target) / 86400000)
  const hm = `${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`
  if (diff === 0) return `今天 ${hm}`
  if (diff === 1) return `昨天 ${hm}`
  const md = `${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`
  return `${md} ${hm}`
}

function money(value) {
  return '¥' + Number(value || 0).toLocaleString('zh-CN', { minimumFractionDigits: 2 })
}

function itemSummary(row) {
  const items = row.items || []
  const qty = items.reduce((sum, i) => sum + (i.quantity || 0), 0)
  return `${items.length} 种 · ${qty} 件`
}

function stockOf(productId) {
  const product = products.value.find((p) => p.id === productId)
  return product ? product.stock_qty : null
}

function isShort(item) {
  const stock = stockOf(item.product_id)
  return stock !== null && item.quantity > stock
}

async function load() {
  loading.value = true
  try {
    const data = await salesApi.list({
      ...query,
      customer_id: query.customer_id || undefined,
      start_date: dateRange.value?.[0] || '',
      end_date: dateRange.value?.[1] || '',
      amount_min: amountRange.min ?? undefined,
      amount_max: amountRange.max ?? undefined,
      page: page.value,
      page_size: pageSize.value,
    })
    list.value = data.items
    total.value = data.total
    // 台账口径：当前筛选条件下的总额由后端算，不受分页影响
    amountSum.value = data.amount_sum || 0
  } finally {
    loading.value = false
    selection.value = []
    tableRef.value?.clearSelection()
  }
}

function onSelectionChange(rows) {
  selection.value = rows
}

function clearSelection() {
  selection.value = []
  tableRef.value?.clearSelection()
}

function selectableRow(row) {
  return canManage.value && row.status === 'draft'
}

async function batchAction(action) {
  if (!selection.value.length || batchBusy.value) return
  const rows = [...selection.value]
  const label = action === 'submit' ? '提交审批' : '删除'
  if (!(await confirmAction(`确定批量${label}选中的 ${rows.length} 张草稿订单吗？`, `批量${label}`))) return

  batchBusy.value = true
  let success = 0
  for (const row of rows) {
    try {
      if (action === 'submit') await salesApi.changeStatus(row.id, 'submit')
      else await salesApi.remove(row.id)
      success += 1
    } catch {
      // 单条失败不阻断其余订单
    }
  }
  batchBusy.value = false
  ElMessage[success === rows.length ? 'success' : 'warning'](`已${label} ${success} / ${rows.length} 张订单`)
  clearSelection()
  load()
}

function search() {
  page.value = 1
  load()
}

function setStatus(value) {
  if (query.status === value) return
  query.status = value
  search()
}

function resetQuery() {
  query.keyword = ''
  query.status = ''
  query.customer_id = null
  query.sort = 'created_desc'
  dateRange.value = null
  amountRange.min = null
  amountRange.max = null
  search()
}

function onSizeChange(size) {
  pageSize.value = size
  page.value = 1
  load()
}

function onProductChange(row) {
  const product = products.value.find((p) => p.id === row.product_id)
  if (product) row.unit_price = product.sale_price
  markMatched(row)
}

function addItem() {
  form.items.push({ product_id: null, quantity: 1, unit_price: 0 })
}

function removeItem(index) {
  if (form.items.length === 1) return
  form.items.splice(index, 1)
}

function onRecognized(result) {
  Object.assign(form, photoFields(result))
  form.raw_customer_name = result.partner?.raw_name || ''
  recognizedPartner.value = result.partner || null
  if (result.customer_id) form.customer_id = result.customer_id
  if (result.order_date) form.order_date = result.order_date
  form.items = mapRecognizedItems(result.items)
  recognizeWarnings.value = result.warnings || []
  if (!form.items.length) {
    ElMessage.warning('未识别到商品行，请手动添加或换一张更清晰的照片')
    return
  }
  const seconds = ((result.elapsed_ms || 0) / 1000).toFixed(1)
  ElMessage.success(`识别完成，共 ${form.items.length} 行，用时 ${seconds} 秒，请核对后保存`)
  nextTick(() => formRef.value?.clearValidate())
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
  if (quickProductTarget.value) {
    quickProductTarget.value.product_id = product.id
    markMatched(quickProductTarget.value)
  }
  quickProductTarget.value = null
}

function onCustomerCreated(customer) {
  customers.value.push(customer)
  form.customer_id = customer.id
  nextTick(() => formRef.value?.clearValidate())
}

// 顶栏「新建」与看板快捷入口带 ?new=1 时直接打开开单弹窗
useQuickCreate(openCreate)

function openCreate() {
  editingId.value = null
  originalDate.value = ''
  Object.assign(form, blankOrder())
  recognizeWarnings.value = []
  recognizedPartner.value = null
  dialogVisible.value = true
  nextTick(() => formRef.value?.clearValidate())
}

function openEdit(row) {
  editingId.value = row.id
  originalDate.value = String(row.order_date || '').slice(0, 10)
  Object.assign(form, {
    customer_id: row.customer_id,
    order_date: originalDate.value || todayString(),
    discount_amount: row.discount_amount || 0,
    tax_rate: row.tax_rate || 0,
    remark: row.remark || '',
    photo_path: row.photo_path || '',
    photo_name: row.photo_name || '',
    photo_paths: row.photo_paths || [],
    ocr_status: row.ocr_status || 'manual',
    ocr_raw: row.ocr_raw || '',
    raw_customer_name: '',
    items: (row.items || []).map((item) => ({
      product_id: item.product_id,
      quantity: item.quantity,
      unit_price: item.unit_price,
    })),
  })
  recognizeWarnings.value = []
  recognizedPartner.value = null
  if (!form.items.length) form.items = [{ product_id: null, quantity: 1, unit_price: 0 }]
  dialogVisible.value = true
  nextTick(() => formRef.value?.clearValidate())
}

// 以现有订单为模板快速开单，减少重复录入
function copyOrder(row) {
  editingId.value = null
  originalDate.value = ''
  Object.assign(form, {
    customer_id: row.customer_id,
    order_date: todayString(),
    discount_amount: row.discount_amount || 0,
    tax_rate: row.tax_rate || 0,
    remark: row.remark || '',
    photo_path: '',
    photo_name: '',
    photo_paths: [],
    ocr_status: 'manual',
    ocr_raw: '',
    raw_customer_name: '',
    items: (row.items || []).map((item) => ({
      product_id: item.product_id,
      quantity: item.quantity,
      unit_price: item.unit_price,
    })),
  })
  recognizeWarnings.value = []
  recognizedPartner.value = null
  if (!form.items.length) form.items = [{ product_id: null, quantity: 1, unit_price: 0 }]
  dialogVisible.value = true
  nextTick(() => formRef.value?.clearValidate())
  ElMessage.info('已复制原单内容，保存后生成新草稿')
}

async function submit() {
  if (submitting.value) return
  try {
    await formRef.value.validate()
  } catch {
    return
  }
  if (!form.items.length || form.items.some((i) => !i.product_id || !i.quantity || i.quantity <= 0)) {
    ElMessage.warning('请完整填写订单明细')
    return
  }

  const payload = {
    customer_id: form.customer_id,
    discount_amount: form.discount_amount || 0,
    tax_rate: form.tax_rate || 0,
    remark: form.remark,
    photo_path: form.photo_path,
    photo_name: form.photo_name,
    photo_paths: form.photo_paths,
    ocr_status: form.ocr_status,
    ocr_raw: form.ocr_raw,
    raw_customer_name: form.raw_customer_name,
    items: form.items.map((item) => itemPayload(item)),
  }
  // 下单日期：新建时若为今天则不传，由后端取当前时间；编辑时仅在日期被改动时才传
  if (editingId.value) {
    if (form.order_date && form.order_date !== originalDate.value) payload.order_date = form.order_date
  } else if (form.order_date && form.order_date !== todayString()) {
    payload.order_date = form.order_date
  }

  submitting.value = true
  try {
    if (editingId.value) {
      await salesApi.update(editingId.value, payload)
      ElMessage.success('草稿订单已更新')
    } else {
      await salesApi.create(payload)
      ElMessage.success('订单已创建（草稿），确认后才会扣减库存')
    }
    dialogVisible.value = false
    editingId.value = null
    load()
  } catch {
    // 错误提示由请求拦截器统一处理
  } finally {
    submitting.value = false
  }
}

// 打印输出真实 .docx：有客户上传的初始格式就套用，没有则用系统内置版式
async function printOrder(row) {
  if (printingId.value) return
  printingId.value = row.id
  try {
    const blob = await salesApi.printDocx(row.id)
    saveBlob(blob, `销售订单_${row.order_no}.docx`)
    ElMessage.success('Word 单据已生成，用 Word 打开即可打印')
  } finally {
    printingId.value = null
  }
}

async function printDelivery(row) {
  if (printingId.value) return
  printingId.value = row.id
  try {
    const blob = await salesApi.deliveryDocx(row.id)
    saveBlob(blob, `送货单_${row.order_no}.docx`)
    ElMessage.success('送货单已生成，用 Word 打开即可打印')
  } finally {
    printingId.value = null
  }
}

async function requestCreditOverride(row) {
  if (!row || requestingOverride.value) return
  let reason = ''
  try {
    const result = await ElMessageBox.prompt('请说明超额原因，审批人会在待办里看到', '申请信用特批', {
      inputPlaceholder: '例如：老客户急单，月底回款',
      confirmButtonText: '提交申请',
      cancelButtonText: '取消',
    })
    reason = result.value || ''
  } catch {
    return
  }
  requestingOverride.value = true
  try {
    await creditAdminApi.requestOverride(row.id, reason)
    ElMessage.success('特批申请已提交，审批通过后即可确认出库')
    current.value = await salesApi.detail(row.id)
  } finally {
    requestingOverride.value = false
  }
}

async function createOutbound(row) {
  if (!row || dispatching.value) return
  dispatching.value = true
  try {
    const outbound = await outboundApi.create({ sales_order_id: row.id })
    ElMessage.success(`已生成出库单 ${outbound.outbound_no}，到「销售出库」确认出库后扣库存`)
    await load()
    current.value = await salesApi.detail(row.id)
  } finally {
    dispatching.value = false
  }
}

async function viewDetail(row) {
  detailLoading.value = true
  detailVisible.value = true
  current.value = null
  approvalLogs.value = []
  try {
    const [detail, logs] = await Promise.all([salesApi.detail(row.id), salesApi.approvals(row.id)])
    current.value = detail
    approvalLogs.value = logs
  } finally {
    detailLoading.value = false
  }
}

async function confirmAction(message, title) {
  try {
    await ElMessageBox.confirm(message, title, { type: 'warning', confirmButtonText: '确定' })
    return true
  } catch {
    return false
  }
}

async function perform(row, action) {
  if (action === 'submit') {
    if (!(await confirmAction(`提交后订单进入待审批，确定提交 ${row.order_no} 吗？`, '提交审批'))) return
    await salesApi.changeStatus(row.id, 'submit')
    ElMessage.success('已提交审批')
    return
  }

  if (action === 'approve') {
    if (!(await confirmAction(`审批通过后订单将立即扣减库存，确定通过 ${row.order_no} 吗？`, '审批通过'))) return
    const data = await salesApi.changeStatus(row.id, 'approve')
    ElMessage.success('订单已审批通过并扣减库存')
    if (data?.auto_alert?.sent) ElMessage.success(data.auto_alert.message)
    return
  }

  if (action === 'reject') {
    let reason = ''
    try {
      const result = await ElMessageBox.prompt('请填写驳回原因，订单会退回草稿', `驳回 ${row.order_no}`, {
        inputValidator: (val) => (val && val.trim() ? true : '请填写驳回原因'),
      })
      reason = result.value
    } catch {
      return
    }
    await salesApi.changeStatus(row.id, 'reject', reason)
    ElMessage.success('订单已驳回')
    return
  }

  if (action === 'complete') {
    if (!(await confirmAction(`确定将 ${row.order_no} 标记为已完成吗？`, '完成订单'))) return
    await salesApi.changeStatus(row.id, 'complete')
    ElMessage.success('订单已完成')
    return
  }

  if (action === 'cancel') {
    const tip =
      row.status === 'confirmed' || row.status === 'completed'
        ? `取消后订单商品会自动回库，确定取消 ${row.order_no} 吗？`
        : `确定取消草稿订单 ${row.order_no} 吗？`
    if (!(await confirmAction(tip, '取消订单'))) return
    await salesApi.changeStatus(row.id, 'cancel')
    ElMessage.success('订单已取消')
    return
  }

  if (action === 'remove') {
    if (!(await confirmAction(`删除后不可恢复，确定删除草稿订单 ${row.order_no} 吗？`, '删除订单'))) return
    await salesApi.remove(row.id)
    ElMessage.success('已删除')
  }
}

async function runAction(row, action) {
  if (busyId.value) return
  if (action === 'print') return printOrder(row)
  if (action === 'delivery') return printDelivery(row)
  if (action === 'view') return viewDetail(row)
  if (action === 'edit') return openEdit(row)
  if (action === 'copy') return copyOrder(row)

  busyId.value = row.id
  try {
    await perform(row, action)
    await load()
  } catch {
    // 错误提示由请求拦截器统一处理
  } finally {
    busyId.value = null
  }
}

// 详情抽屉内直接推进单据状态，无需回到列表
async function drawerAction() {
  const row = current.value
  const info = drawerActionInfo.value
  if (!row || !info || busyId.value) return
  busyId.value = row.id
  try {
    await perform(row, info.key)
    const [detail, logs] = await Promise.all([salesApi.detail(row.id), salesApi.approvals(row.id)])
    current.value = detail
    approvalLogs.value = logs
    load()
  } catch {
    // 错误提示由请求拦截器统一处理
  } finally {
    busyId.value = null
  }
}

function primaryAction(row) {
  if (row.status === 'draft' && canManage.value) return { key: 'submit', label: '提交审批', type: 'success' }
  if (row.status === 'pending' && canApprove.value) return { key: 'approve', label: '审批通过', type: 'success' }
  if (row.status === 'confirmed' && canManage.value) return { key: 'complete', label: '完成订单', type: 'primary' }
  return null
}

function moreActions(row) {
  const actions = []
  if (row.status === 'draft' && canManage.value) actions.push({ key: 'edit', label: '编辑草稿' })
  if (row.status === 'pending' && canApprove.value) actions.push({ key: 'reject', label: '驳回订单' })
  if (row.status !== 'cancelled' && canManage.value) actions.push({ key: 'copy', label: '复制为新订单' })
  if (row.status !== 'cancelled' && canManage.value) actions.push({ key: 'cancel', label: '取消订单', danger: true })
  if (row.status === 'draft' && canManage.value) actions.push({ key: 'remove', label: '删除订单', danger: true })
  actions.push({ key: 'print', label: '打印 Word 单据' })
  actions.push({ key: 'delivery', label: '打印送货单' })
  return actions
}

onMounted(async () => {
  load()
  try {
    const [c, p] = await Promise.all([
      customerApi.all(),
      productApi.list({ page_size: 200, product_type: 'finished_good' }),
    ])
    customers.value = c
    products.value = p.items
  } catch {
    // 基础数据加载失败不阻塞列表本身
  }
})
</script>

<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h1 class="page-title">销售订单</h1>
        <p class="page-subtitle">订单先提交审批，审批通过才扣减库存；取消已确认订单会自动回滚库存</p>
        <el-tag v-if="userStore.dataScope === 'own'" size="small" type="warning" effect="plain" class="scope-tag">
          数据范围：仅显示本人创建的订单
        </el-tag>
      </div>
      <el-button v-perm="'sales:manage'" type="primary" @click="openCreate">
        <el-icon><Plus /></el-icon>&nbsp;新建销售订单
      </el-button>
    </div>

    <div class="card">
      <div class="status-tabs">
        <button
          v-for="item in statusFilters"
          :key="item.value || 'all'"
          type="button"
          class="status-chip"
          :class="{ active: query.status === item.value }"
          @click="setStatus(item.value)"
        >
          {{ item.label }}
        </button>
      </div>

      <div v-if="selection.length" class="batch-bar">
        <span class="batch-text">已选 <b>{{ selection.length }}</b> 张草稿订单</span>
        <el-button size="small" type="success" plain :loading="batchBusy" @click="batchAction('submit')">
          批量提交审批
        </el-button>
        <el-button size="small" type="danger" plain :loading="batchBusy" @click="batchAction('delete')">
          批量删除
        </el-button>
        <el-button size="small" text @click="clearSelection">取消选择</el-button>
      </div>

      <div class="toolbar">
        <el-input
          v-model="query.keyword"
          placeholder="搜索订单号 / 客户"
          clearable
          class="search-input"
          @keyup.enter="search"
          @clear="search"
        >
          <template #prefix><el-icon><Search /></el-icon></template>
        </el-input>
        <el-button type="primary" plain @click="search">查询</el-button>
        <el-button v-if="hasFilter" text @click="resetQuery">重置</el-button>
        <el-button plain @click="openPrint(list.map((row) => row.id))">打印本页 PDF</el-button>
        <span class="toolbar-count">共 {{ total }} 张订单 · 合计 ¥{{ money(amountSum) }}</span>
      </div>

      <div class="filter-bar">
        <el-select
          v-model="query.customer_id"
          placeholder="全部客户"
          clearable
          filterable
          style="width: 180px"
          @change="search"
        >
          <el-option v-for="c in customers" :key="c.id" :label="c.name" :value="c.id" />
        </el-select>
        <el-date-picker
          v-model="dateRange"
          type="daterange"
          value-format="YYYY-MM-DD"
          start-placeholder="下单开始日"
          end-placeholder="下单结束日"
          style="width: 250px"
          @change="search"
        />
        <el-input-number
          v-model="amountRange.min"
          :min="0"
          :controls="false"
          placeholder="金额下限"
          style="width: 118px"
          @change="search"
        />
        <span class="filter-dash">~</span>
        <el-input-number
          v-model="amountRange.max"
          :min="0"
          :controls="false"
          placeholder="金额上限"
          style="width: 118px"
          @change="search"
        />
        <el-select v-model="query.sort" style="width: 176px" @change="search">
          <el-option v-for="option in sortOptions" :key="option.value" :label="option.label" :value="option.value" />
        </el-select>
      </div>

      <el-table
        ref="tableRef"
        :data="list"
        v-loading="loading"
        row-key="id"
        @selection-change="onSelectionChange"
      >
        <el-table-column v-if="canManage" type="selection" width="42" :selectable="selectableRow" />
        <el-table-column label="订单号" width="170">
          <template #default="{ row }">
            <button type="button" class="order-link" @click="runAction(row, 'view')">{{ row.order_no }}</button>
          </template>
        </el-table-column>
        <el-table-column prop="customer_name" label="客户" min-width="150" show-overflow-tooltip />
        <el-table-column label="下单时间" width="150">
          <template #default="{ row }">
            <span class="muted" :title="formatTime(row.order_date)">{{ smartTime(row.order_date) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="明细" width="115" align="center">
          <template #default="{ row }">
            <span class="muted">{{ itemSummary(row) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="金额" width="130" align="right">
          <template #default="{ row }"><b class="money amount">{{ money(row.total_amount) }}</b></template>
        </el-table-column>
        <el-table-column label="状态" width="96" align="center">
          <template #default="{ row }">
            <el-tag :type="statusMap[row.status]?.type" effect="light" size="small" round>
              {{ statusMap[row.status]?.text }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="created_by" label="创建人" width="96" show-overflow-tooltip />
        <el-table-column label="操作" width="212" fixed="right">
          <template #default="{ row }">
            <div class="row-actions">
              <el-button link type="primary" @click="runAction(row, 'view')">详情</el-button>
              <el-button
                v-if="primaryAction(row)"
                link
                :type="primaryAction(row).type"
                :loading="busyId === row.id"
                @click="runAction(row, primaryAction(row).key)"
              >
                {{ primaryAction(row).label }}
              </el-button>
              <el-dropdown trigger="click" @command="(cmd) => runAction(row, cmd)">
                <el-button link type="info" :loading="busyId === row.id">
                  更多<el-icon class="el-icon--right"><ArrowDown /></el-icon>
                </el-button>
                <template #dropdown>
                  <el-dropdown-menu>
                    <el-dropdown-item
                      v-for="item in moreActions(row)"
                      :key="item.key"
                      :command="item.key"
                      :style="item.danger ? { color: 'var(--danger)' } : {}"
                    >
                      {{ item.label }}
                    </el-dropdown-item>
                  </el-dropdown-menu>
                </template>
              </el-dropdown>
            </div>
          </template>
        </el-table-column>

        <template #empty>
          <div class="table-empty">
            <div class="empty-icon"><el-icon><Document /></el-icon></div>
            <p>{{ hasFilter ? '没有符合筛选条件的订单' : '还没有销售订单' }}</p>
            <el-button v-if="!hasFilter" v-perm="'sales:manage'" type="primary" plain size="small" @click="openCreate">
              新建第一张订单
            </el-button>
          </div>
        </template>
      </el-table>

      <div class="pager">
        <span class="page-total">本页合计 <b class="money">{{ money(pageTotal) }}</b></span>
        <el-pagination
          v-model:current-page="page"
          v-model:page-size="pageSize"
          :page-sizes="[10, 20, 50, 100]"
          :total="total"
          layout="total, sizes, prev, pager, next"
          @current-change="load"
          @size-change="onSizeChange"
        />
      </div>
    </div>

    <!-- 新建 / 编辑订单 -->
    <el-dialog v-model="dialogVisible" :title="dialogTitle" width="880px" top="5vh">
      <div class="recognize-row">
        <DocRecognize doc-type="sales" @recognized="onRecognized" />
        <span v-if="form.ocr_status === 'recognized'" class="recognized-tag">已识别 {{ form.photo_paths.length || 1 }} 页</span>
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

      <el-form ref="formRef" :model="form" :rules="rules" label-width="84px">
        <el-row :gutter="14">
          <el-col :span="10">
            <el-form-item label="客户" prop="customer_id">
              <div class="field-with-action">
                <el-select v-model="form.customer_id" filterable placeholder="选择客户" style="width: 100%">
                  <el-option v-for="c in customers" :key="c.id" :label="c.name" :value="c.id" />
                </el-select>
                <el-button link type="primary" @click="quickPartnerVisible = true">新建</el-button>
              </div>
              <div v-if="recognizedPartner && !form.customer_id" class="field-hint">
                识别为「{{ recognizedPartner.raw_name || '未识别' }}」，系统里没有匹配的客户
              </div>
            </el-form-item>
          </el-col>
          <el-col :span="5">
            <el-form-item label="下单日期">
              <el-date-picker v-model="form.order_date" type="date" value-format="YYYY-MM-DD" placeholder="默认今天" style="width: 100%" />
            </el-form-item>
          </el-col>
          <el-col :span="5">
            <el-form-item label="优惠金额">
              <el-input-number v-model="form.discount_amount" :min="0" :precision="2" :controls="false" style="width: 100%" />
            </el-form-item>
          </el-col>
          <el-col :span="4">
            <el-form-item label="税率%">
              <el-input-number v-model="form.tax_rate" :min="0" :max="100" :precision="2" :controls="false" placeholder="0 不含税" style="width: 100%" />
            </el-form-item>
          </el-col>
        </el-row>

        <div class="items-section">
          <div class="items-title">
            <span>订单明细</span>
            <span class="items-count">{{ form.items.length }} 种商品</span>
          </div>

          <div class="item-grid-head">
            <span>商品</span>
            <span>数量（件）</span>
            <span>单价（元）</span>
            <span class="align-right">小计（元）</span>
            <span></span>
          </div>

          <div v-for="(item, index) in form.items" :key="index" class="item-grid-row">
            <div class="product-cell">
              <el-select v-model="item.product_id" filterable placeholder="选择商品" @change="onProductChange(item)">
                <el-option-group v-if="item.candidates?.length" label="识别建议">
                  <el-option
                    v-for="candidate in item.candidates"
                    :key="`c-${candidate.product_id}`"
                    :label="`${candidate.name}（相似 ${candidate.score}）`"
                    :value="candidate.product_id"
                  />
                </el-option-group>
                <el-option-group label="全部商品">
                  <el-option
                    v-for="p in products"
                    :key="p.id"
                    :label="`${p.name}（库存 ${p.stock_qty}）`"
                    :value="p.id"
                  />
                </el-option-group>
              </el-select>
              <span v-if="isShort(item)" class="stock-warn">库存不足，当前库存 {{ stockOf(item.product_id) }}</span>
              <div v-if="item.match_status || item.raw_name" class="item-hint">
                <el-tag size="small" :type="matchTag(item).type">{{ matchTag(item).text }}</el-tag>
                <span v-if="item.raw_name" class="muted">识别为：{{ item.raw_name }}</span>
                <el-button v-if="!item.product_id" link type="primary" size="small" @click="openQuickProduct(item)">新建商品</el-button>
              </div>
            </div>
            <el-input-number v-model="item.quantity" :min="1" :controls="false" />
            <el-input-number v-model="item.unit_price" :min="0" :precision="2" :controls="false" />
            <span class="align-right money">{{ money((item.quantity || 0) * (item.unit_price || 0)) }}</span>
            <el-button link type="danger" :disabled="form.items.length === 1" @click="removeItem(index)">
              <el-icon><Delete /></el-icon>
            </el-button>
          </div>

          <el-button link type="primary" class="add-item" @click="addItem">
            <el-icon><Plus /></el-icon>&nbsp;添加一行明细
          </el-button>
        </div>

        <el-form-item label="备注">
          <el-input v-model="form.remark" type="textarea" :rows="2" placeholder="选填，例如发货要求、交付说明" />
        </el-form-item>

        <div class="summary-strip">
          <div class="summary-item">
            <span>商品金额</span>
            <b class="money">{{ money(grossAmount) }}</b>
          </div>
          <div class="summary-item">
            <span>优惠</span>
            <b class="money discount">-{{ money(form.discount_amount || 0) }}</b>
          </div>
          <div class="summary-item total">
            <span>应收合计</span>
            <b class="money">{{ money(totalAmount) }}</b>
          </div>
        </div>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="submit">
          {{ editingId ? '保存修改' : '保存为草稿' }}
        </el-button>
      </template>
    </el-dialog>

    <!-- 订单详情 -->
    <el-drawer v-model="detailVisible" title="订单详情" size="720px">
      <div v-loading="detailLoading" class="detail">
        <div v-if="current">
          <div class="detail-head">
            <div>
              <h3>{{ current.order_no }}</h3>
              <p class="customer">{{ current.customer_name }}</p>
              <p class="muted">下单 {{ formatTime(current.order_date) }} · 创建人 {{ current.created_by }}</p>
            </div>
            <el-tag :type="statusMap[current.status]?.type" effect="light" round>
              {{ statusMap[current.status]?.text }}
            </el-tag>
          </div>

          <h4 class="detail-section">单据信息</h4>
          <el-descriptions class="detail-desc" :column="2" size="small" border>
            <el-descriptions-item label="订单编号">{{ current.order_no }}</el-descriptions-item>
            <el-descriptions-item label="单据状态">{{ statusMap[current.status]?.text || current.status }}</el-descriptions-item>
            <el-descriptions-item label="下单时间">{{ formatTime(current.order_date) }}</el-descriptions-item>
            <el-descriptions-item label="创建时间">{{ formatTime(current.created_at) }}</el-descriptions-item>
            <el-descriptions-item label="创建人">{{ current.created_by || '—' }}</el-descriptions-item>
            <el-descriptions-item label="明细行数">{{ current.items?.length || 0 }} 行</el-descriptions-item>
          </el-descriptions>

          <h4 class="detail-section">客户信息</h4>
          <el-descriptions class="detail-desc" :column="2" size="small" border>
            <el-descriptions-item label="客户名称">{{ current.customer_name || '—' }}</el-descriptions-item>
            <el-descriptions-item label="联系人">{{ current.customer_contact || '—' }}</el-descriptions-item>
            <el-descriptions-item label="联系电话">{{ current.customer_phone || '—' }}</el-descriptions-item>
            <el-descriptions-item label="电子邮箱">{{ current.customer_email || '—' }}</el-descriptions-item>
            <el-descriptions-item label="客户等级">{{ current.customer_level || '—' }}</el-descriptions-item>
            <el-descriptions-item label="信用额度">{{ money(current.customer_credit_limit) }}</el-descriptions-item>
            <el-descriptions-item label="客户地址" :span="2">{{ current.customer_address || '—' }}</el-descriptions-item>
          </el-descriptions>

          <div v-if="current.photo_path || current.photo_paths?.length" class="detail-photo">
            <span class="muted">订单照片（点击可放大）</span>
            <div class="detail-photo-list">
              <el-image
                v-for="(page, index) in (current.photo_paths?.length ? current.photo_paths : [{ path: current.photo_path }])"
                :key="index"
                :src="page.path"
                :preview-src-list="(current.photo_paths?.length ? current.photo_paths : [{ path: current.photo_path }]).map((item) => item.path)"
                :initial-index="index"
                fit="cover"
              />
            </div>
          </div>

          <h4 class="detail-section">金额汇总</h4>
          <div class="amount-cards">
            <div class="amount-card">
              <span>商品金额</span>
              <b class="money">{{ money(detailGross) }}</b>
            </div>
            <div class="amount-card">
              <span>优惠</span>
              <b class="money discount">-{{ money(current.discount_amount) }}</b>
            </div>
            <div v-if="current.tax_rate" class="amount-card">
              <span>税率 / 其中税金</span>
              <b class="money">{{ current.tax_rate }}% · {{ money((current.total_amount * current.tax_rate) / (100 + current.tax_rate)) }}</b>
            </div>
            <div class="amount-card highlight">
              <span>应收总额</span>
              <b class="money">{{ money(current.total_amount) }}</b>
            </div>
          </div>

          <el-alert
            v-if="current.status === 'cancelled'"
            title="订单已取消，不再参与出库与统计"
            type="info"
            :closable="false"
            show-icon
            class="cancel-alert"
          />
          <el-steps v-else :active="stepActive" finish-status="success" align-center class="order-steps">
            <el-step title="草稿" />
            <el-step title="待审批" />
            <el-step title="已确认" />
            <el-step title="已完成" />
          </el-steps>

          <div v-if="current.approval && current.approval.required > 1" class="approval-chain">
            <div class="ship-head">
              <h4 class="detail-section">审批链</h4>
              <span class="ship-text">已通过 {{ current.approval.approved }} / {{ current.approval.required }} 级</span>
            </div>
            <el-steps :active="current.approval.approved" finish-status="success" align-center>
              <el-step v-for="level in current.approval.levels" :key="level.level" :title="level.name" />
            </el-steps>
          </div>

          <el-alert
            v-if="current.credit_override"
            :type="current.credit_override.status === 'approved' ? 'success' : current.credit_override.status === 'rejected' ? 'error' : 'warning'"
            :closable="false"
            show-icon
            class="override-alert"
            :title="`信用特批：${overrideStatusText}`"
            :description="current.credit_override.reason || '未填写原因'"
          />

          <div v-if="current.shipment && current.shipping_mode === 'outbound' && current.status !== 'cancelled'" class="ship-box">
            <div class="ship-head">
              <h4 class="detail-section">发货进度</h4>
              <span class="ship-text">
                已发 {{ current.shipment.shipped_quantity }} / {{ current.shipment.total_quantity }}
                · 未发 {{ current.shipment.unshipped_quantity }}
              </span>
            </div>
            <el-progress :percentage="current.shipment.progress_percent" :stroke-width="10" />
            <el-table v-if="current.outbounds && current.outbounds.length" :data="current.outbounds" size="small" class="ship-table">
              <el-table-column prop="outbound_no" label="出库单号" width="150" />
              <el-table-column label="状态" width="92">
                <template #default="{ row }">{{ row.status_text || row.status }}</template>
              </el-table-column>
              <el-table-column label="数量" width="70" align="right">
                <template #default="{ row }">{{ row.total_quantity }}</template>
              </el-table-column>
              <el-table-column label="物流" min-width="160" show-overflow-tooltip>
                <template #default="{ row }">
                  {{ row.logistics_no ? `${row.logistics_company} ${row.logistics_no}` : '—' }}
                </template>
              </el-table-column>
              <el-table-column label="发货时间" width="140">
                <template #default="{ row }">{{ formatTime(row.shipped_at) }}</template>
              </el-table-column>
            </el-table>
          </div>

          <h4 class="detail-section">商品明细</h4>
          <el-table :data="current.items" size="small">
            <el-table-column type="index" label="#" width="44" align="center" />
            <el-table-column prop="product_sku" label="商品编码" width="110" show-overflow-tooltip />
            <el-table-column prop="product_name" label="商品名称" min-width="140" show-overflow-tooltip />
            <el-table-column prop="product_spec" label="规格" width="90" show-overflow-tooltip />
            <el-table-column prop="product_unit" label="单位" width="60" align="center" />
            <el-table-column prop="product_category" label="分类" width="90" show-overflow-tooltip />
            <el-table-column prop="quantity" label="数量" width="60" align="center" />
            <el-table-column label="单价" width="92" align="right">
              <template #default="{ row }">{{ money(row.unit_price) }}</template>
            </el-table-column>
            <el-table-column label="金额" width="100" align="right">
              <template #default="{ row }">{{ money(row.amount) }}</template>
            </el-table-column>
          </el-table>

          <div class="detail-remark">
            <span class="label">备注</span>
            <p>{{ current.remark || '无' }}</p>
          </div>

          <div v-if="approvalLogs.length" class="approval-box">
            <h4 class="detail-section">审批轨迹</h4>
            <el-timeline>
              <el-timeline-item
                v-for="log in approvalLogs"
                :key="log.id"
                :timestamp="formatTime(log.created_at)"
                :type="log.action === 'approve' ? 'success' : log.action === 'reject' ? 'danger' : 'primary'"
              >
                {{ { submit: '提交审批', approve: '审批通过', reject: '审批驳回' }[log.action] || log.action }}
                · {{ log.operator }}
                <p v-if="log.comment" class="log-comment">{{ log.comment }}</p>
              </el-timeline-item>
            </el-timeline>
          </div>
        </div>
      </div>
      <template #footer>
        <el-button @click="detailVisible = false">关闭</el-button>
        <el-button v-if="current" :loading="printingId === current.id" @click="printOrder(current)">
          <el-icon><Printer /></el-icon>&nbsp;打印 Word 单据
        </el-button>
        <el-button v-if="current" :loading="printingId === current.id" @click="printDelivery(current)">
          <el-icon><Van /></el-icon>&nbsp;打印送货单
        </el-button>
        <el-button v-if="current" @click="openPrint(current.id)">
          <el-icon><Printer /></el-icon>&nbsp;PDF 打印
        </el-button>
        <el-button
          v-if="canRequestOverride"
          type="warning"
          plain
          :loading="requestingOverride"
          @click="requestCreditOverride(current)"
        >
          申请信用特批
        </el-button>
        <el-button v-if="canDispatch" type="success" :loading="dispatching" @click="createOutbound(current)">
          <el-icon><Van /></el-icon>&nbsp;生成出库单
        </el-button>
        <el-button
          v-if="drawerActionInfo"
          :type="drawerActionInfo.type"
          :loading="busyId === current?.id"
          @click="drawerAction"
        >
          {{ drawerActionInfo.label }}
        </el-button>
      </template>
    </el-drawer>

    <PrintDialog v-model="printVisible" doc-type="sales_order" :ids="printIds" title="打印销售订单" />

    <QuickProductDialog
      v-model="quickProductVisible"
      :preset="quickProductPreset"
      product-type="finished_good"
      @created="onProductCreated"
    />
    <QuickPartnerDialog
      v-model="quickPartnerVisible"
      kind="customer"
      :preset-name="recognizedPartner?.raw_name || ''"
      @created="onCustomerCreated"
    />
  </div>
</template>

<style scoped>
.scope-tag {
  margin-top: 6px;
}

/* 状态快捷筛选 */
.status-tabs {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin-bottom: 14px;
  padding-bottom: 12px;
  border-bottom: 1px solid var(--border-light);
}

.status-chip {
  border: 1px solid transparent;
  background: transparent;
  color: var(--text-secondary);
  font-size: 12.5px;
  line-height: 26px;
  padding: 0 12px;
  border-radius: 14px;
  cursor: pointer;
  transition: color 0.18s, background 0.18s, border-color 0.18s;
}

.status-chip:hover {
  color: var(--text);
  background: var(--bg);
}

.status-chip.active {
  color: var(--brand);
  background: var(--brand-soft);
  border-color: rgba(99, 102, 241, 0.25);
  font-weight: 500;
}

.search-input {
  width: 260px;
}

.batch-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
  padding: 8px 12px;
  border-radius: 8px;
  background: var(--brand-soft);
  border: 1px solid rgba(99, 102, 241, 0.18);
}

.batch-bar .el-button {
  margin-left: 0;
}

.batch-text {
  margin-right: auto;
  font-size: 12.5px;
  color: var(--brand-dark);
}

.batch-text b {
  font-size: 13.5px;
}

.toolbar-count {
  margin-left: auto;
  font-size: 12px;
  color: var(--text-muted);
}

.order-link {
  border: none;
  background: none;
  padding: 0;
  font: inherit;
  font-weight: 600;
  color: var(--brand);
  cursor: pointer;
}

.order-link:hover {
  color: var(--brand-dark);
  text-decoration: underline;
}

.muted {
  color: var(--text-secondary);
  font-size: 12.5px;
}

.amount {
  font-size: 13.5px;
}

.row-actions {
  display: flex;
  align-items: center;
  gap: 4px;
}

.row-actions :deep(.el-button) {
  margin-left: 0;
}

.table-empty {
  padding: 34px 0 26px;
  text-align: center;
}

.empty-icon {
  width: 52px;
  height: 52px;
  margin: 0 auto 10px;
  border-radius: 14px;
  background: var(--brand-soft);
  color: var(--brand);
  font-size: 24px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.table-empty p {
  margin: 0 0 12px;
  color: var(--text-secondary);
  font-size: 13px;
}

.pager {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  flex-wrap: wrap;
  margin-top: 14px;
}

.page-total {
  font-size: 12.5px;
  color: var(--text-secondary);
}

.page-total b {
  margin-left: 4px;
  font-size: 14px;
  color: var(--danger);
}

/* 弹窗内明细编辑 */
.items-section {
  margin: 4px 0 18px;
  padding: 14px 16px;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: #fcfdff;
}

.items-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 10px;
  font-size: 13px;
  font-weight: 600;
}

.items-count {
  font-size: 12px;
  font-weight: 400;
  color: var(--text-secondary);
}

.item-grid-head,
.item-grid-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 110px 120px 110px 28px;
  gap: 8px;
  align-items: center;
}

.item-grid-head {
  font-size: 12px;
  color: var(--text-secondary);
  padding: 0 2px 6px;
}

.item-grid-row {
  padding: 6px 0;
  border-top: 1px dashed var(--border-light);
}

.product-cell {
  display: flex;
  flex-direction: column;
  gap: 3px;
  min-width: 0;
}

.stock-warn {
  font-size: 11.5px;
  color: var(--danger);
}

.align-right {
  text-align: right;
}

.add-item {
  margin-top: 8px;
}

.summary-strip {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 28px;
  margin: 4px 0 6px;
  padding: 12px 16px;
  border-radius: 10px;
  background: var(--bg);
}

.summary-item {
  display: flex;
  flex-direction: column;
  gap: 2px;
  align-items: flex-end;
  font-size: 12px;
  color: var(--text-secondary);
}

.summary-item b {
  font-size: 16px;
  color: var(--text);
}

.summary-item .discount {
  color: var(--warning);
}

.summary-item.total b {
  font-size: 20px;
  color: var(--danger);
}

/* 详情抽屉 */
.detail {
  min-height: 240px;
}

.detail-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
}

.detail-head h3 {
  margin: 0 0 6px;
  font-size: 17px;
}

.detail-head .customer {
  margin: 0 0 2px;
  font-size: 14px;
  color: var(--text);
}

.detail-head .muted {
  margin: 0;
}

.amount-cards {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
  margin: 16px 0 18px;
}

.amount-card {
  padding: 10px 12px;
  border-radius: 10px;
  background: var(--bg);
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.amount-card span {
  font-size: 12px;
  color: var(--text-secondary);
}

.amount-card b {
  font-size: 16px;
}

.amount-card .discount {
  color: var(--warning);
}

.amount-card.highlight {
  background: #fff1f2;
}

.amount-card.highlight b {
  color: var(--danger);
  font-size: 18px;
}

.cancel-alert {
  margin-bottom: 4px;
}

.order-steps {
  margin: 4px 0 18px;
}

.ship-box {
  margin: 6px 0 18px;
  padding: 12px 14px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: #f8fafc;
}

.ship-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
}

.ship-head .detail-section {
  margin: 0 0 8px;
}

.ship-text {
  font-size: 12px;
  color: var(--text-muted);
}

.ship-table {
  margin-top: 10px;
}

.approval-chain {
  margin: 6px 0 14px;
}

.override-alert {
  margin-bottom: 14px;
}

.detail-section {
  margin: 0 0 10px;
  font-size: 13px;
  font-weight: 600;
}

.detail-desc {
  margin-bottom: 18px;
}

.detail-desc :deep(.el-descriptions__label) {  width: 88px;
  color: var(--text-secondary);
}

.detail-remark {
  margin-top: 14px;
  padding: 10px 12px;
  border-radius: 8px;
  background: var(--bg);
  font-size: 13px;
}

.detail-remark .label {
  color: var(--text-secondary);
  font-size: 12px;
}

.detail-remark p {
  margin: 4px 0 0;
  color: var(--text);
  line-height: 1.6;
}

.approval-box {
  margin-top: 18px;
  padding-top: 14px;
  border-top: 1px solid var(--border-light);
}

.approval-box h4 {
  margin: 0 0 12px;
}

.log-comment {
  margin: 4px 0 0;
  padding: 2px 0;
  font-size: 12px;
  color: var(--text-secondary);
}

@media (max-width: 760px) {
  .search-input {
    width: 100%;
  }

  .toolbar-count {
    margin-left: 0;
  }

  .item-grid-head {
    display: none;
  }

  .item-grid-row {
    grid-template-columns: 1fr 1fr;
    row-gap: 6px;
  }

  .item-grid-row > :nth-child(4),
  .item-grid-row > :nth-child(5) {
    grid-column: span 1;
  }
}

.recognize-row {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  margin-bottom: 10px;
}

.recognized-tag {
  font-size: 12px;
  color: #16a34a;
}

.recognize-warnings {
  margin-bottom: 12px;
}

.warning-line {
  margin: 2px 0;
  font-size: 12px;
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

.item-hint {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 4px;
  font-size: 12px;
  flex-wrap: wrap;
}

.detail-photo {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin: 12px 0;
}

.detail-photo-list {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.detail-photo-list .el-image {
  width: 120px;
  height: 90px;
  border-radius: 4px;
  border: 1px solid var(--border);
}
</style>
