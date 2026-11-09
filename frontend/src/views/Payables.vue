<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { payablesApi, supplierApi, templateApi } from '../api'
import { saveBlob } from '../utils/download'
import { printDocument } from '../utils/print'

const tab = ref('overview')

const loading = ref(false)
const summary = ref({ total: 0, buckets: [], items: [] })

const suppliers = ref([])

const payVisible = ref(false)
const paying = ref(false)
const payForm = reactive({
  supplier_id: null,
  amount: 0,
  pay_date: new Date().toISOString().slice(0, 10),
  method: '银行转账',
  reference: '',
  remark: '',
  manual: false,
})
const openOrders = ref([])
const allocations = ref({})
const matchLoading = ref(false)
const smartResult = ref(null)

async function runSmartMatch() {
  if (!payForm.supplier_id) return ElMessage.warning('请先选择供应商')
  if (!payForm.amount || Number(payForm.amount) <= 0) return ElMessage.warning('请填写付款金额')
  matchLoading.value = true
  try {
    if (!openOrders.value.length) await onSupplierChange(payForm.supplier_id)
    const data = await payablesApi.matchPayment({
      supplier_id: payForm.supplier_id,
      amount: Number(payForm.amount),
      pay_date: payForm.pay_date ? `${payForm.pay_date}T00:00:00` : null,
      reference: payForm.reference,
    })
    smartResult.value = data
    payForm.manual = true
    const suggested = Object.fromEntries(data.allocations.map((item) => [item.order_id, item.amount]))
    allocations.value = Object.fromEntries(
      openOrders.value.map((row) => [row.order_id, suggested[row.order_id] || 0])
    )
    ElMessage.success(`已按「${data.strategy_label}」填充 ${data.allocations.length} 条核销金额`)
  } finally {
    matchLoading.value = false
  }
}


const ordersVisible = ref(false)
const ordersSupplier = ref(null)

const paymentsLoading = ref(false)
const payments = ref([])
const payTotal = ref(0)
const payPage = ref(1)

const statementsLoading = ref(false)
const statements = ref([])
const statementTotal = ref(0)
const statementPage = ref(1)
const statementVisible = ref(false)
const statementForm = reactive({
  supplier_id: null,
  period: [new Date().toISOString().slice(0, 8) + '01', new Date().toISOString().slice(0, 10)],
})
const statementDetailVisible = ref(false)
const statementDetail = ref(null)
const paymentTemplate = ref(null)
const statementTemplate = ref(null)

const buckets = computed(() => summary.value.buckets || [])
const supplierMap = computed(() =>
  Object.fromEntries(suppliers.value.map((item) => [item.id, item.name]))
)

function money(value) {
  return '¥' + Number(value || 0).toLocaleString('zh-CN', { minimumFractionDigits: 2 })
}

function datetime(value) {
  return String(value || '').replace('T', ' ').slice(0, 16)
}

async function loadSummary() {
  loading.value = true
  try {
    summary.value = await payablesApi.summary()
  } finally {
    loading.value = false
  }
}

async function loadSuppliers() {
  suppliers.value = await supplierApi.all()
}

async function loadPayments() {
  paymentsLoading.value = true
  try {
    const data = await payablesApi.payments({ page: payPage.value, page_size: 20 })
    payments.value = data.items
    payTotal.value = data.total
  } finally {
    paymentsLoading.value = false
  }
}

async function loadStatements() {
  statementsLoading.value = true
  try {
    const data = await payablesApi.statements({ page: statementPage.value, page_size: 20 })
    statements.value = data.items
    statementTotal.value = data.total
  } finally {
    statementsLoading.value = false
  }
}

function onTabChange() {
  if (tab.value === 'payments') loadPayments()
  if (tab.value === 'statements') loadStatements()
}

async function openPay(row = null) {
  await loadSuppliers()
  Object.assign(payForm, {
    supplier_id: row?.supplier_id || null,
    amount: row?.balance || 0,
    pay_date: new Date().toISOString().slice(0, 10),
    method: '银行转账',
    reference: '',
    remark: '',
    manual: false,
  })
  openOrders.value = []
  allocations.value = {}
  smartResult.value = null
  payVisible.value = true
  if (row) await onSupplierChange(row.supplier_id)
}

async function onSupplierChange(supplierId) {
  if (!supplierId) return
  openOrders.value = await payablesApi.openOrders(supplierId)
  allocations.value = Object.fromEntries(openOrders.value.map((row) => [row.order_id, 0]))
}

function toggleManual(value) {
  if (value && payForm.supplier_id && !openOrders.value.length) onSupplierChange(payForm.supplier_id)
}

const allocationTotal = computed(() =>
  Object.values(allocations.value).reduce((sum, value) => sum + Number(value || 0), 0)
)

async function submitPay() {
  if (!payForm.supplier_id) return ElMessage.warning('请选择供应商')
  if (!payForm.amount || payForm.amount <= 0) return ElMessage.warning('付款金额必须大于 0')
  const payload = {
    supplier_id: payForm.supplier_id,
    amount: Number(payForm.amount),
    pay_date: `${payForm.pay_date}T00:00:00`,
    method: payForm.method,
    reference: payForm.reference,
    remark: payForm.remark,
  }
  if (payForm.manual) {
    const rows = Object.entries(allocations.value)
      .map(([orderId, amount]) => ({ order_id: Number(orderId), amount: Number(amount || 0) }))
      .filter((row) => row.amount > 0)
    if (!rows.length) return ElMessage.warning('请至少填写一笔核销金额')
    if (allocationTotal.value > payForm.amount + 0.001) return ElMessage.warning('核销合计不能超过付款金额')
    payload.allocations = rows
  }
  paying.value = true
  try {
    const result = await payablesApi.createPayment(payload)
    ElMessage.success(
      result.status === 'pending'
        ? `付款单 ${result.payment_no} 已登记（待审批，通过后计入应付）`
        : `付款单 ${result.payment_no} 已登记，核销 ${result.allocations.length} 张入库单`
    )
    payVisible.value = false
    await Promise.all([loadSummary(), loadPayments()])
  } finally {
    paying.value = false
  }
}

async function showOpenOrders(row) {
  ordersSupplier.value = row
  openOrders.value = await payablesApi.openOrders(row.supplier_id)
  ordersVisible.value = true
}

async function voidPayment(row) {
  const { value } = await ElMessageBox.prompt(`作废后应付将恢复，请填写作废原因`, `作废付款单 ${row.payment_no}`, {
    inputPlaceholder: '例如：录错金额 / 重复登记',
    inputValidator: (val) => (val && val.trim() ? true : '请填写作废原因'),
  })
  await payablesApi.voidPayment(row.id, value)
  ElMessage.success('付款单已作废')
  await Promise.all([loadPayments(), loadSummary()])
}

async function approvePayment(row) {
  await ElMessageBox.confirm(`通过后付款单生效并核销应付，确定继续吗？`, `审批付款单 ${row.payment_no}`, {
    type: 'warning',
  })
  const result = await payablesApi.approvePayment(row.id)
  ElMessage.success(result?.status === 'pending' ? '本级已通过，等待下一级审批' : '付款单已通过')
  await Promise.all([loadPayments(), loadSummary()])
}

async function rejectPayment(row) {
  const { value } = await ElMessageBox.prompt('请填写驳回原因', `驳回付款单 ${row.payment_no}`, {
    inputValidator: (val) => (val && val.trim() ? true : '请填写驳回原因'),
  })
  await payablesApi.rejectPayment(row.id, value)
  ElMessage.success('付款单已驳回')
  await Promise.all([loadPayments(), loadSummary()])
}

async function openStatement() {
  await loadSuppliers()
  Object.assign(statementForm, {
    supplier_id: null,
    period: [new Date().toISOString().slice(0, 8) + '01', new Date().toISOString().slice(0, 10)],
  })
  statementVisible.value = true
}

async function generateStatement() {
  if (!statementForm.supplier_id || !statementForm.period?.length) {
    return ElMessage.warning('请选择供应商与账期')
  }
  const result = await payablesApi.createStatement({
    supplier_id: statementForm.supplier_id,
    period_start: statementForm.period[0],
    period_end: statementForm.period[1],
  })
  ElMessage.success(`对账单 ${result.statement_no} 已生成并冻结`)
  statementVisible.value = false
  statementDetail.value = result
  statementDetailVisible.value = true
  loadStatements()
}

async function viewStatement(row) {
  statementDetail.value = await payablesApi.statementDetail(row.id)
  statementDetailVisible.value = true
}

async function exportStatement(row) {
  saveBlob(await payablesApi.exportStatement(row.id), `供应商对账单-${row.statement_no}.xlsx`)
}

function printPayment(row) {
  const ok = printDocument({
    title: '付款单',
    subtitle: row.payment_no,
    meta: [
      ['供应商', row.supplier_name || supplierMap.value[row.supplier_id] || ''],
      ['付款日期', String(row.pay_date || '').slice(0, 10)],
      ['付款方式', row.method || ''],
      ['流水号', row.reference || ''],
    ],
    columns: [
      { key: 'order_no', label: '核销入库单' },
      { key: 'amount', label: '核销金额', align: 'right', format: money },
    ],
    rows: row.allocations || [],
    totals: [{ label: '付款金额', value: money(row.amount), bold: true }],
    template: paymentTemplate.value,
  })
  if (!ok) ElMessage.warning('浏览器拦截了打印窗口，请允许弹出窗口')
}

function printStatement(data) {
  const ok = printDocument({
    title: '供应商对账单',
    subtitle: data.statement_no,
    meta: [
      ['供应商', data.supplier_name],
      ['账期', `${data.period_start} ~ ${data.period_end}`],
      ['版本', `V${data.version}`],
      ['期末应付', money(data.closing_amount)],
    ],
    columns: [
      { key: 'date', label: '日期' },
      {
        key: 'type',
        label: '类型',
        render: (row) => ({ order: '采购入库', return: '采购退货', payment: '付款' }[row.type] || row.type),
      },
      { key: 'no', label: '单号' },
      { key: 'summary', label: '摘要' },
      { key: 'increase', label: '增加', align: 'right', format: (value) => (value ? money(value) : '') },
      { key: 'decrease', label: '减少', align: 'right', format: (value) => (value ? money(value) : '') },
      { key: 'balance', label: '余额', align: 'right', format: money },
    ],
    rows: data.payload?.lines || [],
    totals: [
      { label: '期初应付', value: money(data.opening_amount) },
      { label: '本期增加', value: money(data.increase_amount) },
      { label: '本期减少', value: money(data.decrease_amount) },
      { label: '期末应付', value: money(data.closing_amount), bold: true },
    ],
    template: statementTemplate.value,
  })
  if (!ok) ElMessage.warning('浏览器拦截了打印窗口，请允许弹出窗口')
}

onMounted(async () => {
  await loadSummary()
  loadSuppliers()
  const [payTpl, statementTpl] = await Promise.all([
    templateApi.defaultFor('payment').catch(() => null),
    templateApi.defaultFor('statement').catch(() => null),
  ])
  paymentTemplate.value = payTpl
  statementTemplate.value = statementTpl
})
</script>

<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h1 class="page-title">应付与付款</h1>
        <p class="page-subtitle">应付 = 已入库采购 − 已确认采购退货 − 已付款；付款可核销入库单，支持账龄与供应商对账。</p>
      </div>
      <div class="header-actions">
        <el-button v-perm="'payable:manage'" @click="openStatement">生成对账单</el-button>
        <el-button v-perm="'payable:manage'" type="primary" @click="openPay()">
          <el-icon><Wallet /></el-icon>&nbsp;登记付款
        </el-button>
      </div>
    </div>

    <div class="cards">
      <div class="card stat">
        <span>应付余额</span>
        <b>{{ money(summary.total) }}</b>
      </div>
      <div v-for="bucket in buckets" :key="bucket.name" class="card stat">
        <span>{{ bucket.name }}</span>
        <b>{{ money(bucket.value) }}</b>
      </div>
    </div>

    <el-tabs v-model="tab" @tab-change="onTabChange">
      <el-tab-pane label="供应商应付" name="overview">
        <div class="card">
          <el-table :data="summary.items" v-loading="loading">
            <el-table-column prop="supplier" label="供应商" min-width="150" />
            <el-table-column prop="category" label="供货类别" width="120" />
            <el-table-column label="采购总额" width="120" align="right">
              <template #default="{ row }">{{ money(row.total) }}</template>
            </el-table-column>
            <el-table-column label="已退货" width="110" align="right">
              <template #default="{ row }">{{ money(row.returned) }}</template>
            </el-table-column>
            <el-table-column label="已付款" width="110" align="right">
              <template #default="{ row }">{{ money(row.paid) }}</template>
            </el-table-column>
            <el-table-column label="应付余额" width="130" align="right">
              <template #default="{ row }"><b class="danger">{{ money(row.balance) }}</b></template>
            </el-table-column>
            <el-table-column prop="age_days" label="账龄" width="80" align="center">
              <template #default="{ row }">{{ row.age_days }} 天</template>
            </el-table-column>
            <el-table-column label="账龄段" width="100" align="center">
              <template #default="{ row }">
                <el-tag size="small" :type="row.bucket === '0-30天' ? 'success' : row.bucket === '31-60天' ? 'warning' : 'danger'">
                  {{ row.bucket }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="150" fixed="right">
              <template #default="{ row }">
                <el-button v-perm="'payable:manage'" link type="primary" @click="openPay(row)">付款</el-button>
                <el-button link type="primary" @click="showOpenOrders(row)">未结清单</el-button>
              </template>
            </el-table-column>
          </el-table>
          <div v-if="!summary.items.length && !loading" class="empty-text">暂无应付余额</div>
        </div>
      </el-tab-pane>

      <el-tab-pane label="付款记录" name="payments">
        <div class="card">
          <el-table :data="payments" v-loading="paymentsLoading">
            <el-table-column prop="payment_no" label="付款单号" width="170" />
            <el-table-column prop="supplier_name" label="供应商" min-width="150" />
            <el-table-column label="金额" width="120" align="right">
              <template #default="{ row }"><b>{{ money(row.amount) }}</b></template>
            </el-table-column>
            <el-table-column prop="method" label="方式" width="100" />
            <el-table-column prop="reference" label="流水号" width="140" show-overflow-tooltip />
            <el-table-column label="付款日期" width="120">
              <template #default="{ row }">{{ String(row.pay_date).slice(0, 10) }}</template>
            </el-table-column>
            <el-table-column label="核销单数" width="90" align="center">
              <template #default="{ row }">{{ row.allocations.length }}</template>
            </el-table-column>
            <el-table-column label="状态" width="90" align="center">
              <template #default="{ row }">
                <el-tag
                  size="small"
                  :type="row.status === 'posted' ? 'success' : row.status === 'pending' ? 'warning' : 'info'"
                >
                  {{ row.status === 'posted' ? '已登记' : row.status === 'pending' ? '待审批' : '已作废' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="created_by" label="登记人" width="90" />
            <el-table-column label="操作" width="200" fixed="right">
              <template #default="{ row }">
                <el-button link type="primary" @click="printPayment(row)">打印</el-button>
                <template v-if="row.status === 'pending'">
                  <el-button link type="success" @click="approvePayment(row)">通过</el-button>
                  <el-button link type="danger" @click="rejectPayment(row)">驳回</el-button>
                </template>
                <el-button v-if="row.status === 'posted'" v-perm="'payable:manage'" link type="danger" @click="voidPayment(row)">作废</el-button>
                <span v-if="row.status === 'voided'" class="muted">{{ row.void_reason }}</span>
              </template>
            </el-table-column>
          </el-table>
          <el-pagination v-model:current-page="payPage" :page-size="20" :total="payTotal" layout="total, prev, pager, next" class="pager" @current-change="loadPayments" />
        </div>
      </el-tab-pane>

      <el-tab-pane label="供应商对账单" name="statements">
        <div class="card">
          <el-table :data="statements" v-loading="statementsLoading">
            <el-table-column prop="statement_no" label="对账单号" width="180" />
            <el-table-column prop="supplier_name" label="供应商" min-width="150" />
            <el-table-column label="账期" width="210">
              <template #default="{ row }">{{ row.period_start }} ~ {{ row.period_end }}</template>
            </el-table-column>
            <el-table-column prop="version" label="版本" width="70" align="center" />
            <el-table-column label="期初" width="110" align="right">
              <template #default="{ row }">{{ money(row.opening_amount) }}</template>
            </el-table-column>
            <el-table-column label="本期增加" width="110" align="right">
              <template #default="{ row }">{{ money(row.increase_amount) }}</template>
            </el-table-column>
            <el-table-column label="本期减少" width="110" align="right">
              <template #default="{ row }">{{ money(row.decrease_amount) }}</template>
            </el-table-column>
            <el-table-column label="期末应付" width="120" align="right">
              <template #default="{ row }"><b class="danger">{{ money(row.closing_amount) }}</b></template>
            </el-table-column>
            <el-table-column label="操作" width="180" fixed="right">
              <template #default="{ row }">
                <el-button link type="primary" @click="viewStatement(row)">明细</el-button>
                <el-button link type="primary" @click="printStatement(row)">打印</el-button>
                <el-button link type="primary" @click="exportStatement(row)">导出</el-button>
              </template>
            </el-table-column>
          </el-table>
          <el-pagination v-model:current-page="statementPage" :page-size="20" :total="statementTotal" layout="total, prev, pager, next" class="pager" @current-change="loadStatements" />
        </div>
      </el-tab-pane>
    </el-tabs>

    <el-dialog v-model="payVisible" title="登记付款" width="680px" top="8vh">
      <el-form label-width="90px">
        <el-form-item label="供应商" required>
          <el-select v-model="payForm.supplier_id" filterable placeholder="选择供应商" style="width: 100%" @change="onSupplierChange">
            <el-option v-for="item in suppliers" :key="item.id" :label="item.name" :value="item.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="付款金额" required>
          <el-input-number v-model="payForm.amount" :min="0.01" :precision="2" :step="100" style="width: 220px" />
          <span class="muted" style="margin-left: 10px">留空核销则按入库单先进先出自动分摊</span>
        </el-form-item>
        <el-form-item label="付款日期">
          <el-date-picker v-model="payForm.pay_date" type="date" value-format="YYYY-MM-DD" style="width: 220px" />
        </el-form-item>
        <el-form-item label="付款方式">
          <el-select v-model="payForm.method" style="width: 220px">
            <el-option label="银行转账" value="银行转账" />
            <el-option label="现金" value="现金" />
            <el-option label="承兑汇票" value="承兑汇票" />
            <el-option label="其他" value="其他" />
          </el-select>
        </el-form-item>
        <el-form-item label="流水号">
          <el-input v-model="payForm.reference" placeholder="银行流水号 / 支票号（选填）" />
        </el-form-item>
        <el-form-item label="核销方式">
          <el-switch v-model="payForm.manual" @change="toggleManual" />
          <span class="muted" style="margin-left: 10px">关闭时按先进先出自动分摊</span>
          <el-button
            type="primary"
            plain
            size="small"
            style="margin-left: 12px"
            :loading="matchLoading"
            @click="runSmartMatch"
          >
            <el-icon><MagicStick /></el-icon>&nbsp;智能核销
          </el-button>
        </el-form-item>
        <el-form-item v-if="smartResult" label="匹配依据">
          <div style="width: 100%">
            <el-alert
              :title="`${smartResult.strategy_label}：${smartResult.explanation}`"
              :type="smartResult.confidence === 'high' ? 'success' : 'info'"
              :closable="false"
              show-icon
            />
            <div
              v-for="(warning, index) in smartResult.warnings || []"
              :key="index"
              class="muted"
              style="margin-top: 6px"
            >
              · {{ warning }}
            </div>
          </div>
        </el-form-item>

        <el-form-item v-if="payForm.manual" label="核销明细">
          <el-table :data="openOrders" size="small" border max-height="240">
            <el-table-column prop="order_no" label="入库单" min-width="140" />
            <el-table-column prop="order_date" label="日期" width="100" />
            <el-table-column label="未结余额" width="110" align="right">
              <template #default="{ row }">{{ money(row.balance) }}</template>
            </el-table-column>
            <el-table-column label="本次核销" width="150">
              <template #default="{ row }">
                <el-input-number v-model="allocations[row.order_id]" :min="0" :max="row.balance" :precision="2" size="small" style="width: 100%" />
              </template>
            </el-table-column>
          </el-table>
          <div class="muted">核销合计 {{ money(allocationTotal) }}，付款金额 {{ money(payForm.amount) }}</div>
        </el-form-item>

        <el-form-item label="备注">
          <el-input v-model="payForm.remark" type="textarea" :rows="2" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="payVisible = false">取消</el-button>
        <el-button type="primary" :loading="paying" @click="submitPay">确认登记</el-button>
      </template>
    </el-dialog>

    <el-drawer v-model="ordersVisible" :title="`${ordersSupplier?.supplier || ''} 未结清入库单`" size="520px">
      <el-table :data="openOrders" size="small">
        <el-table-column prop="order_no" label="入库单号" width="160" />
        <el-table-column prop="order_date" label="日期" width="100" />
        <el-table-column label="金额" width="100" align="right">
          <template #default="{ row }">{{ money(row.total_amount) }}</template>
        </el-table-column>
        <el-table-column label="已退货" width="100" align="right">
          <template #default="{ row }">{{ money(row.returned) }}</template>
        </el-table-column>
        <el-table-column label="未结余额" width="110" align="right">
          <template #default="{ row }"><b class="danger">{{ money(row.balance) }}</b></template>
        </el-table-column>
      </el-table>
    </el-drawer>

    <el-dialog v-model="statementVisible" title="生成供应商对账单" width="480px">
      <el-form label-width="80px">
        <el-form-item label="供应商" required>
          <el-select v-model="statementForm.supplier_id" filterable placeholder="选择供应商" style="width: 100%">
            <el-option v-for="item in suppliers" :key="item.id" :label="item.name" :value="item.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="账期" required>
          <el-date-picker v-model="statementForm.period" type="daterange" value-format="YYYY-MM-DD" style="width: 100%" />
        </el-form-item>
      </el-form>
      <div class="muted">生成后会冻结当期采购、退货、付款与期末应付，供双方对账留档。</div>
      <template #footer>
        <el-button @click="statementVisible = false">取消</el-button>
        <el-button type="primary" @click="generateStatement">生成并冻结</el-button>
      </template>
    </el-dialog>

    <el-drawer v-model="statementDetailVisible" :title="statementDetail?.statement_no || '对账单'" size="720px">
      <div v-if="statementDetail">
        <div class="statement-head">
          <div>
            <h3>{{ statementDetail.supplier_name }}</h3>
            <p>账期 {{ statementDetail.period_start }} ~ {{ statementDetail.period_end }} · 版本 V{{ statementDetail.version }}</p>
          </div>
          <b class="danger">期末应付 {{ money(statementDetail.closing_amount) }}</b>
        </div>
        <el-table :data="statementDetail.payload?.lines || []" size="small" border>
          <el-table-column prop="date" label="日期" width="100" />
          <el-table-column label="类型" width="90">
            <template #default="{ row }">{{ { order: '采购入库', return: '采购退货', payment: '付款' }[row.type] || row.type }}</template>
          </el-table-column>
          <el-table-column prop="no" label="单号" width="160" />
          <el-table-column prop="summary" label="摘要" min-width="160" show-overflow-tooltip />
          <el-table-column label="增加" width="100" align="right">
            <template #default="{ row }">{{ row.increase ? money(row.increase) : '' }}</template>
          </el-table-column>
          <el-table-column label="减少" width="100" align="right">
            <template #default="{ row }">{{ row.decrease ? money(row.decrease) : '' }}</template>
          </el-table-column>
          <el-table-column label="余额" width="110" align="right">
            <template #default="{ row }">{{ money(row.balance) }}</template>
          </el-table-column>
        </el-table>
        <div class="statement-foot">
          <span>期初 {{ money(statementDetail.opening_amount) }}</span>
          <span>本期增加 {{ money(statementDetail.increase_amount) }}</span>
          <span>本期减少 {{ money(statementDetail.decrease_amount) }}</span>
          <span>期末 {{ money(statementDetail.closing_amount) }}</span>
        </div>
        <el-button type="primary" plain @click="exportStatement(statementDetail)">导出 Excel</el-button>
        <el-button plain @click="printStatement(statementDetail)">打印对账单</el-button>
      </div>
    </el-drawer>
  </div>
</template>

<style scoped>
.header-actions {
  display: flex;
  gap: 8px;
}

.cards {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
  gap: 12px;
  margin-bottom: 14px;
}

.stat {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.stat span {
  font-size: 12px;
  color: var(--text-secondary);
}

.stat b {
  font-size: 20px;
}

.danger {
  color: #dc2626;
}

.muted {
  font-size: 12px;
  color: var(--text-secondary);
}

.empty-text {
  padding: 18px 0;
  text-align: center;
  color: var(--text-secondary);
  font-size: 13px;
}

.statement-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 12px;
}

.statement-head h3 {
  margin: 0 0 6px;
}

.statement-head p {
  margin: 0;
  color: var(--text-secondary);
  font-size: 13px;
}

.statement-foot {
  display: flex;
  gap: 24px;
  margin: 14px 0;
  font-size: 13px;
  color: var(--text-secondary);
}
</style>
