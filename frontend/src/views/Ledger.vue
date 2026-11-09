<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { ledgerApi, periodApi } from '../api'
import { useUserStore } from '../stores/user'

const userStore = useUserStore()
const canManage = computed(() => userStore.hasPerm('ledger:manage'))
const canClose = computed(() => userStore.hasPerm('period:manage'))

const activeTab = ref('accounts')
const period = ref(new Date().toISOString().slice(0, 7))
const accounts = ref([])
const vouchers = ref([])
const periods = ref([])
const loading = ref(false)
const checkDialog = ref(false)
const checkResult = ref(null)
const checkPeriod = ref('')

// 三大报表
const balanceSheet = ref(null)
const incomeStatement = ref(null)
const cashFlow = ref(null)

function money(v) {
  return '¥' + Number(v || 0).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function catTag(category) {
  return (
    { 资产: 'primary', 负债: 'warning', 所有者权益: 'success', 成本: 'info', 损益: 'danger' }[category] || 'info'
  )
}

async function loadAccounts() {
  accounts.value = await ledgerApi.accounts()
}

async function loadVouchers() {
  loading.value = true
  try {
    vouchers.value = await ledgerApi.vouchers({ period: period.value })
  } finally {
    loading.value = false
  }
}

async function loadStatements() {
  const [bs, is, cf] = await Promise.all([
    ledgerApi.balanceSheet(period.value),
    ledgerApi.incomeStatement(period.value),
    ledgerApi.cashFlow(period.value),
  ])
  balanceSheet.value = bs
  incomeStatement.value = is
  cashFlow.value = cf
}

async function refresh() {
  await Promise.all([loadAccounts(), loadVouchers(), loadStatements(), loadPeriods()])
}

async function loadPeriods() {
  periods.value = await periodApi.list()
}

async function onClosePeriod(row) {
  await ElMessageBox.confirm(
    `确认对「${row.period}」期末结账？系统会先自动体检，通过后生成结转损益凭证，并锁定该期间的凭证。`,
    '期末结账',
    { type: 'warning' }
  )
  try {
    const res = await periodApi.close(row.period)
    const parts = []
    if (res.carry_voucher_no) parts.push(`结转损益凭证 ${res.carry_voucher_no}`)
    if (res.year_carry_voucher_no) parts.push(`年结凭证 ${res.year_carry_voucher_no}`)
    ElMessage.success(parts.length ? `已结账：${parts.join('；')}` : '已结账（当期无损益发生）')
    await refresh()
  } catch (e) {
    // 结账前体检未通过时后端返回 400，detail 为各阻断项说明
    const detail = e?.response?.data?.detail || e?.message || '结账失败'
    ElMessageBox.alert(detail, '结账未通过', { type: 'error', dangerouslyUseHTMLString: false })
  }
}

async function onCheckPeriod(row) {
  const res = await periodApi.check(row.period)
  checkPeriod.value = row.period
  checkResult.value = res
  checkDialog.value = true
}

async function onReopenPeriod(row) {
  await ElMessageBox.confirm(
    `反结账「${row.period}」？结转损益凭证会被冲回，期间重新打开后才能修改凭证。`,
    '反结账',
    { type: 'warning' }
  )
  await periodApi.reopen(row.period)
  ElMessage.success('已反结账')
  await refresh()
}

async function onGenerate() {
  if (!canManage.value) return
  await ElMessageBox.confirm(
    `将按「${period.value}」期间从销售/收款/采购/付款单据重建自动凭证（不影响手工凭证）。是否继续？`,
    '自动生成凭证',
    { type: 'warning' }
  )
  const res = await ledgerApi.generate(period.value)
  ElMessage.success(`已生成 ${res.generated} 张自动凭证`)
  await loadVouchers()
  await loadStatements()
}

async function onVoid(row) {
  await ElMessageBox.confirm(`确认作废凭证「${row.voucher_no}」？作废后不再计入报表。`, '作废凭证', { type: 'warning' })
  await ledgerApi.voidVoucher(row.id)
  ElMessage.success('已作废')
  await loadVouchers()
  await loadStatements()
}

// 手工凭证
const voucherDialog = ref(false)
const voucherForm = ref({ summary: '', voucher_date: new Date().toISOString().slice(0, 10), lines: [{ account_code: '', summary: '', debit: 0, credit: 0 }] })

function addLine() {
  voucherForm.value.lines.push({ account_code: '', summary: '', debit: 0, credit: 0 })
}
function removeLine(i) {
  voucherForm.value.lines.splice(i, 1)
}
async function submitVoucher() {
  const lines = voucherForm.value.lines.filter((l) => l.account_code && (Number(l.debit) > 0 || Number(l.credit) > 0))
  if (!lines.length) return ElMessage.warning('请至少填写一条有效分录')
  const debits = lines.reduce((s, l) => s + Number(l.debit || 0), 0)
  const credits = lines.reduce((s, l) => s + Number(l.credit || 0), 0)
  if (Math.abs(debits - credits) > 0.005) return ElMessage.warning('借贷不平衡，请检查金额')
  await ledgerApi.createVoucher({
    voucher_date: voucherForm.value.voucher_date,
    summary: voucherForm.value.summary,
    lines: lines.map((l) => ({ account_code: l.account_code, summary: l.summary, debit: Number(l.debit), credit: Number(l.credit) })),
  })
  ElMessage.success('凭证已保存')
  voucherDialog.value = false
  voucherForm.value = { summary: '', voucher_date: new Date().toISOString().slice(0, 10), lines: [{ account_code: '', summary: '', debit: 0, credit: 0 }] }
  await loadVouchers()
  await loadStatements()
}

onMounted(refresh)
</script>

<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h1 class="page-title">总账与报表</h1>
        <p class="page-subtitle">会计科目、复式记账凭证与三大财务报表（中国小企业会计准则）</p>
      </div>
      <div class="head-actions">
        <el-date-picker v-model="period" type="month" value-format="YYYY-MM" placeholder="选择期间" style="width: 140px" :clearable="false" @change="refresh" />
        <el-button @click="refresh"><el-icon><Refresh /></el-icon>&nbsp;刷新</el-button>
      </div>
    </div>

    <el-tabs v-model="activeTab" class="card">
      <!-- 会计科目 -->
      <el-tab-pane label="会计科目" name="accounts">
        <div class="panel-head">
          <h3>科目余额表（{{ period }}）</h3>
          <el-button v-if="canManage" size="small" type="primary" @click="voucherDialog = true"><el-icon><Plus /></el-icon>&nbsp;记一笔</el-button>
        </div>
        <el-table :data="accounts" size="small">
          <el-table-column prop="code" label="编码" width="90" />
          <el-table-column prop="name" label="科目名称" min-width="180" />
          <el-table-column label="类别" width="120" align="center">
            <template #default="{ row }"><el-tag :type="catTag(row.category)" size="small" effect="plain">{{ row.category }}</el-tag></template>
          </el-table-column>
          <el-table-column label="方向" width="80" align="center">
            <template #default="{ row }">{{ row.direction === 'dr' ? '借方' : '贷方' }}</template>
          </el-table-column>
          <el-table-column label="级次" width="70" align="center">
            <template #default="{ row }">{{ row.level }} 级</template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <!-- 记账凭证 -->
      <el-tab-pane label="记账凭证" name="vouchers">
        <div class="panel-head">
          <h3>凭证列表（{{ period }}）</h3>
          <div>
            <el-button v-if="canManage" size="small" type="warning" plain @click="onGenerate"><el-icon><MagicStick /></el-icon>&nbsp;自动生成凭证</el-button>
            <el-button v-if="canManage" size="small" type="primary" @click="voucherDialog = true"><el-icon><Plus /></el-icon>&nbsp;手工凭证</el-button>
          </div>
        </div>
        <el-table v-loading="loading" :data="vouchers" size="small" row-key="id">
          <el-table-column type="expand">
            <template #default="{ row }">
              <div class="line-table">
                <table>
                  <thead>
                    <tr><th>科目</th><th>摘要</th><th class="num">借方</th><th class="num">贷方</th><th>往来对象</th></tr>
                  </thead>
                  <tbody>
                    <tr v-for="ln in row.lines" :key="ln.id">
                      <td>{{ ln.account_code }} {{ ln.account_name }}</td>
                      <td>{{ ln.summary }}</td>
                      <td class="num">{{ ln.debit ? money(ln.debit) : '' }}</td>
                      <td class="num">{{ ln.credit ? money(ln.credit) : '' }}</td>
                      <td>{{ ln.partner_name }}</td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </template>
          </el-table-column>
          <el-table-column prop="voucher_no" label="凭证号" width="150" />
          <el-table-column prop="voucher_date" label="日期" width="110">
            <template #default="{ row }">{{ (row.voucher_date || '').slice(0, 10) }}</template>
          </el-table-column>
          <el-table-column prop="summary" label="摘要" min-width="200" />
          <el-table-column label="借方" width="120" align="right">
            <template #default="{ row }">{{ money(row.total_debit) }}</template>
          </el-table-column>
          <el-table-column label="贷方" width="120" align="right">
            <template #default="{ row }">{{ money(row.total_credit) }}</template>
          </el-table-column>
          <el-table-column label="来源" width="110" align="center">
            <template #default="{ row }">
              <el-tag v-if="row.source_type === 'manual'" size="small">手工</el-tag>
              <el-tag v-else size="small" type="info" effect="plain">自动</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="90" align="center">
            <template #default="{ row }">
              <el-button v-if="canManage && row.status !== 'voided'" link type="danger" size="small" @click="onVoid(row)">作废</el-button>
              <el-tag v-else-if="row.status === 'voided'" type="danger" size="small">已作废</el-tag>
            </template>
          </el-table-column>
        </el-table>
        <div v-if="!vouchers.length" class="empty-text">该期间暂无凭证，点击「自动生成凭证」从单据过账，或手工录入</div>
      </el-tab-pane>

      <!-- 财务报表 -->
      <el-tab-pane label="财务报表" name="statements">
        <div class="report-grid">
          <div class="card report-card">
            <div class="panel-head"><h3>资产负债表</h3><span class="hint">{{ period || '累计' }}</span></div>
            <table v-if="balanceSheet" class="report-table">
              <tbody>
                <tr><th colspan="2">资产</th></tr>
                <tr v-for="r in balanceSheet.assets" :key="r.code + r.name"><td>{{ r.name }}</td><td class="num">{{ money(r.amount) }}</td></tr>
                <tr class="total"><td>资产总计</td><td class="num">{{ money(balanceSheet.asset_total) }}</td></tr>
                <tr><th colspan="2">负债</th></tr>
                <tr v-for="r in balanceSheet.liabilities" :key="r.code + r.name"><td>{{ r.name }}</td><td class="num">{{ money(r.amount) }}</td></tr>
                <tr class="total"><td>负债总计</td><td class="num">{{ money(balanceSheet.liability_total) }}</td></tr>
                <tr><th colspan="2">所有者权益</th></tr>
                <tr v-for="r in balanceSheet.equity" :key="r.code + r.name"><td>{{ r.name }}</td><td class="num">{{ money(r.amount) }}</td></tr>
                <tr class="total"><td>权益总计</td><td class="num">{{ money(balanceSheet.equity_total) }}</td></tr>
                <tr class="balanced"><td colspan="2">{{ balanceSheet.balanced ? '✓ 试算平衡' : '✗ 借贷不平衡，请检查凭证' }}</td></tr>
              </tbody>
            </table>
          </div>

          <div class="card report-card">
            <div class="panel-head"><h3>利润表</h3><span class="hint">{{ period || '累计' }}</span></div>
            <table v-if="incomeStatement" class="report-table">
              <tbody>
                <tr><td>一、营业收入</td><td class="num">{{ money(incomeStatement.revenue) }}</td></tr>
                <tr><td>减：营业成本</td><td class="num">{{ money(incomeStatement.cost) }}</td></tr>
                <tr><td>税金及附加</td><td class="num">{{ money(incomeStatement.tax_surcharge) }}</td></tr>
                <tr><td>销售费用</td><td class="num">{{ money(incomeStatement.sales_expense) }}</td></tr>
                <tr><td>管理费用</td><td class="num">{{ money(incomeStatement.admin_expense) }}</td></tr>
                <tr><td>财务费用</td><td class="num">{{ money(incomeStatement.finance_expense) }}</td></tr>
                <tr class="total"><td>二、营业利润</td><td class="num">{{ money(incomeStatement.operating_profit) }}</td></tr>
                <tr><td>加：营业外收入</td><td class="num">{{ money(incomeStatement.non_op_income) }}</td></tr>
                <tr><td>减：营业外支出</td><td class="num">{{ money(incomeStatement.non_op_expense) }}</td></tr>
                <tr class="total"><td>三、利润总额</td><td class="num">{{ money(incomeStatement.total_profit) }}</td></tr>
                <tr><td>减：所得税费用</td><td class="num">{{ money(incomeStatement.income_tax) }}</td></tr>
                <tr class="total strong"><td>四、净利润</td><td class="num">{{ money(incomeStatement.net_profit) }}</td></tr>
              </tbody>
            </table>
          </div>

          <div class="card report-card">
            <div class="panel-head"><h3>现金流量表</h3><span class="hint">{{ period || '累计' }}</span></div>
            <table v-if="cashFlow" class="report-table">
              <tbody>
                <tr><th colspan="2">一、经营活动（直接法）</th></tr>
                <tr><td>销售商品收到的现金</td><td class="num">{{ money(cashFlow.operating_inflow) }}</td></tr>
                <tr><td>购买商品支付的现金</td><td class="num">{{ money(cashFlow.operating_outflow) }}</td></tr>
                <tr class="total"><td>经营活动现金流量净额</td><td class="num">{{ money(cashFlow.operating_net) }}</td></tr>
                <tr><th colspan="2">二、投资活动</th></tr>
                <tr><td>投资活动现金流入</td><td class="num">{{ money(cashFlow.investing_inflow) }}</td></tr>
                <tr><td>投资活动现金流出</td><td class="num">{{ money(cashFlow.investing_outflow) }}</td></tr>
                <tr class="total"><td>投资活动现金流量净额</td><td class="num">{{ money(cashFlow.investing_net) }}</td></tr>
                <tr><th colspan="2">三、筹资活动</th></tr>
                <tr><td>筹资活动现金流入</td><td class="num">{{ money(cashFlow.financing_inflow) }}</td></tr>
                <tr><td>筹资活动现金流出</td><td class="num">{{ money(cashFlow.financing_outflow) }}</td></tr>
                <tr class="total"><td>筹资活动现金流量净额</td><td class="num">{{ money(cashFlow.financing_net) }}</td></tr>
                <tr class="total strong"><td>现金及现金等价物净增加额</td><td class="num">{{ money(cashFlow.net_increase) }}</td></tr>
              </tbody>
            </table>
          </div>
        </div>
      </el-tab-pane>

      <!-- 期末结账 -->
      <el-tab-pane label="期末结账" name="periods">
        <div class="panel-head">
          <h3>会计期间</h3>
          <span class="hint">结账后该期间凭证不可再修改，损益类余额结转到本年利润（3103）</span>
        </div>
        <el-table :data="periods" size="small" border>
          <el-table-column prop="period" label="期间" width="120" />
          <el-table-column label="状态" width="90" align="center">
            <template #default="{ row }">
              <el-tag size="small" :type="row.status === 'closed' ? 'success' : 'info'">
                {{ row.status === 'closed' ? '已结账' : '未结账' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="carry_voucher_no" label="结转损益凭证" width="170">
            <template #default="{ row }">{{ row.carry_voucher_no || '—' }}</template>
          </el-table-column>
          <el-table-column prop="closed_by" label="结账人" width="110" />
          <el-table-column label="结账时间" width="170">
            <template #default="{ row }">{{ (row.closed_at || '').replace('T', ' ').slice(0, 19) }}</template>
          </el-table-column>
          <el-table-column label="操作" width="160" align="center">
            <template #default="{ row }">
              <el-button
                link
                type="info"
                size="small"
                @click="onCheckPeriod(row)"
              >体检</el-button>
              <el-button
                v-if="row.status === 'open'"
                link
                type="primary"
                size="small"
                :disabled="!canClose"
                @click="onClosePeriod(row)"
              >结账</el-button>
              <el-button
                v-else
                link
                type="warning"
                size="small"
                :disabled="!canClose"
                @click="onReopenPeriod(row)"
              >反结账</el-button>
            </template>
          </el-table-column>
          <template #empty><el-empty description="暂无会计期间" /></template>
        </el-table>
      </el-tab-pane>
    </el-tabs>

    <!-- 结账前体检对话框 -->
    <el-dialog v-model="checkDialog" :title="`结账前体检 · ${checkPeriod}`" width="560px">
      <div v-if="checkResult">
        <el-alert
          :type="checkResult.passed ? 'success' : 'error'"
          :title="checkResult.passed ? '体检通过，可以结账' : '体检未通过，存在阻断项'"
          :closable="false"
          show-icon
        />
        <div class="check-list">
          <div v-for="item in checkResult.items" :key="item.key" class="check-item">
            <el-icon :class="item.passed ? 'ok' : 'bad'">
              <CircleCheck v-if="item.passed" />
              <CircleClose v-else />
            </el-icon>
            <div class="check-main">
              <div class="check-label">{{ item.label }}</div>
              <div class="check-hint">{{ item.hint }}</div>
            </div>
          </div>
        </div>
      </div>
      <template #footer>
        <el-button @click="checkDialog = false">知道了</el-button>
      </template>
    </el-dialog>

    <!-- 手工凭证对话框 -->
    <el-dialog v-model="voucherDialog" title="手工记账凭证" width="680px">
      <el-form label-width="80px">
        <el-row :gutter="12">
          <el-col :span="12">
            <el-form-item label="凭证日期">
              <el-date-picker v-model="voucherForm.voucher_date" type="date" value-format="YYYY-MM-DD" style="width: 100%" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="摘要">
              <el-input v-model="voucherForm.summary" placeholder="凭证摘要" />
            </el-form-item>
          </el-col>
        </el-row>

        <div v-for="(line, i) in voucherForm.lines" :key="i" class="vline-row">
          <el-select v-model="line.account_code" filterable placeholder="科目" class="vline-acc">
            <el-option v-for="a in accounts" :key="a.code" :label="`${a.code} ${a.name}`" :value="a.code" />
          </el-select>
          <el-input v-model="line.summary" placeholder="摘要" class="vline-sum" />
          <el-input-number v-model="line.debit" :min="0" :precision="2" :controls="false" placeholder="借方" class="vline-num" />
          <el-input-number v-model="line.credit" :min="0" :precision="2" :controls="false" placeholder="贷方" class="vline-num" />
          <el-button link type="danger" @click="removeLine(i)">删</el-button>
        </div>
        <el-button link type="primary" @click="addLine"><el-icon><Plus /></el-icon>&nbsp;添加分录</el-button>
      </el-form>
      <template #footer>
        <el-button @click="voucherDialog = false">取消</el-button>
        <el-button type="primary" @click="submitVoucher">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.head-actions {
  display: flex;
  align-items: center;
  gap: 10px;
}

.panel-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 10px;
}

.panel-head h3 {
  margin: 0;
  font-size: 15px;
}

.check-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin-top: 14px;
}

.check-item {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 10px 12px;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: var(--bg);
}

.check-item .el-icon {
  font-size: 18px;
  margin-top: 1px;
}

.check-item .el-icon.ok {
  color: #16a34a;
}

.check-item .el-icon.bad {
  color: #ef4444;
}

.check-label {
  font-size: 13.5px;
  font-weight: 600;
  color: var(--text);
}

.check-hint {
  margin-top: 2px;
  font-size: 12.5px;
  color: var(--text-secondary);
}

.line-table {
  padding: 4px 20px;
}

.line-table table,
.report-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}

.line-table th,
.line-table td,
.report-table th,
.report-table td {
  padding: 7px 10px;
  border-bottom: 1px solid var(--border);
  text-align: left;
}

.line-table th {
  color: var(--text-secondary);
  font-weight: 500;
}

.report-table .num,
.line-table .num {
  text-align: right;
  font-variant-numeric: tabular-nums;
}

.report-table th {
  background: #f8fafc;
  color: var(--text-secondary);
}

.report-table tr.total td {
  font-weight: 600;
  border-top: 2px solid var(--border);
}

.report-table tr.strong td {
  color: var(--success);
  font-size: 14px;
}

.report-table tr.balanced td {
  text-align: center;
  color: var(--success);
  font-weight: 600;
  padding-top: 10px;
  border: none;
}

.report-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 14px;
}

.report-card {
  align-self: start;
}

.vline-row {
  display: flex;
  gap: 6px;
  align-items: center;
  margin-bottom: 6px;
}

.vline-acc {
  flex: 1.4;
}

.vline-sum {
  flex: 1;
}

.vline-num {
  width: 110px;
}

.empty-text {
  padding: 24px;
  text-align: center;
  color: var(--text-secondary);
  font-size: 13px;
}

@media (max-width: 1200px) {
  .report-grid {
    grid-template-columns: 1fr;
  }
}
</style>