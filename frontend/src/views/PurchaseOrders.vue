<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouteKeyword } from '../utils/routeQuery'
import { useQuickCreate } from '../utils/quickCreate'
import { ElMessage, ElMessageBox } from 'element-plus'
import { productApi, purchaseApi, supplierApi } from '../api'
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
const printingId = ref(null)

const loading = ref(false)
const list = ref([])
const total = ref(0)
const page = ref(1)
const query = reactive({ keyword: '', status: '', supplier_id: null, sort: 'created_desc' })
// 日期与金额区间单独存，避免数组/空值混进请求参数
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
const printVisible = ref(false)
const printIds = ref([])

function openPrint(ids) {
  const rows = (Array.isArray(ids) ? ids : [ids]).filter(Boolean)
  if (!rows.length) {
    ElMessage.warning('没有可打印的采购单')
    return
  }
  printIds.value = rows
  printVisible.value = true
}
const hasFilter = computed(
  () =>
    !!query.keyword.trim() ||
    !!query.status ||
    !!query.supplier_id ||
    !!dateRange.value?.length ||
    amountRange.min != null ||
    amountRange.max != null,
)
const route = useRouteKeyword(query, () => {
  page.value = 1
  load()
})
if (typeof route.query.status === 'string') query.status = route.query.status

const suppliers = ref([])
const products = ref([])
const dialogVisible = ref(false)
const detailVisible = ref(false)
const current = ref(null)
const approvalLogs = ref([])
const editingId = ref(null)
const dialogTitle = computed(() => (editingId.value ? '编辑采购单' : '新建采购单'))

const blankOrder = () => ({
  supplier_id: null,
  tax_rate: 0,
  remark: '',
  photo_path: '',
  photo_name: '',
  photo_paths: [],
  ocr_status: 'manual',
  ocr_raw: '',
  raw_supplier_name: '',
  items: [{ product_id: null, quantity: 10, unit_price: 0 }],
})
const form = reactive(blankOrder())

const recognizeWarnings = ref([])
const recognizedPartner = ref(null)
const quickProductVisible = ref(false)
const quickProductPreset = ref({})
const quickProductTarget = ref(null)
const quickPartnerVisible = ref(false)

const statusMap = {
  draft: { text: '草稿', type: 'info' },
  pending: { text: '待审批', type: 'warning' },
  ordered: { text: '已下单', type: 'primary' },
  received: { text: '已入库', type: 'success' },
  cancelled: { text: '已取消', type: 'danger' },
}

const totalAmount = computed(() =>
  form.items.reduce((sum, i) => sum + i.quantity * i.unit_price, 0)
)

async function load() {
  loading.value = true
  try {
    const data = await purchaseApi.list({
      ...query,
      supplier_id: query.supplier_id || undefined,
      start_date: dateRange.value?.[0] || '',
      end_date: dateRange.value?.[1] || '',
      amount_min: amountRange.min ?? undefined,
      amount_max: amountRange.max ?? undefined,
      page: page.value,
      page_size: 20,
    })
    list.value = data.items
    total.value = data.total
    // 台账口径：筛选条件下的总额（后端按全量算，不随分页变化）
    amountSum.value = data.amount_sum || 0
  } finally {
    loading.value = false
  }
}

function resetQuery() {
  query.keyword = ''
  query.status = ''
  query.supplier_id = null
  query.sort = 'created_desc'
  dateRange.value = null
  amountRange.min = null
  amountRange.max = null
  search()
}

function search() {
  page.value = 1
  load()
}

function onProductChange(row) {
  const product = products.value.find((p) => p.id === row.product_id)
  if (product) row.unit_price = product.cost_price
  markMatched(row)
}

function addItem() {
  form.items.push({ product_id: null, quantity: 10, unit_price: 0 })
}

function removeItem(index) {
  if (form.items.length === 1) return
  form.items.splice(index, 1)
}

function onRecognized(result) {
  Object.assign(form, photoFields(result))
  form.raw_supplier_name = result.partner?.raw_name || ''
  recognizedPartner.value = result.partner || null
  if (result.supplier_id) form.supplier_id = result.supplier_id
  form.items = mapRecognizedItems(result.items)
  recognizeWarnings.value = result.warnings || []
  if (!form.items.length) {
    ElMessage.warning('未识别到商品行，请手动添加或换一张更清晰的照片')
    return
  }
  const seconds = ((result.elapsed_ms || 0) / 1000).toFixed(1)
  ElMessage.success(`识别完成，共 ${form.items.length} 行，用时 ${seconds} 秒，请核对后保存`)
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

function onSupplierCreated(supplier) {
  suppliers.value.push(supplier)
  form.supplier_id = supplier.id
}

// 顶栏「新建」入口带 ?new=1 时直接打开开单弹窗
useQuickCreate(openCreate)

function openCreate() {
  editingId.value = null
  Object.assign(form, blankOrder())
  recognizeWarnings.value = []
  recognizedPartner.value = null
  dialogVisible.value = true
}

function openEdit(row) {
  editingId.value = row.id
  Object.assign(form, {
    supplier_id: row.supplier_id,
    tax_rate: row.tax_rate || 0,
    remark: row.remark || '',
    photo_path: row.photo_path || '',
    photo_name: row.photo_name || '',
    photo_paths: row.photo_paths || [],
    ocr_status: row.ocr_status || 'manual',
    ocr_raw: row.ocr_raw || '',
    raw_supplier_name: '',
    items: (row.items || []).map((item) => ({
      product_id: item.product_id,
      quantity: item.quantity,
      unit_price: item.unit_price,
    })),
  })
  recognizeWarnings.value = []
  recognizedPartner.value = null
  if (!form.items.length) form.items = [{ product_id: null, quantity: 10, unit_price: 0 }]
  dialogVisible.value = true
}

async function submit() {
  if (!form.supplier_id) return ElMessage.warning('请选择供应商')
  if (form.items.some((i) => !i.product_id || i.quantity <= 0)) {
    return ElMessage.warning('请完整填写采购明细')
  }
  const payload = {
    supplier_id: form.supplier_id,
    tax_rate: form.tax_rate || 0,
    remark: form.remark,
    photo_path: form.photo_path,
    photo_name: form.photo_name,
    photo_paths: form.photo_paths,
    ocr_status: form.ocr_status,
    ocr_raw: form.ocr_raw,
    raw_supplier_name: form.raw_supplier_name,
    items: form.items.map((item) => itemPayload(item)),
  }
  if (editingId.value) {
    await purchaseApi.update(editingId.value, payload)
    ElMessage.success('草稿采购单已更新')
  } else {
    await purchaseApi.create(payload)
    ElMessage.success('采购单已创建（草稿）')
  }
  dialogVisible.value = false
  editingId.value = null
  load()
}

// 打印输出真实 .docx：优先套用采购订单模板，没有则用系统内置版式
async function printOrder(row) {
  if (printingId.value) return
  printingId.value = row.id
  try {
    const blob = await purchaseApi.printDocx(row.id)
    saveBlob(blob, `采购订单_${row.order_no}.docx`)
    ElMessage.success('Word 单据已生成，用 Word 打开即可打印')
  } finally {
    printingId.value = null
  }
}

async function viewDetail(row) {
  current.value = await purchaseApi.detail(row.id)
  approvalLogs.value = await purchaseApi.approvals(row.id)
  detailVisible.value = true
}

async function changeStatus(row, action, label) {
  await ElMessageBox.confirm(`确定要执行「${label}」吗？`, '操作确认', { type: 'warning' })
  await purchaseApi.changeStatus(row.id, action)
  ElMessage.success('操作成功')
  load()
}

async function approveOrder(row) {
  await ElMessageBox.confirm(`审批通过后采购单将进入已下单状态，确定通过 ${row.order_no} 吗？`, '审批通过', {
    type: 'warning',
  })
  await purchaseApi.changeStatus(row.id, 'approve')
  ElMessage.success('采购单已审批通过')
  load()
}

async function rejectOrder(row) {
  const { value } = await ElMessageBox.prompt('请填写驳回原因，采购单会退回草稿', `驳回 ${row.order_no}`, {
    inputValidator: (val) => (val && val.trim() ? true : '请填写驳回原因'),
  })
  await purchaseApi.changeStatus(row.id, 'reject', value)
  ElMessage.success('采购单已驳回')
  load()
}

async function remove(row) {
  await ElMessageBox.confirm(`确定删除草稿采购单 ${row.order_no} 吗？`, '删除确认', { type: 'warning' })
  await purchaseApi.remove(row.id)
  ElMessage.success('已删除')
  load()
}

function money(value) {
  return '¥' + Number(value || 0).toLocaleString('zh-CN', { minimumFractionDigits: 2 })
}

onMounted(async () => {
  load()
  const [s, p] = await Promise.all([
    supplierApi.all(),
    productApi.list({ page_size: 200, product_type: 'raw_material' }),
  ])
  suppliers.value = s
  products.value = p.items
})
</script>

<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h1 class="page-title">采购订单</h1>
        <p class="page-subtitle">采购单先提交审批，通过后下单，入库时自动增加库存；退货会冲减应付</p>
        <el-tag v-if="userStore.dataScope === 'own'" size="small" type="warning" effect="plain" style="margin-top: 6px">
          数据范围：仅显示本人创建的采购单
        </el-tag>
      </div>
      <el-button v-perm="'purchase:manage'" type="primary" @click="openCreate"><el-icon><Plus /></el-icon>&nbsp;新建采购单</el-button>
    </div>

    <div class="card">
      <div class="toolbar">
        <el-input v-model="query.keyword" placeholder="搜索采购单号 / 供应商" clearable style="width: 240px" @keyup.enter="search" @clear="search" />
        <el-select v-model="query.status" placeholder="全部状态" clearable style="width: 140px" @change="search">
          <el-option v-for="(v, k) in statusMap" :key="k" :label="v.text" :value="k" />
        </el-select>
        <el-button @click="search">查询</el-button>
        <el-button v-if="hasFilter" text @click="resetQuery">重置</el-button>
        <el-button plain @click="openPrint(list.map((row) => row.id))">打印本页 PDF</el-button>
        <span class="toolbar-count">共 {{ total }} 张 · 合计 ¥{{ money(amountSum) }}</span>
      </div>

      <div class="filter-bar">
        <el-select
          v-model="query.supplier_id"
          placeholder="全部供应商"
          clearable
          filterable
          style="width: 200px"
          @change="search"
        >
          <el-option v-for="s in suppliers" :key="s.id" :label="s.name" :value="s.id" />
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

      <el-table :data="list" v-loading="loading">
        <el-table-column prop="order_no" label="采购单号" width="170" />
        <el-table-column prop="supplier_name" label="供应商" min-width="170" show-overflow-tooltip />
        <el-table-column label="下单时间" width="160">
          <template #default="{ row }">{{ row.order_date.replace('T', ' ').slice(0, 16) }}</template>
        </el-table-column>
        <el-table-column label="金额" width="130" align="right">
          <template #default="{ row }"><b class="money">{{ money(row.total_amount) }}</b></template>
        </el-table-column>
        <el-table-column label="状态" width="100" align="center">
          <template #default="{ row }">
            <el-tag :type="statusMap[row.status]?.type" effect="light" size="small">{{ statusMap[row.status]?.text }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="remark" label="备注" min-width="150" show-overflow-tooltip />
        <el-table-column label="操作" width="380" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="viewDetail(row)">详情</el-button>
            <el-button link type="info" :loading="printingId === row.id" @click="printOrder(row)">打印 Word</el-button>
            <el-button v-if="row.status === 'draft'" v-perm="'purchase:manage'" link type="primary" @click="openEdit(row)">编辑</el-button>
            <el-button v-if="row.status === 'draft'" v-perm="'purchase:manage'" link type="success" @click="changeStatus(row, 'submit', '提交审批')">提交审批</el-button>
            <el-button v-if="row.status === 'pending' && userStore.hasPerm('purchase:approve')" link type="success" @click="approveOrder(row)">通过</el-button>
            <el-button v-if="row.status === 'pending' && userStore.hasPerm('purchase:approve')" link type="danger" @click="rejectOrder(row)">驳回</el-button>
            <el-button v-if="row.status === 'ordered'" v-perm="'purchase:receive'" link type="success" @click="changeStatus(row, 'receive', '确认入库并增加库存')">入库</el-button>
            <el-button v-if="row.status !== 'cancelled'" v-perm="'purchase:manage'" link type="warning" @click="changeStatus(row, 'cancel', '取消采购单')">取消</el-button>
            <el-button v-if="row.status === 'draft'" v-perm="'purchase:manage'" link type="danger" @click="remove(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>

      <div class="pager">
        <el-pagination v-model:current-page="page" :page-size="20" :total="total" layout="total, prev, pager, next" @current-change="load" />
      </div>
    </div>

    <el-dialog v-model="dialogVisible" :title="dialogTitle" width="760px" top="6vh">
      <div class="recognize-row">
        <DocRecognize doc-type="purchase" @recognized="onRecognized" />
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

      <el-form :model="form" label-width="80px">
        <el-form-item label="供应商" required>
          <div class="field-with-action">
            <el-select v-model="form.supplier_id" filterable placeholder="选择供应商" style="width: 100%">
              <el-option
                v-for="s in suppliers"
                :key="s.id"
                :label="s.supply_category ? `${s.name}（${s.supply_category}）` : s.name"
                :value="s.id"
              />
            </el-select>
            <el-button link type="primary" @click="quickPartnerVisible = true">新建</el-button>
          </div>
          <div v-if="recognizedPartner && !form.supplier_id" class="field-hint">
            识别为「{{ recognizedPartner.raw_name || '未识别' }}」，系统里没有匹配的供应商
          </div>
        </el-form-item>

        <el-form-item label="采购明细">
          <div class="items-box">
            <div class="item-head">
              <span class="col-product">商品</span>
              <span class="col-num">数量</span>
              <span class="col-num">采购单价</span>
              <span class="col-amount">小计</span>
              <span class="col-op"></span>
            </div>
            <div v-for="(item, index) in form.items" :key="index" class="item-block">
              <div class="item-row">
                <el-select v-model="item.product_id" filterable placeholder="选择商品" class="col-product" @change="onProductChange(item)">
                  <el-option-group v-if="item.candidates?.length" label="识别建议">
                    <el-option
                      v-for="candidate in item.candidates"
                      :key="`c-${candidate.product_id}`"
                      :label="`${candidate.name}（相似 ${candidate.score}）`"
                      :value="candidate.product_id"
                    />
                  </el-option-group>
                  <el-option-group label="全部商品">
                    <el-option v-for="p in products" :key="p.id" :label="`${p.name}（库存 ${p.stock_qty}）`" :value="p.id" />
                  </el-option-group>
                </el-select>
                <el-input-number v-model="item.quantity" :min="1" class="col-num" />
                <el-input-number v-model="item.unit_price" :min="0" :precision="2" class="col-num" />
                <span class="col-amount money">{{ money(item.quantity * item.unit_price) }}</span>
                <el-button class="col-op" link type="danger" :disabled="form.items.length === 1" @click="removeItem(index)">
                  <el-icon><Delete /></el-icon>
                </el-button>
              </div>
              <div v-if="item.match_status || item.raw_name" class="item-hint">
                <el-tag size="small" :type="matchTag(item).type">{{ matchTag(item).text }}</el-tag>
                <span v-if="item.raw_name" class="muted">识别为：{{ item.raw_name }}</span>
                <el-button v-if="!item.product_id" link type="primary" size="small" @click="openQuickProduct(item)">新建商品</el-button>
              </div>
            </div>
            <el-button link type="primary" @click="addItem"><el-icon><Plus /></el-icon>&nbsp;添加明细</el-button>
          </div>
        </el-form-item>

        <el-form-item label="备注"><el-input v-model="form.remark" type="textarea" :rows="2" /></el-form-item>
        <el-form-item label="税率%">
          <el-input-number v-model="form.tax_rate" :min="0" :max="100" :precision="2" :controls="false" placeholder="0 不含税" style="width: 160px" />
          <span class="muted" style="margin-left: 8px">大于 0 时采购金额按价税合计，过账自动拆进项税额</span>
        </el-form-item>
        <div class="total-line">采购金额：<b>{{ money(totalAmount) }}</b></div>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" @click="submit">{{ editingId ? '保存修改' : '保存为草稿' }}</el-button>
      </template>
    </el-dialog>

    <el-drawer v-model="detailVisible" title="采购单详情" size="520px">
      <div v-if="current">
        <div class="detail-head">
          <div>
            <h3>{{ current.order_no }}</h3>
            <p>{{ current.supplier_name }} · {{ current.order_date.replace('T', ' ').slice(0, 16) }}</p>
          </div>
          <el-tag :type="statusMap[current.status]?.type" effect="light">{{ statusMap[current.status]?.text }}</el-tag>
        </div>
        <el-table :data="current.items" size="small">
          <el-table-column prop="product_name" label="商品" min-width="140" show-overflow-tooltip />
          <el-table-column prop="quantity" label="数量" width="70" align="center" />
          <el-table-column label="单价" width="90" align="right">
            <template #default="{ row }">{{ money(row.unit_price) }}</template>
          </el-table-column>
          <el-table-column label="小计" width="100" align="right">
            <template #default="{ row }">{{ money(row.amount) }}</template>
          </el-table-column>
        </el-table>
        <div class="detail-total"><span>合计金额：<b>{{ money(current.total_amount) }}</b></span></div>
        <p class="detail-remark">备注：{{ current.remark || '无' }}</p>
        <div v-if="current.photo_path || current.photo_paths?.length" class="detail-photo">
          <span class="muted">单据照片（点击可放大）</span>
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
        <div v-if="approvalLogs.length" class="approval-box">
          <h4>审批轨迹</h4>
          <el-timeline>
            <el-timeline-item
              v-for="log in approvalLogs"
              :key="log.id"
              :timestamp="String(log.created_at).replace('T', ' ').slice(0, 16)"
              :type="log.action === 'approve' ? 'success' : log.action === 'reject' ? 'danger' : 'primary'"
            >
              {{ { submit: '提交审批', approve: '审批通过', reject: '审批驳回' }[log.action] || log.action }}
              · {{ log.operator }}
              <p v-if="log.comment" class="log-comment">{{ log.comment }}</p>
            </el-timeline-item>
          </el-timeline>
        </div>
      </div>
    </el-drawer>

    <PrintDialog v-model="printVisible" doc-type="purchase_order" :ids="printIds" title="打印采购订单" />

    <QuickProductDialog
      v-model="quickProductVisible"
      :preset="quickProductPreset"
      product-type="raw_material"
      @created="onProductCreated"
    />
    <QuickPartnerDialog
      v-model="quickPartnerVisible"
      kind="supplier"
      :preset-name="recognizedPartner?.raw_name || ''"
      @created="onSupplierCreated"
    />
  </div>
</template>

<style scoped>
.items-box {
  width: 100%;
}

.item-head,
.item-row {
  display: flex;
  gap: 8px;
  align-items: center;
}

.item-head span {
  font-size: 12px;
  color: var(--text-secondary);
}

.item-row {
  margin-bottom: 8px;
}

.item-block {
  margin-bottom: 8px;
}

.item-block .item-row {
  margin-bottom: 0;
}

.col-product {
  flex: 1 1 auto;
  min-width: 0;
}

.col-num {
  width: 110px;
  flex: 0 0 110px;
}

.col-amount {
  width: 110px;
  flex: 0 0 110px;
  text-align: right;
}

.col-op {
  width: 32px;
  flex: 0 0 32px;
}

.total-line {
  text-align: right;
  color: var(--text-secondary);
}

.total-line b {
  color: #ef4444;
  font-size: 20px;
}

.detail-head {
  display: flex;
  justify-content: space-between;
  margin-bottom: 16px;
}

.detail-head h3 {
  margin: 0 0 6px;
}

.detail-head p {
  margin: 0;
  color: var(--text-secondary);
  font-size: 13px;
}

.detail-total {
  margin-top: 16px;
  padding-top: 12px;
  border-top: 1px solid var(--border);
  text-align: right;
}

.detail-total b {
  color: #ef4444;
  font-size: 18px;
}

.detail-remark {
  margin-top: 14px;
  color: var(--text-secondary);
  font-size: 13px;
}

.approval-box {
  margin-top: 18px;
  padding-top: 14px;
  border-top: 1px solid var(--border);
}

.approval-box h4 {
  margin: 0 0 12px;
  font-size: 14px;
}

.log-comment {
  margin: 4px 0 0;
  font-size: 12px;
  color: var(--text-secondary);
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
  margin: 2px 0 6px;
  font-size: 12px;
  flex-wrap: wrap;
}

.muted {
  color: var(--text-secondary);
  font-size: 12px;
}

.detail-photo {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-top: 14px;
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
