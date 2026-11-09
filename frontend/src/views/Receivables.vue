<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { receivablesApi } from '../api'

const loading = ref(false)
const summary = ref({ total: 0, buckets: [], items: [] })
const payments = ref([])
const statements = ref([])

const payVisible = ref(false)
const paySubmitting = ref(false)
const openOrders = ref([])
const payForm = reactive({
  customer_id: null,
  customer_name: '',
  amount: 0,
  pay_date: '',
  method: '银行转账',
  reference: '',
  remark: '',
})

const stVisible = ref(false)
const stSubmitting = ref(false)
const stForm = reactive({ customer_id: null, customer_name: '', month: '' })
const issued = ref(null)

const statusMap = {
  issued: { text: '待确认', type: 'info' },
  confirmed: { text: '已确认', type: 'success' },
  disputed: { text: '有异议', type: 'warning' },
}

function money(value) {
  return '¥' + Number(value || 0).toLocaleString('zh-CN', { minimumFractionDigits: 2 })
}

function saveBlob(blob, filename) {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)
}

async function load() {
  loading.value = true
  try {
    const [data, pay, st] = await Promise.all([
      receivablesApi.summary(),
      receivablesApi.payments({ page_size: 10 }),
      receivablesApi.statements({ page_size: 10 }),
    ])
    summary.value = data
    payments.value = pay.items
    statements.value = st.items
  } finally {
    loading.value = false
  }
}

/* ---------------- 登记收款 ---------------- */
async function openPay(row) {
  Object.assign(payForm, {
    customer_id: row.customer_id,
    customer_name: row.customer,
    amount: row.balance,
    pay_date: new Date().toISOString().slice(0, 10),
    method: '银行转账',
    reference: '',
    remark: '',
  })
  openOrders.value = await receivablesApi.openOrders(row.customer_id)
  smart.value = null
  manualRows.value = null
  payVisible.value = true
}

// 核销方案：默认走后端先进先出；点「智能核销」后采用建议，并可手工微调金额
const smart = ref(null)
const matchLoading = ref(false)
const manualRows = ref(null)

const fifoRows = computed(() => {
  let remaining = Number(payForm.amount || 0)
  return openOrders.value
    .map((order) => {
      const take = Math.max(Math.min(order.balance, remaining), 0)
      remaining = remaining - take
      return { ...order, take: Math.round(take * 100) / 100, reason: '先进先出' }
    })
    .filter((order) => order.take > 0)
})

const allocationRows = computed(() => manualRows.value || fifoRows.value)

const allocationSum = computed(() =>
  allocationRows.value.reduce((sum, row) => sum + Number(row.take || 0), 0)
)

async function runSmartMatch() {
  if (!payForm.amount || Number(payForm.amount) <= 0) {
    ElMessage.warning('请先填写收款金额')
    return
  }
  matchLoading.value = true
  try {
    const data = await receivablesApi.matchPayment({
      customer_id: payForm.customer_id,
      amount: Number(payForm.amount),
      pay_date: payForm.pay_date ? `${payForm.pay_date}T00:00:00` : null,
      reference: payForm.reference,
    })
    smart.value = data
    manualRows.value = data.allocations.map((item) => ({
      order_id: item.order_id,
      order_no: item.order_no,
      order_date: item.order_date,
      balance: item.balance,
      take: item.amount,
      reason: item.reason,
    }))
    ElMessage.success(`已按「${data.strategy_label}」生成 ${data.allocations.length} 条核销建议`)
  } finally {
    matchLoading.value = false
  }
}

function resetToFifo() {
  smart.value = null
  manualRows.value = null
}

// 金额或流水号一变，原建议就不再对应，清掉避免拿旧结果误核销
watch([() => payForm.amount, () => payForm.reference], () => {
  if (manualRows.value) resetToFifo()
})

async function submitPay() {
  if (!payForm.amount || Number(payForm.amount) <= 0) {
    ElMessage.warning('请填写收款金额')
    return
  }
  const payload = {
    customer_id: payForm.customer_id,
    amount: Number(payForm.amount),
    pay_date: payForm.pay_date ? `${payForm.pay_date}T00:00:00` : null,
    method: payForm.method,
    reference: payForm.reference,
    remark: payForm.remark,
  }
  if (manualRows.value) {
    const rows = manualRows.value
      .filter((row) => Number(row.take) > 0)
      .map((row) => ({ order_id: row.order_id, amount: Number(row.take) }))
    if (!rows.length) {
      ElMessage.warning('核销明细为空，请重新匹配或改回先进先出')
      return
    }
    if (allocationSum.value > Number(payForm.amount) + 0.001) {
      ElMessage.warning('核销合计不能超过收款金额')
      return
    }
    payload.allocations = rows
  }
  paySubmitting.value = true
  try {
    const result = await receivablesApi.createPayment(payload)
    ElMessage.success(
    result.status === 'pending'
      ? `收款已登记：${result.payment_no}（待审批，通过后入账）`
      : `收款已登记：${result.payment_no}，核销 ${result.allocations.length} 张订单`
  )
    payVisible.value = false
    load()
  } finally {
    paySubmitting.value = false
  }
}

async function voidPayment(row) {
  const value = await ElMessageBox.prompt('作废后核销自动回退，订单余额恢复。请填写作废原因：', '作废收款', {
    inputPlaceholder: '例如：录错金额',
  })
  await receivablesApi.voidPayment(row.id, value.value || '')
  ElMessage.success('已作废，应收已回退')
  load()
}

async function approvePayment(row) {
  await ElMessageBox.confirm('通过后收款单入账并核销应收，确定继续吗？', `审批收款单 ${row.payment_no}`, {
    type: 'warning',
  })
  const result = await receivablesApi.approvePayment(row.id)
  ElMessage.success(result?.status === 'pending' ? '本级已通过，等待下一级审批' : '收款单已通过')
  load()
}

async function rejectPayment(row) {
  const { value } = await ElMessageBox.prompt('请填写驳回原因', `驳回收款单 ${row.payment_no}`, {
    inputValidator: (val) => (val && val.trim() ? true : '请填写驳回原因'),
  })
  await receivablesApi.rejectPayment(row.id, value)
  ElMessage.success('收款单已驳回')
  load()
}

/* ---------------- 生成对账单 ---------------- */
function lastMonth() {
  const date = new Date()
  date.setDate(1)
  date.setMonth(date.getMonth() - 1)
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}`
}

function periodRange(month) {
  const [year, mon] = month.split('-').map(Number)
  const last = new Date(year, mon, 0).getDate()
  return { start: `${month}-01`, end: `${month}-${String(last).padStart(2, '0')}` }
}

function openStatement(row) {
  Object.assign(stForm, { customer_id: row.customer_id, customer_name: row.customer, month: lastMonth() })
  issued.value = null
  stVisible.value = true
}

async function submitStatement() {
  if (!stForm.month) return ElMessage.warning('请选择账期')
  const { start, end } = periodRange(stForm.month)
  stSubmitting.value = true
  try {
    const result = await receivablesApi.createStatement({
      customer_id: stForm.customer_id,
      period_start: start,
      period_end: end,
    })
    issued.value = { ...result, url: `${location.origin}${result.share_path}` }
    ElMessage.success(`已生成 ${result.statement_no}`)
    load()
  } finally {
    stSubmitting.value = false
  }
}

async function copyText(text, label) {
  try {
    await navigator.clipboard.writeText(text)
    ElMessage.success(`${label}已复制`)
  } catch {
    ElMessage.warning('复制失败，请手动选中文本复制')
  }
}

async function exportStatement(row) {
  saveBlob(await receivablesApi.exportStatement(row.id), `对账单-${row.statement_no}.xlsx`)
}

onMounted(load)
</script>

<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h1 class="page-title">应收与对账</h1>
        <p class="page-subtitle">
          应收 = 已确认订单 − 已核销收款。生成对账单后把「链接 + 口令」发给客户，对方在线确认或提异议，全程留痕。
        </p>
      </div>
      <el-button @click="load" :loading="loading"><el-icon><Refresh /></el-icon>&nbsp;刷新</el-button>
    </div>

    <div class="cards">
      <div class="card stat">
        <span>应收合计</span>
        <b class="danger">{{ money(summary.total) }}</b>
      </div>
      <div class="card stat" v-for="bucket in summary.buckets" :key="bucket.name">
        <span>{{ bucket.name }}</span>
        <b>{{ money(bucket.value) }}</b>
      </div>
    </div>

    <div class="card">
      <div class="toolbar">
        <b>客户应收台账</b>
        <span class="hint">账龄按最早未结订单计算</span>
      </div>
      <el-table :data="summary.items" v-loading="loading">
        <el-table-column prop="customer" label="客户" min-width="170" show-overflow-tooltip />
        <el-table-column label="等级" width="70" align="center">
          <template #default="{ row }"><el-tag size="small" effect="light">{{ row.level }}</el-tag></template>
        </el-table-column>
        <el-table-column label="订单金额" width="130" align="right">
          <template #default="{ row }">{{ money(row.total) }}</template>
        </el-table-column>
        <el-table-column label="已收" width="130" align="right">
          <template #default="{ row }"><span class="muted">{{ money(row.paid) }}</span></template>
        </el-table-column>
        <el-table-column label="未收" width="130" align="right">
          <template #default="{ row }"><b class="danger">{{ money(row.balance) }}</b></template>
        </el-table-column>
        <el-table-column label="账龄" width="100" align="center">
          <template #default="{ row }">
            <el-tag :type="row.bucket === '60天以上' ? 'danger' : row.bucket === '31-60天' ? 'warning' : 'info'" size="small">
              {{ row.age_days }} 天
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="190" fixed="right">
          <template #default="{ row }">
            <el-button v-perm="'finance:manage'" link type="primary" @click="openPay(row)">登记收款</el-button>
            <el-button v-perm="'finance:manage'" link type="success" @click="openStatement(row)">生成对账单</el-button>
          </template>
        </el-table-column>
      </el-table>
      <div v-if="!summary.items.length && !loading" class="empty-text">当前没有未结清的应收</div>
    </div>

    <div class="card">
      <div class="toolbar"><b>最近收款</b></div>
      <el-table :data="payments" size="small">
        <el-table-column prop="payment_no" label="收款单号" width="170" />
        <el-table-column prop="customer_name" label="客户" min-width="150" show-overflow-tooltip />
        <el-table-column label="金额" width="120" align="right">
          <template #default="{ row }">{{ money(row.amount) }}</template>
        </el-table-column>
        <el-table-column prop="method" label="方式" width="100" />
        <el-table-column prop="reference" label="流水号" width="130" />
        <el-table-column label="核销" width="80" align="center">
          <template #default="{ row }">{{ row.allocations.length }} 单</template>
        </el-table-column>
        <el-table-column label="状态" width="90" align="center">
          <template #default="{ row }">
            <el-tag
              :type="row.status === 'posted' ? 'success' : row.status === 'pending' ? 'warning' : 'info'"
              size="small"
            >
              {{ row.status === 'posted' ? '已入账' : row.status === 'pending' ? '待审批' : '已作废' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="150" fixed="right">
          <template #default="{ row }">
            <template v-if="row.status === 'pending'">
              <el-button link type="success" @click="approvePayment(row)">通过</el-button>
              <el-button link type="danger" @click="rejectPayment(row)">驳回</el-button>
            </template>
            <el-button
              v-if="row.status === 'posted'"
              v-perm="'finance:manage'"
              link
              type="danger"
              @click="voidPayment(row)"
            >
              作废
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <div class="card">
      <div class="toolbar"><b>对账单</b><span class="hint">已发出的版本冻结，改单不会影响对方看到的内容</span></div>
      <el-table :data="statements" size="small">
        <el-table-column prop="statement_no" label="单号" width="190" />
        <el-table-column prop="customer_name" label="客户" min-width="150" show-overflow-tooltip />
        <el-table-column label="账期" width="190">
          <template #default="{ row }">{{ row.period_start }} ~ {{ row.period_end }}</template>
        </el-table-column>
        <el-table-column label="期末应收" width="130" align="right">
          <template #default="{ row }">{{ money(row.closing_amount) }}</template>
        </el-table-column>
        <el-table-column label="状态" width="90" align="center">
          <template #default="{ row }">
            <el-tag :type="statusMap[row.status]?.type" size="small">{{ statusMap[row.status]?.text }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="110" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="exportStatement(row)">导出</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <!-- 登记收款 -->
    <el-dialog v-model="payVisible" :title="`登记收款 · ${payForm.customer_name}`" width="720px" top="6vh">
      <el-form label-width="90px">
        <el-row :gutter="14">
          <el-col :span="8">
            <el-form-item label="收款金额">
              <el-input-number v-model="payForm.amount" :min="0" :precision="2" style="width: 100%" />
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="收款日期">
              <el-date-picker v-model="payForm.pay_date" type="date" value-format="YYYY-MM-DD" style="width: 100%" />
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="收款方式">
              <el-select v-model="payForm.method" style="width: 100%">
                <el-option v-for="m in ['银行转账', '承兑汇票', '现金', '微信', '支付宝']" :key="m" :label="m" :value="m" />
              </el-select>
            </el-form-item>
          </el-col>
        </el-row>
        <el-row :gutter="14">
          <el-col :span="12">
            <el-form-item label="流水号">
              <el-input v-model="payForm.reference" placeholder="银行回单号，便于对方查账" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="备注">
              <el-input v-model="payForm.remark" />
            </el-form-item>
          </el-col>
        </el-row>

        <el-form-item label="核销方案">
          <div class="alloc-box">
            <div class="alloc-head">
              <el-button type="primary" plain :loading="matchLoading" @click="runSmartMatch">
                <el-icon><MagicStick /></el-icon>&nbsp;智能核销
              </el-button>
              <el-button v-if="manualRows" link @click="resetToFifo">改回先进先出</el-button>
              <el-tag
                v-if="smart"
                :type="smart.confidence === 'high' ? 'success' : smart.confidence === 'medium' ? 'warning' : 'info'"
                effect="light"
                size="small"
              >
                {{ smart.strategy_label }} · {{ { high: '高置信', medium: '中置信', low: '低置信' }[smart.confidence] }}
              </el-tag>
              <span class="hint">
                {{ manualRows ? '可直接修改下方金额，确认后按此核销' : '默认按订单时间先进先出自动核销' }}
              </span>
            </div>
            <el-alert
              v-if="smart"
              :title="smart.explanation"
              :type="smart.confidence === 'high' ? 'success' : 'info'"
              :closable="false"
              show-icon
              style="margin-bottom: 8px"
            />
            <el-alert
              v-for="(warning, index) in smart?.warnings || []"
              :key="index"
              :title="warning"
              type="warning"
              :closable="false"
              show-icon
              style="margin-bottom: 6px"
            />
            <el-table :data="allocationRows" size="small" border>
              <el-table-column prop="order_no" label="订单号" width="170" />
              <el-table-column prop="order_date" label="下单日期" width="110" />
              <el-table-column label="未结余额" width="120" align="right">
                <template #default="{ row }">{{ money(row.balance) }}</template>
              </el-table-column>
              <el-table-column label="本次核销" width="150" align="right">
                <template #default="{ row }">
                  <el-input-number
                    v-if="manualRows"
                    v-model="row.take"
                    :min="0"
                    :max="row.balance"
                    :precision="2"
                    size="small"
                    style="width: 100%"
                  />
                  <b v-else>{{ money(row.take) }}</b>
                </template>
              </el-table-column>
              <el-table-column prop="reason" label="依据" min-width="140" show-overflow-tooltip />
            </el-table>
            <div class="alloc-sum">
              核销合计 {{ money(allocationSum) }} / 收款金额 {{ money(payForm.amount) }}
              <span v-if="allocationSum > Number(payForm.amount || 0) + 0.001" class="over">（超过收款金额）</span>
            </div>
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="payVisible = false">取消</el-button>
        <el-button type="primary" :loading="paySubmitting" @click="submitPay">确认入账</el-button>
      </template>
    </el-dialog>

    <!-- 生成对账单 -->
    <el-dialog v-model="stVisible" :title="`生成对账单 · ${stForm.customer_name}`" width="640px">
      <el-form label-width="90px">
        <el-form-item label="账期">
          <el-date-picker v-model="stForm.month" type="month" value-format="YYYY-MM" style="width: 220px" />
          <span class="hint" style="margin-left: 10px">默认上月，可跨月对账时手动调整</span>
        </el-form-item>
      </el-form>

      <el-alert
        v-if="issued"
        type="success"
        :closable="false"
        show-icon
        title="对账单已生成，请把链接和口令一起发给客户（分开发更安全）"
      />
      <div v-if="issued" class="share-box">
        <div class="share-row">
          <span class="label">确认链接</span>
          <code class="value">{{ issued.url }}</code>
          <el-button link type="primary" @click="copyText(issued.url, '链接')">复制</el-button>
        </div>
        <div class="share-row">
          <span class="label">确认口令</span>
          <code class="value code">{{ issued.share_code }}</code>
          <el-button link type="primary" @click="copyText(issued.share_code, '口令')">复制</el-button>
        </div>
        <p class="hint">
          期末应收 {{ money(issued.closing_amount) }} ·
          内容摘要 {{ issued.content_hash.slice(0, 16) }} · 有效期 7 天
        </p>
      </div>

      <template #footer>
        <el-button @click="stVisible = false">关闭</el-button>
        <el-button type="primary" :loading="stSubmitting" @click="submitStatement">生成</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.cards {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
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
  font-size: 19px;
}

.danger {
  color: #ef4444;
}

.muted {
  color: var(--text-secondary);
}

.hint {
  font-size: 12px;
  color: var(--text-secondary);
}

.toolbar {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 10px;
}

.alloc-box {
  width: 100%;
}

.share-box {
  margin-top: 12px;
  padding: 12px;
  background: #f8fbff;
  border: 1px solid #dbeafe;
  border-radius: 8px;
}

.share-row {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 8px;
}

.share-row .label {
  width: 70px;
  color: var(--text-secondary);
  font-size: 13px;
}

.share-row .value {
  flex: 1;
  font-family: ui-monospace, Consolas, monospace;
  font-size: 12px;
  word-break: break-all;
}

.share-row .code {
  font-size: 18px;
  letter-spacing: 4px;
  color: #2563eb;
  font-weight: 600;
}

.empty-text {
  padding: 18px 0;
  text-align: center;
  color: var(--text-secondary);
  font-size: 13px;
}

.alloc-head {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 8px;
  flex-wrap: wrap;
}

.alloc-sum {
  margin-top: 8px;
  font-size: 13px;
  color: var(--text-secondary);
  text-align: right;
}

.alloc-sum .over {
  color: #ef4444;
}
</style>
