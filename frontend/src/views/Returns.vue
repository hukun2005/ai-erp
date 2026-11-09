<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { purchaseApi, returnsApi, salesApi, templateApi } from '../api'
import { printDocument } from '../utils/print'
import { useUserStore } from '../stores/user'

const userStore = useUserStore()
const salesPrintTemplate = ref(null)
const purchasePrintTemplate = ref(null)
const canSales = computed(() => userStore.hasPerm('sales:view'))
const canPurchase = computed(() => userStore.hasPerm('purchase:view'))

const kind = ref(canSales.value ? 'sales' : 'purchase')
const loading = ref(false)
const list = ref([])
const total = ref(0)
const page = ref(1)
const query = reactive({ keyword: '', status: '' })

const dialogVisible = ref(false)
const detailVisible = ref(false)
const submitting = ref(false)
const current = ref(null)
const orders = ref([])
const available = ref([])
const form = reactive({ order_id: null, reason: '', items: [] })
const orderInfo = ref(null)

const statusMap = {
  draft: { text: '草稿', type: 'info' },
  pending: { text: '待审批', type: 'warning' },
  confirmed: { text: '已确认', type: 'success' },
  cancelled: { text: '已取消', type: 'danger' },
}

const kindLabel = computed(() => (kind.value === 'sales' ? '销售退货' : '采购退货'))

function money(value) {
  return '¥' + Number(value || 0).toLocaleString('zh-CN', { minimumFractionDigits: 2 })
}

function datetime(value) {
  return String(value || '').replace('T', ' ').slice(0, 16)
}

async function load() {
  loading.value = true
  try {
    const api = kind.value === 'sales' ? returnsApi.salesList : returnsApi.purchaseList
    const data = await api({ ...query, page: page.value, page_size: 20 })
    list.value = data.items
    total.value = data.total
  } finally {
    loading.value = false
  }
}

function switchKind(value) {
  kind.value = value
  page.value = 1
  query.keyword = ''
  query.status = ''
  load()
}

async function loadOrderOptions() {
  const statuses = kind.value === 'sales' ? ['confirmed', 'completed'] : ['received']
  const api = kind.value === 'sales' ? salesApi : purchaseApi
  const pages = await Promise.all(statuses.map((status) => api.list({ status, page_size: 200 })))
  orders.value = pages.flatMap((data) => data.items)
}

async function openCreate() {
  if (!canSales.value && kind.value === 'sales') return ElMessage.warning('当前角色无销售订单查看权限')
  if (!canPurchase.value && kind.value === 'purchase') return ElMessage.warning('当前角色无采购订单查看权限')
  Object.assign(form, { order_id: null, reason: '', items: [] })
  available.value = []
  orderInfo.value = null
  await loadOrderOptions()
  if (!orders.value.length) {
    ElMessage.warning(kind.value === 'sales' ? '暂无可退货的已确认销售订单' : '暂无可退货的已入库采购订单')
    return
  }
  dialogVisible.value = true
}

async function onOrderChange(orderId) {
  const api = kind.value === 'sales' ? returnsApi.salesAvailable : returnsApi.purchaseAvailable
  const data = await api(orderId)
  orderInfo.value = data
  available.value = data.items
  form.items = data.items
    .filter((item) => item.remaining_qty > 0)
    .map((item) => ({
      product_id: item.product_id,
      product_name: item.product_name,
      product_sku: item.product_sku,
      unit_price: item.unit_price,
      remaining_qty: item.remaining_qty,
      quantity: item.remaining_qty,
    }))
}

const totalAmount = computed(() =>
  form.items.reduce((sum, item) => sum + Number(item.quantity || 0) * item.unit_price, 0)
)

async function submit() {
  if (!form.order_id) return ElMessage.warning('请选择退货的原订单')
  const items = form.items.filter((item) => item.quantity > 0)
  if (!items.length) return ElMessage.warning('请填写退货数量')
  const invalid = items.find((item) => item.quantity > item.remaining_qty)
  if (invalid) return ElMessage.warning(`${invalid.product_name} 退货数量超过可退数量`)
  submitting.value = true
  try {
    const api = kind.value === 'sales' ? returnsApi.salesCreate : returnsApi.purchaseCreate
    const result = await api({
      order_id: form.order_id,
      reason: form.reason,
      items: items.map((item) => ({
        product_id: item.product_id,
        quantity: Number(item.quantity),
        unit_price: item.unit_price,
      })),
    })
    ElMessage.success(`退货单 ${result.return_no} 已保存为草稿，确认后才会回冲库存与${kind.value === 'sales' ? '应收' : '应付'}`)
    dialogVisible.value = false
    load()
  } finally {
    submitting.value = false
  }
}

async function viewDetail(row) {
  const api = kind.value === 'sales' ? returnsApi.salesDetail : returnsApi.purchaseDetail
  current.value = await api(row.id)
  detailVisible.value = true
}

async function changeStatus(row, action) {
  const label = {
    confirm: '确认退货',
    submit: '提交审批',
    approve: '审批通过',
    reject: '审批驳回',
    cancel: '取消退货',
  }[action]
  const detail = {
    confirm: `确认后${kind.value === 'sales' ? '库存回补、应收减少' : '库存扣减、应付减少'}，确定继续吗？`,
    submit: '提交后进入审批流程，审批通过才真正调整库存与往来。',
    approve: '审批通过后将立即执行退货（调整库存与往来）。',
    reject: '驳回后退回草稿，可修改后重新提交。',
    cancel: '取消已确认的退货会回滚库存，确定继续吗？',
  }[action]
  await ElMessageBox.confirm(detail, `${label} · ${row.return_no}`, { type: 'warning' })
  const api = kind.value === 'sales' ? returnsApi.salesStatus : returnsApi.purchaseStatus
  const result = await api(row.id, action)
  ElMessage.success(
    action === 'approve' && result?.status === 'pending' ? '本级已通过，等待下一级审批' : `${label}成功`
  )
  load()
}

async function remove(row) {
  await ElMessageBox.confirm(`确定删除草稿退货单 ${row.return_no} 吗？`, '删除确认', { type: 'warning' })
  const api = kind.value === 'sales' ? returnsApi.salesRemove : returnsApi.purchaseRemove
  await api(row.id)
  ElMessage.success('已删除')
  load()
}

async function printReturn(row) {
  let data = current.value && current.value.id === row.id ? current.value : null
  if (!data) {
    const api = kind.value === 'sales' ? returnsApi.salesDetail : returnsApi.purchaseDetail
    data = await api(row.id)
  }
  const ok = printDocument({
    title: kind.value === 'sales' ? '销售退货单' : '采购退货单',
    subtitle: data.return_no,
    meta: [
      [kind.value === 'sales' ? '客户' : '供应商', data.partner_name],
      ['原订单', data.order_no],
      ['退货仓库', data.warehouse_name],
      ['状态', statusMap[data.status]?.text || data.status],
    ],
    columns: [
      { key: 'product_sku', label: 'SKU' },
      { key: 'product_name', label: '商品名称' },
      { key: 'quantity', label: '退货数量', align: 'right' },
      { key: 'unit_price', label: '单价', align: 'right', format: money },
      { key: 'amount', label: '金额', align: 'right', format: money },
    ],
    rows: data.items || [],
    totals: [{ label: '退货金额', value: money(data.total_amount), bold: true }],
    remark: data.reason,
    template: kind.value === 'sales' ? salesPrintTemplate.value : purchasePrintTemplate.value,
  })
  if (!ok) ElMessage.warning('浏览器拦截了打印窗口，请允许弹出窗口')
}

async function loadTemplates() {
  const [salesTpl, purchaseTpl] = await Promise.all([
    templateApi.defaultFor('sales_return').catch(() => null),
    templateApi.defaultFor('purchase_return').catch(() => null),
  ])
  salesPrintTemplate.value = salesTpl
  purchasePrintTemplate.value = purchaseTpl
}

onMounted(() => {
  load()
  loadTemplates()
})
</script>

<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h1 class="page-title">退货管理</h1>
        <p class="page-subtitle">
          销售退货回补库存并冲减应收；采购退货扣减库存并冲减应付。确认后不可编辑，仅能取消回滚。
        </p>
      </div>
      <el-button v-perm="'return:manage'" type="primary" @click="openCreate">
        <el-icon><Plus /></el-icon>&nbsp;新建{{ kindLabel }}
      </el-button>
    </div>

    <div class="card">
      <div class="toolbar">
        <el-radio-group :model-value="kind" size="small" @change="switchKind">
          <el-radio-button v-if="canSales" value="sales">销售退货</el-radio-button>
          <el-radio-button v-if="canPurchase" value="purchase">采购退货</el-radio-button>
        </el-radio-group>
        <el-input v-model="query.keyword" placeholder="搜索退货单号 / 往来单位" clearable style="width: 260px" @keyup.enter="load" @clear="load" />
        <el-select v-model="query.status" placeholder="全部状态" clearable style="width: 130px" @change="load">
          <el-option v-for="(v, k) in statusMap" :key="k" :label="v.text" :value="k" />
        </el-select>
        <el-button @click="load">查询</el-button>
      </div>

      <el-table :data="list" v-loading="loading">
        <el-table-column prop="return_no" label="退货单号" width="170" />
        <el-table-column prop="order_no" label="原订单号" width="170" />
        <el-table-column :label="kind === 'sales' ? '客户' : '供应商'" prop="partner_name" min-width="160" show-overflow-tooltip />
        <el-table-column prop="warehouse_name" label="仓库" width="110" />
        <el-table-column label="退货金额" width="120" align="right">
          <template #default="{ row }"><b class="money">{{ money(row.total_amount) }}</b></template>
        </el-table-column>
        <el-table-column label="状态" width="90" align="center">
          <template #default="{ row }">
            <el-tag :type="statusMap[row.status]?.type" size="small">{{ statusMap[row.status]?.text }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="created_by" label="创建人" width="90" />
        <el-table-column label="创建时间" width="150">
          <template #default="{ row }">{{ datetime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="280" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="viewDetail(row)">详情</el-button>
            <el-button link type="primary" @click="printReturn(row)">打印</el-button>
            <el-button v-if="row.status === 'draft'" v-perm="'return:manage'" link type="success" @click="changeStatus(row, 'confirm')">确认</el-button>
            <el-button v-if="row.status === 'draft'" v-perm="'return:manage'" link type="warning" @click="changeStatus(row, 'submit')">提交审批</el-button>
            <template v-if="row.status === 'pending'">
              <el-button link type="success" @click="changeStatus(row, 'approve')">通过</el-button>
              <el-button link type="danger" @click="changeStatus(row, 'reject')">驳回</el-button>
            </template>
            <el-button v-if="row.status !== 'cancelled'" v-perm="'return:manage'" link type="warning" @click="changeStatus(row, 'cancel')">取消</el-button>
            <el-button v-if="row.status === 'draft'" v-perm="'return:manage'" link type="danger" @click="remove(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>

      <div class="pager">
        <el-pagination v-model:current-page="page" :page-size="20" :total="total" layout="total, prev, pager, next" @current-change="load" />
      </div>
    </div>

    <el-dialog v-model="dialogVisible" :title="`新建${kindLabel}`" width="780px" top="6vh">
      <el-form label-width="90px">
        <el-form-item label="原订单" required>
          <el-select v-model="form.order_id" filterable placeholder="选择可退货的原订单" style="width: 100%" @change="onOrderChange">
            <el-option
              v-for="order in orders"
              :key="order.id"
              :label="`${order.order_no} · ${kind === 'sales' ? order.customer_name : order.supplier_name} · ${money(order.total_amount)}`"
              :value="order.id"
            />
          </el-select>
        </el-form-item>

        <el-form-item v-if="orderInfo" label="退货明细">
          <el-table :data="form.items" size="small" border style="width: 100%">
            <el-table-column prop="product_name" label="商品" min-width="150" show-overflow-tooltip />
            <el-table-column prop="product_sku" label="SKU" width="110" />
            <el-table-column label="可退" width="70" align="center">
              <template #default="{ row }">{{ row.remaining_qty }}</template>
            </el-table-column>
            <el-table-column label="退货数量" width="130">
              <template #default="{ row }">
                <el-input-number v-model="row.quantity" :min="0" :max="row.remaining_qty" size="small" style="width: 100%" />
              </template>
            </el-table-column>
            <el-table-column label="单价" width="100" align="right">
              <template #default="{ row }">{{ money(row.unit_price) }}</template>
            </el-table-column>
            <el-table-column label="小计" width="110" align="right">
              <template #default="{ row }">{{ money(row.quantity * row.unit_price) }}</template>
            </el-table-column>
          </el-table>
          <div v-if="!form.items.length" class="hint">该订单已无可退商品</div>
        </el-form-item>

        <el-form-item label="退货原因">
          <el-input v-model="form.reason" type="textarea" :rows="2" placeholder="例如：客户退回 / 质量不合格" />
        </el-form-item>

        <div class="total-line">退货金额：<b>{{ money(totalAmount) }}</b></div>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="submit">保存为草稿</el-button>
      </template>
    </el-dialog>

    <el-drawer v-model="detailVisible" :title="`${kindLabel}详情`" size="480px">
      <div v-if="current">
        <div class="detail-head">
          <div>
            <h3>{{ current.return_no }}</h3>
            <p>{{ current.partner_name }} · 原单 {{ current.order_no }}</p>
            <p v-if="current.warehouse_name">退货仓库：{{ current.warehouse_name }}</p>
          </div>
          <el-tag :type="statusMap[current.status]?.type">{{ statusMap[current.status]?.text }}</el-tag>
        </div>
        <el-button size="small" style="margin-bottom: 10px" @click="printReturn(current)">打印退货单</el-button>
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
        <div class="detail-total">
          <span>创建人：{{ current.created_by }}</span>
          <span>合计：<b>{{ money(current.total_amount) }}</b></span>
        </div>
        <p class="detail-remark">退货原因：{{ current.reason || '无' }}</p>
        <p v-if="current.confirmed_at" class="detail-remark">确认时间：{{ datetime(current.confirmed_at) }}</p>
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
  flex-wrap: wrap;
}

.hint {
  font-size: 12px;
  color: var(--text-secondary);
  margin-top: 6px;
}

.total-line {
  text-align: right;
  font-size: 15px;
  color: var(--text-secondary);
}

.total-line b {
  color: #ef4444;
  font-size: 20px;
}

.detail-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 16px;
}

.detail-head h3 {
  margin: 0 0 6px;
}

.detail-head p {
  margin: 0 0 4px;
  color: var(--text-secondary);
  font-size: 13px;
}

.detail-total {
  display: flex;
  justify-content: space-between;
  margin-top: 16px;
  padding-top: 12px;
  border-top: 1px solid var(--border);
  font-size: 13px;
  color: var(--text-secondary);
}

.detail-total b {
  color: #ef4444;
  font-size: 18px;
}

.detail-remark {
  margin-top: 12px;
  color: var(--text-secondary);
  font-size: 13px;
}
</style>
