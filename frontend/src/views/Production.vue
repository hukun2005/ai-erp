<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { bomApi, productionApi, productApi, warehouseApi } from '../api'

const loading = ref(false)
const list = ref([])
const total = ref(0)
const page = ref(1)
const query = reactive({ keyword: '', status: '' })

const products = ref([])
const warehouses = ref([])

const dialogVisible = ref(false)
const detailVisible = ref(false)
const current = ref(null)
const tab = ref('materials')
const form = reactive({
  product_id: null,
  quantity: 1,
  warehouse_id: null,
  plan_start: null,
  plan_end: null,
  remark: '',
})
const bomPreview = ref(null)
const expenseForm = reactive({ category: '制造费用', amount: 0, remark: '' })

const statusMap = {
  draft: { text: '草稿', type: 'info' },
  released: { text: '已下达', type: 'warning' },
  in_progress: { text: '生产中', type: 'primary' },
  completed: { text: '已完工', type: 'success' },
  cancelled: { text: '已取消', type: 'danger' },
}

const activeCount = computed(() => list.value.filter((r) => ['released', 'in_progress'].includes(r.status)).length)

async function load() {
  loading.value = true
  try {
    const data = await productionApi.list({ ...query, page: page.value, page_size: 20 })
    list.value = data.items
    total.value = data.total
  } finally {
    loading.value = false
  }
}

function search() {
  page.value = 1
  load()
}

async function openCreate() {
  Object.assign(form, {
    product_id: null,
    quantity: 1,
    warehouse_id: warehouses.value.find((w) => w.is_default)?.id || warehouses.value[0]?.id || null,
    plan_start: null,
    plan_end: null,
    remark: '',
  })
  bomPreview.value = null
  dialogVisible.value = true
}

async function onProductChange() {
  bomPreview.value = form.product_id ? await bomApi.activeFor(form.product_id) : null
}

async function submit() {
  if (!form.product_id || form.quantity <= 0 || !form.warehouse_id) {
    return ElMessage.warning('请填写成品、数量与入库仓库')
  }
  await productionApi.create({ ...form })
  ElMessage.success('生产订单已创建（草稿），下达后自动展开 BOM 用料')
  dialogVisible.value = false
  load()
}

async function refreshCurrent() {
  if (current.value) current.value = await productionApi.detail(current.value.id)
}

async function viewDetail(row) {
  current.value = await productionApi.detail(row.id)
  tab.value = 'materials'
  detailVisible.value = true
}

async function act(row, action, label) {
  await ElMessageBox.confirm(`确定执行「${label}」吗？`, '操作确认', { type: 'warning' })
  try {
    await productionApi.changeStatus(row.id, action)
    ElMessage.success(`${label}成功`)
    await refreshCurrent()
    load()
  } catch (e) {
    if (e?.response) ElMessage.error(e.response.data?.detail || '操作失败')
  }
}

async function issue(row) {
  const wh = row.warehouse_id || warehouses.value.find((w) => w.is_default)?.id
  await ElMessageBox.confirm('将按未领数量整单领料并扣减库存，确定继续吗？', '生产领料', { type: 'warning' })
  try {
    await productionApi.issue(row.id, { items: [], warehouse_id: wh })
    ElMessage.success('领料完成')
    await refreshCurrent()
    load()
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '领料失败')
  }
}

async function scan(step) {
  try {
    await productionApi.scanProcess(current.value.id, step.id)
    ElMessage.success(`${step.name} 报工完成`)
    await refreshCurrent()
    load()
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '扫码失败')
  }
}

async function addExpense() {
  if (expenseForm.amount <= 0) return ElMessage.warning('请输入费用金额')
  try {
    current.value = await productionApi.addExpense(current.value.id, { ...expenseForm })
    expenseForm.amount = 0
    expenseForm.remark = ''
    ElMessage.success('费用已登记')
    load()
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '登记失败')
  }
}

async function removeExpense(row) {
  await ElMessageBox.confirm('确定删除这笔费用吗？', '删除确认', { type: 'warning' })
  current.value = await productionApi.removeExpense(current.value.id, row.id)
  load()
}

async function remove(row) {
  await ElMessageBox.confirm(`确定删除草稿生产订单 ${row.order_no} 吗？`, '删除确认', { type: 'warning' })
  await productionApi.remove(row.id)
  ElMessage.success('已删除')
  load()
}

function money(value) {
  return '¥' + Number(value || 0).toLocaleString('zh-CN', { minimumFractionDigits: 2 })
}

function shortTime(value) {
  return value ? String(value).replace('T', ' ').slice(0, 16) : '—'
}

onMounted(async () => {
  load()
  const [p, w] = await Promise.all([
    productApi.list({ page_size: 200, product_type: 'finished_good' }),
    warehouseApi.options(),
  ])
  products.value = p.items
  warehouses.value = w
})
</script>

<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h1 class="page-title">生产订单</h1>
        <p class="page-subtitle">下达展开 BOM 用料 → 生产领料 → 工序扫码 → 完工入库；入库成本自动回写商品成本</p>
      </div>
      <el-button v-perm="'production:manage'" type="primary" @click="openCreate">
        <el-icon><Plus /></el-icon>&nbsp;新建生产订单
      </el-button>
    </div>

    <div class="stat-row">
      <span>生产中订单：<b>{{ activeCount }}</b></span>
      <span>共 {{ total }} 张生产订单</span>
    </div>

    <div class="card">
      <div class="toolbar">
        <el-input v-model="query.keyword" placeholder="搜索生产单号 / 成品" clearable style="width: 240px" @keyup.enter="search" @clear="search" />
        <el-select v-model="query.status" placeholder="全部状态" clearable style="width: 140px" @change="search">
          <el-option v-for="(v, k) in statusMap" :key="k" :label="v.text" :value="k" />
        </el-select>
        <el-button @click="search">查询</el-button>
      </div>

      <el-table :data="list" v-loading="loading">
        <el-table-column prop="order_no" label="生产单号" width="160" />
        <el-table-column prop="product_name" label="成品" min-width="150" show-overflow-tooltip />
        <el-table-column prop="quantity" label="数量" width="80" align="right" />
        <el-table-column prop="warehouse_name" label="入库仓" width="130" show-overflow-tooltip />
        <el-table-column label="工序进度" width="150">
          <template #default="{ row }">
            <el-progress :percentage="row.progress" :stroke-width="8" />
            <span class="sub-text">{{ row.process_done }}/{{ row.process_total }}</span>
          </template>
        </el-table-column>
        <el-table-column label="生产成本" width="120" align="right">
          <template #default="{ row }">{{ money(row.total_cost) }}</template>
        </el-table-column>
        <el-table-column label="状态" width="90" align="center">
          <template #default="{ row }">
            <el-tag :type="statusMap[row.status]?.type" size="small">{{ statusMap[row.status]?.text }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="360" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="viewDetail(row)">详情</el-button>
            <el-button v-if="row.status === 'draft'" v-perm="'production:manage'" link type="success" @click="act(row, 'release', '下达生产')">下达</el-button>
            <el-button v-if="['released', 'in_progress'].includes(row.status)" v-perm="'production:manage'" link type="warning" @click="issue(row)">领料</el-button>
            <el-button v-if="['released', 'in_progress'].includes(row.status)" v-perm="'production:manage'" link type="success" @click="act(row, 'complete', '完工入库')">完工</el-button>
            <el-button v-if="['draft', 'released', 'in_progress'].includes(row.status)" v-perm="'production:manage'" link type="info" @click="act(row, 'cancel', '取消生产')">取消</el-button>
            <el-button v-if="row.status === 'draft'" v-perm="'production:manage'" link type="danger" @click="remove(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>

      <div class="pager">
        <el-pagination v-model:current-page="page" :page-size="20" :total="total" layout="total, prev, pager, next" @current-change="load" />
      </div>
    </div>

    <el-dialog v-model="dialogVisible" title="新建生产订单" width="640px">
      <el-form :model="form" label-width="90px">
        <el-form-item label="成品" required>
          <el-select v-model="form.product_id" filterable placeholder="选择要生产的成品" style="width: 100%" @change="onProductChange">
            <el-option v-for="p in products" :key="p.id" :label="`${p.name}（库存 ${p.stock_qty}）`" :value="p.id" />
          </el-select>
        </el-form-item>
        <el-row :gutter="14">
          <el-col :span="12">
            <el-form-item label="生产数量" required>
              <el-input-number v-model="form.quantity" :min="1" style="width: 100%" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="入库仓库" required>
              <el-select v-model="form.warehouse_id" style="width: 100%">
                <el-option v-for="w in warehouses" :key="w.id" :label="w.name" :value="w.id" />
              </el-select>
            </el-form-item>
          </el-col>
        </el-row>
        <el-row :gutter="14">
          <el-col :span="12">
            <el-form-item label="计划开工">
              <el-date-picker v-model="form.plan_start" type="datetime" value-format="YYYY-MM-DDTHH:mm:ss" style="width: 100%" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="计划完工">
              <el-date-picker v-model="form.plan_end" type="datetime" value-format="YYYY-MM-DDTHH:mm:ss" style="width: 100%" />
            </el-form-item>
          </el-col>
        </el-row>
        <el-form-item v-if="bomPreview" label="BOM 用料">
          <div class="bom-preview">
            <span v-for="item in bomPreview.items" :key="item.id" class="bom-chip">
              {{ item.product_name }} × {{ (item.quantity * form.quantity / bomPreview.base_qty).toFixed(2) }}
            </span>
          </div>
        </el-form-item>
        <el-alert v-else-if="form.product_id" type="warning" :closable="false" show-icon
          title="该成品尚未配置启用的 BOM，下达时会失败，请先到 BOM 清单配置用料" />
        <el-form-item label="备注">
          <el-input v-model="form.remark" type="textarea" :rows="2" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" @click="submit">保存为草稿</el-button>
      </template>
    </el-dialog>

    <el-drawer v-model="detailVisible" title="生产订单详情" size="640px">
      <div v-if="current">
        <div class="detail-head">
          <div>
            <h3>{{ current.order_no }} · {{ current.product_name }} × {{ current.quantity }}</h3>
            <p>{{ current.bom_no || '未关联 BOM' }} · 入库仓 {{ current.warehouse_name }}</p>
          </div>
          <el-tag :type="statusMap[current.status]?.type">{{ statusMap[current.status]?.text }}</el-tag>
        </div>
        <div class="cost-line">
          <span>材料成本 <b>{{ money(current.material_cost) }}</b></span>
          <span>生产费用 <b>{{ money(current.expense_cost) }}</b></span>
          <span>合计 <b class="total">{{ money(current.total_cost) }}</b></span>
        </div>

        <el-tabs v-model="tab">
          <el-tab-pane label="用料与领料" name="materials">
            <el-table :data="current.materials" size="small">
              <el-table-column prop="product_name" label="物料" min-width="140" show-overflow-tooltip />
              <el-table-column prop="required_qty" label="需求" width="80" align="right" />
              <el-table-column prop="issued_qty" label="已领" width="80" align="right" />
              <el-table-column prop="remaining_qty" label="待领" width="80" align="right" />
              <el-table-column prop="stock_qty" label="库存" width="80" align="right" />
            </el-table>
            <div class="drawer-actions" v-if="['released', 'in_progress'].includes(current.status)">
              <el-button v-perm="'production:manage'" type="warning" size="small" @click="issue(current)">整单领料</el-button>
            </div>
          </el-tab-pane>

          <el-tab-pane label="工序流程" name="processes">
            <el-steps direction="vertical" :active="current.process_done" finish-status="success" space="70px">
              <el-step v-for="step in current.processes" :key="step.id" :title="step.name">
                <template #description>
                  <span v-if="step.status === 'completed'">{{ step.operator || '—' }} · {{ shortTime(step.scanned_at) }}</span>
                  <span v-else>
                    <el-button v-if="['released', 'in_progress'].includes(current.status)" v-perm="'production:manage'" size="small" link type="primary" @click="scan(step)">扫码报工</el-button>
                    <span v-if="!['released', 'in_progress'].includes(current.status)" class="sub-text">未完成</span>
                  </span>
                </template>
              </el-step>
            </el-steps>
          </el-tab-pane>

          <el-tab-pane label="生产费用" name="expenses">
            <el-table :data="current.expenses" size="small">
              <el-table-column prop="category" label="类别" width="100" />
              <el-table-column label="金额" width="110" align="right">
                <template #default="{ row }">{{ money(row.amount) }}</template>
              </el-table-column>
              <el-table-column prop="remark" label="备注" min-width="120" show-overflow-tooltip />
              <el-table-column prop="created_by" label="登记人" width="90" />
              <el-table-column v-if="!['completed', 'cancelled'].includes(current.status)" label="操作" width="70">
                <template #default="{ row }">
                  <el-button link type="danger" @click="removeExpense(row)">删除</el-button>
                </template>
              </el-table-column>
            </el-table>
            <div v-if="!['completed', 'cancelled'].includes(current.status)" class="expense-form">
              <el-select v-model="expenseForm.category" style="width: 120px">
                <el-option label="人工费用" value="人工费用" />
                <el-option label="制造费用" value="制造费用" />
                <el-option label="能源费用" value="能源费用" />
                <el-option label="其他" value="其他" />
              </el-select>
              <el-input-number v-model="expenseForm.amount" :min="0" :precision="2" style="width: 130px" />
              <el-input v-model="expenseForm.remark" placeholder="备注" style="width: 150px" />
              <el-button type="primary" size="small" @click="addExpense">登记费用</el-button>
            </div>
          </el-tab-pane>
        </el-tabs>
      </div>
    </el-drawer>
  </div>
</template>

<style scoped>
.stat-row { display: flex; gap: 24px; color: var(--text-secondary); font-size: 13px; margin-bottom: 12px; }
.stat-row b { color: var(--text-primary); }
.sub-text { font-size: 12px; color: var(--text-secondary); }
.detail-head { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 14px; }
.detail-head h3 { margin: 0 0 6px; font-size: 16px; }
.detail-head p { margin: 0; color: var(--text-secondary); font-size: 13px; }
.cost-line { display: flex; gap: 22px; padding: 10px 14px; background: var(--fill-2, #f6f8fa); border-radius: 8px; margin-bottom: 14px; font-size: 13px; color: var(--text-secondary); }
.cost-line b { color: var(--text-primary); }
.cost-line .total { color: #ef4444; font-size: 16px; }
.drawer-actions { margin-top: 12px; }
.expense-form { display: flex; gap: 8px; margin-top: 14px; }
.bom-preview { display: flex; flex-wrap: wrap; gap: 6px; }
.bom-chip { padding: 2px 8px; background: var(--fill-2, #f2f4f7); border-radius: 4px; font-size: 12px; }
</style>
