<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { invoiceApi } from '../api'
import { saveBlob } from '../utils/download'

const loading = ref(false)
const rows = ref([])
const total = ref(0)
const selection = ref([])
const summary = ref({ sales: { count: 0, total: 0 }, purchase: { count: 0, total: 0 }, pending_verify: 0 })

const query = reactive({ direction: '', keyword: '', status: '', page: 1, page_size: 20 })

const dialogVisible = ref(false)
const saving = ref(false)
const importing = ref(false)
const exporting = ref(false)
const printing = ref(false)
const reviewing = ref(false)
const batchEditVisible = ref(false)
const batchSaving = ref(false)
const batchForm = reactive({ direction: '', tax_rate: null, issue_date: '', remark: '' })
const editingId = ref(null)
const form = reactive({
  invoice_no: '',
  direction: 'sales',
  partner_name: '',
  order_no: '',
  amount: 0,
  tax_rate: 13,
  tax_amount: 0,
  total_amount: 0,
  issue_date: '',
  remark: '',
})

const directionText = { sales: '销项发票', purchase: '进项发票' }

function statusText(row) {
  if (row.status === 'issued') return row.direction === 'purchase' ? '已收票' : '已开票'
  if (row.status === 'voided') return '已作废'
  return row.direction === 'purchase' ? '未收票' : '未开票'
}
function statusType(row) {
  if (row.status === 'voided') return 'danger'
  if (row.status === 'issued') return 'success'
  return 'info'
}

const emptyForm = () => {
  editingId.value = null
  form.invoice_no = ''
  form.direction = 'sales'
  form.partner_name = ''
  form.order_no = ''
  form.amount = 0
  form.tax_rate = 13
  form.tax_amount = 0
  form.total_amount = 0
  form.issue_date = ''
  form.remark = ''
}

watch(
  () => [form.amount, form.tax_rate],
  () => {
    form.tax_amount = Math.round(form.amount * (form.tax_rate / 100) * 100) / 100
    form.total_amount = Math.round((form.amount + form.tax_amount) * 100) / 100
  },
)

async function load() {
  loading.value = true
  try {
    const [data, s] = await Promise.all([invoiceApi.list(query), invoiceApi.summary()])
    rows.value = data.items || []
    total.value = data.total || 0
    summary.value = s
  } finally {
    loading.value = false
  }
}

function openCreate() {
  emptyForm()
  dialogVisible.value = true
}

function openEdit(row) {
  editingId.value = row.id
  form.invoice_no = row.invoice_no
  form.direction = row.direction
  form.partner_name = row.partner_name
  form.order_no = row.order_no
  form.amount = row.amount
  form.tax_rate = row.tax_rate
  form.tax_amount = row.tax_amount
  form.total_amount = row.total_amount
  form.issue_date = row.issue_date ? row.issue_date.slice(0, 10) : ''
  form.remark = row.remark
  dialogVisible.value = true
}

async function save() {
  if (!form.invoice_no.trim()) {
    ElMessage.warning('请填写发票号码')
    return
  }
  saving.value = true
  try {
    if (editingId.value) {
      await invoiceApi.update(editingId.value, { ...form })
      ElMessage.success('已保存')
    } else {
      await invoiceApi.create({ ...form })
      ElMessage.success('已登记')
    }
    dialogVisible.value = false
    await load()
  } finally {
    saving.value = false
  }
}

async function changeStatus(row, action) {
  if (action === 'void') {
    try {
      await ElMessageBox.confirm(`作废发票「${row.invoice_no}」？`, '作废确认', {
        type: 'warning',
        confirmButtonText: '作废',
        cancelButtonText: '取消',
      })
    } catch {
      return
    }
  }
  await invoiceApi.changeStatus(row.id, action)
  ElMessage.success('已更新')
  await load()
}

async function removeRow(row) {
  try {
    await ElMessageBox.confirm(`删除发票「${row.invoice_no}」？`, '删除确认', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }
  await invoiceApi.remove(row.id)
  ElMessage.success('已删除')
  await load()
}

function onSelectionChange(rows) {
  selection.value = rows
}

async function batchRemove() {
  const ids = selection.value.map((r) => r.id)
  if (!ids.length) {
    ElMessage.warning('请先勾选要删除的发票')
    return
  }
  try {
    await ElMessageBox.confirm(`确定删除选中的 ${ids.length} 张发票？删除后不可恢复。`, '批量删除确认', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }
  const r = await invoiceApi.batchRemove(ids)
  ElMessage.success(`已删除 ${r.deleted} 张`)
  await load()
}

async function batchExport() {
  const ids = selection.value.map((r) => r.id)
  if (!ids.length) {
    ElMessage.warning('请先勾选要导出的发票')
    return
  }
  exporting.value = true
  try {
    const blob = await invoiceApi.batchExport(ids)
    saveBlob(blob, `发票导出_${ids.length}张.xlsx`)
  } finally {
    exporting.value = false
  }
}

async function batchPrint() {
  const ids = selection.value.map((r) => r.id)
  if (!ids.length) {
    ElMessage.warning('请先勾选要打印的发票')
    return
  }
  printing.value = true
  try {
    const blob = await invoiceApi.batchPrint(ids)
    saveBlob(blob, `发票打印_${ids.length}张.docx`)
  } finally {
    printing.value = false
  }
}

async function batchReview() {
  const ids = selection.value.map((r) => r.id)
  if (!ids.length) {
    ElMessage.warning('请先勾选要审核的发票')
    return
  }
  reviewing.value = true
  try {
    const r = await invoiceApi.batchReview(ids)
    ElMessage.success(`已审核 ${r.reviewed} 张发票`)
    await load()
  } finally {
    reviewing.value = false
  }
}

function openBatchEdit() {
  if (!selection.value.length) {
    ElMessage.warning('请先勾选要编辑的发票')
    return
  }
  batchForm.direction = ''
  batchForm.tax_rate = null
  batchForm.issue_date = ''
  batchForm.remark = ''
  batchEditVisible.value = true
}

async function submitBatchEdit() {
  const ids = selection.value.map((r) => r.id)
  const data = {}
  if (batchForm.direction) data.direction = batchForm.direction
  if (batchForm.tax_rate !== null && batchForm.tax_rate !== '') data.tax_rate = Number(batchForm.tax_rate)
  if (batchForm.issue_date) data.issue_date = batchForm.issue_date
  if (batchForm.remark !== '') data.remark = batchForm.remark
  if (!Object.keys(data).length) {
    ElMessage.warning('请至少填写一个要更新的字段')
    return
  }
  batchSaving.value = true
  try {
    const r = await invoiceApi.batchUpdate(ids, data)
    ElMessage.success(`已更新 ${r.updated} 张发票`)
    batchEditVisible.value = false
    await load()
  } finally {
    batchSaving.value = false
  }
}

function downloadTemplate() {
  invoiceApi.template().then((blob) => saveBlob(blob, '发票导入模板.xlsx'))
}

async function handleImport(uploadFile) {
  const file = uploadFile.raw
  if (!file) return
  importing.value = true
  try {
    const r = await invoiceApi.import(file)
    if (r.errors && r.errors.length) {
      const lines = r.errors.map((e) => `第 ${e.row} 行：${e.reason}`).join('<br/>')
      ElMessageBox.alert(
        `成功 ${r.created} 条，失败 ${r.skipped} 条<br/><br/>${lines}`,
        '导入结果',
        { dangerouslyUseHTMLString: true, confirmButtonText: '知道了' },
      )
    } else {
      ElMessage.success(`成功导入 ${r.created} 条`)
    }
    await load()
  } finally {
    importing.value = false
  }
}

const money = (v) => Number(v || 0).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })

onMounted(load)
</script>

<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h1 class="page-title">发票管理</h1>
        <p class="page-subtitle">统一登记销项（开给客户）与进项（供应商开入）发票，进项支持认证标记。</p>
      </div>
      <div class="page-actions">
        <el-button type="primary" @click="openCreate">登记发票</el-button>
      </div>
    </div>

    <el-row :gutter="16" style="margin-bottom: 16px">
      <el-col :span="8">
        <el-card shadow="never">
          <div class="stat-label">销项合计</div>
          <div class="stat-value">¥{{ money(summary.sales.total) }}</div>
          <div class="stat-sub">{{ summary.sales.count }} 张</div>
        </el-card>
      </el-col>
      <el-col :span="8">
        <el-card shadow="never">
          <div class="stat-label">进项合计</div>
          <div class="stat-value">¥{{ money(summary.purchase.total) }}</div>
          <div class="stat-sub">{{ summary.purchase.count }} 张</div>
        </el-card>
      </el-col>
      <el-col :span="8">
        <el-card shadow="never">
          <div class="stat-label">待认证进项</div>
          <div class="stat-value">{{ summary.pending_verify }} 张</div>
          <div class="stat-sub">已收票未认证</div>
        </el-card>
      </el-col>
    </el-row>

    <div class="toolbar">
      <el-select v-model="query.direction" placeholder="方向" clearable style="width: 140px" @change="load">
        <el-option label="销项发票" value="sales" />
        <el-option label="进项发票" value="purchase" />
      </el-select>
      <el-select v-model="query.status" placeholder="状态" clearable style="width: 140px" @change="load">
        <el-option label="未开票" value="pending" />
        <el-option label="已开票" value="issued" />
        <el-option label="已作废" value="voided" />
      </el-select>
      <el-input
        v-model="query.keyword"
        placeholder="搜索发票号 / 往来方 / 单据号"
        clearable
        style="width: 240px"
        @keyup.enter="load"
        @clear="load"
      />
      <el-button type="primary" @click="load">查询</el-button>
      <el-button @click="downloadTemplate">下载模板</el-button>
      <el-upload :auto-upload="false" :show-file-list="false" accept=".xlsx,.xls" :on-change="handleImport">
        <el-button type="success" :loading="importing">批量导入</el-button>
      </el-upload>
      <el-button type="danger" :disabled="!selection.length" @click="batchRemove">批量删除</el-button>
      <el-button :disabled="!selection.length" @click="openBatchEdit">批量编辑</el-button>
      <el-button :disabled="!selection.length" :loading="exporting" @click="batchExport">批量导出</el-button>
      <el-button :disabled="!selection.length" :loading="printing" @click="batchPrint">批量打印</el-button>
      <el-button :disabled="!selection.length" :loading="reviewing" @click="batchReview">批量审核</el-button>
    </div>

    <el-table :data="rows" v-loading="loading" border @selection-change="onSelectionChange">
      <el-table-column type="selection" width="44" />
      <el-table-column prop="invoice_no" label="发票号码" width="160" />
      <el-table-column label="方向" width="100">
        <template #default="{ row }">{{ directionText[row.direction] }}</template>
      </el-table-column>
      <el-table-column prop="partner_name" label="往来方" min-width="140" />
      <el-table-column prop="order_no" label="关联单据" width="140" />
      <el-table-column label="不含税" width="120" align="right">
        <template #default="{ row }">{{ money(row.amount) }}</template>
      </el-table-column>
      <el-table-column label="税率" width="70" align="right">
        <template #default="{ row }">{{ row.tax_rate }}%</template>
      </el-table-column>
      <el-table-column label="价税合计" width="130" align="right">
        <template #default="{ row }">{{ money(row.total_amount) }}</template>
      </el-table-column>
      <el-table-column label="状态" width="90">
        <template #default="{ row }">
          <el-tag :type="statusType(row)" size="small">{{ statusText(row) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="认证" width="80">
        <template #default="{ row }">
          <el-tag v-if="row.direction === 'purchase' && row.certified" type="success" size="small">已认证</el-tag>
          <span v-else>-</span>
        </template>
      </el-table-column>
      <el-table-column label="审核" width="90">
        <template #default="{ row }">
          <el-tag v-if="row.reviewed" type="success" size="small">{{ row.reviewed_by ? `已审核·${row.reviewed_by}` : '已审核' }}</el-tag>
          <span v-else>未审核</span>
        </template>
      </el-table-column>
      <el-table-column label="开票日期" width="120">
        <template #default="{ row }">{{ row.issue_date ? row.issue_date.slice(0, 10) : '' }}</template>
      </el-table-column>
      <el-table-column label="操作" width="230" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" @click="openEdit(row)">编辑</el-button>
          <el-button v-if="row.status !== 'issued'" link type="success" @click="changeStatus(row, 'issue')">
            {{ row.direction === 'purchase' ? '收票' : '开票' }}
          </el-button>
          <el-button
            v-if="row.direction === 'purchase' && row.status === 'issued' && !row.certified"
            link
            type="success"
            @click="changeStatus(row, 'certify')"
          >
            认证
          </el-button>
          <el-button v-if="row.status !== 'voided'" link type="warning" @click="changeStatus(row, 'void')">作废</el-button>
          <el-button link type="danger" @click="removeRow(row)">删除</el-button>
        </template>
      </el-table-column>
      <template #empty><el-empty description="暂无发票记录" /></template>
    </el-table>

    <el-pagination
      v-model:current-page="query.page"
      v-model:page-size="query.page_size"
      :total="total"
      :page-sizes="[10, 20, 50, 100]"
      layout="total, sizes, prev, pager, next"
      style="margin-top: 16px; justify-content: flex-end"
      @current-change="load"
      @size-change="load"
    />

    <el-dialog v-model="dialogVisible" :title="editingId ? '编辑发票' : '登记发票'" width="560px">
      <el-form :model="form" label-width="90px">
        <el-form-item label="发票号码" required>
          <el-input v-model="form.invoice_no" placeholder="如 01234567" />
        </el-form-item>
        <el-form-item label="方向">
          <el-radio-group v-model="form.direction">
            <el-radio-button value="sales">销项</el-radio-button>
            <el-radio-button value="purchase">进项</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="往来方">
          <el-input v-model="form.partner_name" placeholder="客户或供应商名称" />
        </el-form-item>
        <el-form-item label="关联单据">
          <el-input v-model="form.order_no" placeholder="销售/采购订单号（可选）" />
        </el-form-item>
        <el-form-item label="不含税">
          <el-input-number v-model="form.amount" :min="0" :precision="2" style="width: 100%" />
        </el-form-item>
        <el-form-item label="税率(%)">
          <el-input-number v-model="form.tax_rate" :min="0" :max="100" :precision="2" style="width: 100%" />
        </el-form-item>
        <el-form-item label="税额">
          <el-input-number v-model="form.tax_amount" :min="0" :precision="2" style="width: 100%" />
        </el-form-item>
        <el-form-item label="价税合计">
          <el-input-number v-model="form.total_amount" :min="0" :precision="2" style="width: 100%" />
        </el-form-item>
        <el-form-item label="开票日期">
          <el-date-picker v-model="form.issue_date" type="date" value-format="YYYY-MM-DD" placeholder="选择日期" style="width: 100%" />
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="form.remark" type="textarea" :rows="2" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="batchEditVisible" title="批量编辑发票" width="480px">
      <el-alert
        type="info"
        :closable="false"
        show-icon
        title="仅更新下方已填写的字段；留空的字段保持原样"
        style="margin-bottom: 16px"
      />
      <el-form label-width="90px">
        <el-form-item label="方向">
          <el-select v-model="batchForm.direction" placeholder="不修改" clearable style="width: 100%">
            <el-option label="销项" value="sales" />
            <el-option label="进项" value="purchase" />
          </el-select>
        </el-form-item>
        <el-form-item label="税率(%)">
          <el-input-number v-model="batchForm.tax_rate" :min="0" :max="100" :precision="2" style="width: 100%" />
        </el-form-item>
        <el-form-item label="开票日期">
          <el-date-picker v-model="batchForm.issue_date" type="date" value-format="YYYY-MM-DD" placeholder="不修改" style="width: 100%" />
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="batchForm.remark" type="textarea" :rows="2" placeholder="留空则不修改" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="batchEditVisible = false">取消</el-button>
        <el-button type="primary" :loading="batchSaving" @click="submitBatchEdit">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.toolbar {
  display: flex;
  gap: 8px;
  margin-bottom: 16px;
  flex-wrap: wrap;
}
.stat-label { color: #909399; font-size: 12px; }
.stat-value { font-size: 22px; font-weight: 600; margin: 4px 0; }
.stat-sub { color: #a8abb2; font-size: 12px; }
</style>