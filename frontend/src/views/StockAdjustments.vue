<script setup>
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { adjustmentApi, productApi, warehouseApi } from '../api'

const loading = ref(false)
const list = ref([])
const total = ref(0)
const page = ref(1)
const query = reactive({ keyword: '', adjust_type: '', status: '' })
const products = ref([])
const warehouses = ref([])

const dialogVisible = ref(false)
const detailVisible = ref(false)
const current = ref(null)
const form = reactive({
  adjust_type: 'gain',
  warehouse_id: null,
  reason: '',
  items: [{ product_id: null, quantity: 1, unit_cost: 0 }],
})

const typeMap = {
  gain: { text: '盘盈入库', type: 'success' },
  loss: { text: '盘亏出库', type: 'danger' },
  cost: { text: '成本调整', type: 'warning' },
}
const statusMap = {
  draft: { text: '草稿', type: 'info' },
  posted: { text: '已过账', type: 'success' },
  cancelled: { text: '已取消', type: 'info' },
}

async function load() {
  loading.value = true
  try {
    const data = await adjustmentApi.list({ ...query, page: page.value, page_size: 20 })
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

function openCreate() {
  Object.assign(form, {
    adjust_type: 'gain',
    warehouse_id: warehouses.value.find((w) => w.is_default)?.id || warehouses.value[0]?.id || null,
    reason: '',
    items: [{ product_id: null, quantity: 1, unit_cost: 0 }],
  })
  dialogVisible.value = true
}

function onProductChange(item) {
  const product = products.value.find((p) => p.id === item.product_id)
  if (product) item.unit_cost = product.cost_price
}

function onTypeChange() {
  form.items.forEach((item) => {
    const product = products.value.find((p) => p.id === item.product_id)
    if (product) item.unit_cost = product.cost_price
  })
}

function addItem() {
  form.items.push({ product_id: null, quantity: 1, unit_cost: 0 })
}

function removeItem(index) {
  if (form.items.length === 1) return
  form.items.splice(index, 1)
}

function totalAmount() {
  return form.items.reduce((sum, item) => {
    const product = products.value.find((p) => p.id === item.product_id)
    if (!product) return sum
    if (form.adjust_type === 'cost') return sum + (item.unit_cost - product.cost_price) * product.stock_qty
    return sum + item.quantity * product.cost_price
  }, 0)
}

async function submit() {
  if (!form.warehouse_id) return ElMessage.warning('请选择仓库')
  if (form.items.some((i) => !i.product_id)) return ElMessage.warning('请完整填写调整明细')
  if (form.adjust_type === 'cost' && form.items.some((i) => i.unit_cost <= 0)) {
    return ElMessage.warning('成本调整需填写调整后的单位成本')
  }
  if (form.adjust_type !== 'cost' && form.items.some((i) => i.quantity <= 0)) {
    return ElMessage.warning('盘盈/盘亏数量必须大于 0')
  }
  await adjustmentApi.create({
    adjust_type: form.adjust_type,
    warehouse_id: form.warehouse_id,
    reason: form.reason,
    items: form.items.map((i) => ({
      product_id: i.product_id,
      quantity: form.adjust_type === 'cost' ? 0 : i.quantity,
      unit_cost: i.unit_cost,
    })),
  })
  ElMessage.success('调整单已创建（草稿），过账后才影响库存/成本')
  dialogVisible.value = false
  load()
}

async function viewDetail(row) {
  current.value = await adjustmentApi.detail(row.id)
  detailVisible.value = true
}

async function post(row) {
  await ElMessageBox.confirm(
    row.adjust_type === 'cost'
      ? '过账后将直接改写商品单位成本，确定继续吗？'
      : '过账后将立即调整库存数量，确定继续吗？',
    '过账确认',
    { type: 'warning' },
  )
  try {
    await adjustmentApi.changeStatus(row.id, 'post')
    ElMessage.success('已过账')
    load()
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '过账失败')
  }
}

async function cancel(row) {
  await ElMessageBox.confirm(`确定取消调整单 ${row.adjust_no} 吗？`, '取消确认', { type: 'warning' })
  await adjustmentApi.changeStatus(row.id, 'cancel')
  ElMessage.success('已取消')
  load()
}

async function remove(row) {
  await ElMessageBox.confirm(`确定删除草稿调整单 ${row.adjust_no} 吗？`, '删除确认', { type: 'warning' })
  await adjustmentApi.remove(row.id)
  ElMessage.success('已删除')
  load()
}

function money(value) {
  return '¥' + Number(value || 0).toLocaleString('zh-CN', { minimumFractionDigits: 2 })
}

onMounted(async () => {
  load()
  const [p, w] = await Promise.all([productApi.list({ page_size: 200 }), warehouseApi.options()])
  products.value = p.items
  warehouses.value = w
})
</script>

<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h1 class="page-title">库存调整</h1>
        <p class="page-subtitle">盘盈入库、盘亏出库与成本调整统一走单据，过账后自动影响库存与商品成本</p>
      </div>
      <el-button v-perm="'inventory:adjust'" type="primary" @click="openCreate">
        <el-icon><Plus /></el-icon>&nbsp;新建调整单
      </el-button>
    </div>

    <div class="card">
      <div class="toolbar">
        <el-input v-model="query.keyword" placeholder="搜索调整单号 / 原因" clearable style="width: 240px" @keyup.enter="search" @clear="search" />
        <el-select v-model="query.adjust_type" placeholder="全部类型" clearable style="width: 140px" @change="search">
          <el-option v-for="(v, k) in typeMap" :key="k" :label="v.text" :value="k" />
        </el-select>
        <el-select v-model="query.status" placeholder="全部状态" clearable style="width: 130px" @change="search">
          <el-option v-for="(v, k) in statusMap" :key="k" :label="v.text" :value="k" />
        </el-select>
        <el-button @click="search">查询</el-button>
      </div>

      <el-table :data="list" v-loading="loading">
        <el-table-column prop="adjust_no" label="调整单号" width="175" />
        <el-table-column label="类型" width="110" align="center">
          <template #default="{ row }">
            <el-tag :type="typeMap[row.adjust_type]?.type" size="small">{{ typeMap[row.adjust_type]?.text }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="warehouse_name" label="仓库" width="150" show-overflow-tooltip />
        <el-table-column prop="items.length" label="明细行" width="80" align="center" />
        <el-table-column label="金额影响" width="130" align="right">
          <template #default="{ row }">{{ money(row.total_amount) }}</template>
        </el-table-column>
        <el-table-column prop="reason" label="调整原因" min-width="150" show-overflow-tooltip />
        <el-table-column label="状态" width="90" align="center">
          <template #default="{ row }">
            <el-tag :type="statusMap[row.status]?.type" size="small">{{ statusMap[row.status]?.text }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="created_by" label="创建人" width="100" />
        <el-table-column label="操作" width="230" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="viewDetail(row)">详情</el-button>
            <el-button v-if="row.status === 'draft'" v-perm="'inventory:adjust'" link type="success" @click="post(row)">过账</el-button>
            <el-button v-if="row.status === 'draft'" v-perm="'inventory:adjust'" link type="info" @click="cancel(row)">取消</el-button>
            <el-button v-if="row.status === 'draft'" v-perm="'inventory:adjust'" link type="danger" @click="remove(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>

      <div class="pager">
        <el-pagination v-model:current-page="page" :page-size="20" :total="total" layout="total, prev, pager, next" @current-change="load" />
      </div>
    </div>

    <el-dialog v-model="dialogVisible" title="新建库存调整单" width="720px" top="7vh">
      <el-form :model="form" label-width="90px">
        <el-row :gutter="14">
          <el-col :span="8">
            <el-form-item label="调整类型" required>
              <el-select v-model="form.adjust_type" style="width: 100%" @change="onTypeChange">
                <el-option v-for="(v, k) in typeMap" :key="k" :label="v.text" :value="k" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="仓库" required>
              <el-select v-model="form.warehouse_id" style="width: 100%">
                <el-option v-for="w in warehouses" :key="w.id" :label="w.name" :value="w.id" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="调整原因">
              <el-input v-model="form.reason" placeholder="如：盘点差异" />
            </el-form-item>
          </el-col>
        </el-row>

        <el-form-item label="调整明细">
          <div class="items-box">
            <div class="item-head">
              <span class="col-product">商品</span>
              <span class="col-num">{{ form.adjust_type === 'cost' ? '调整后成本' : '数量' }}</span>
              <span class="col-stock">当前库存</span>
              <span class="col-op"></span>
            </div>
            <div v-for="(item, index) in form.items" :key="index" class="item-row">
              <el-select v-model="item.product_id" filterable placeholder="选择商品" class="col-product" @change="onProductChange(item)">
                <el-option v-for="p in products" :key="p.id" :label="`${p.name}（库存 ${p.stock_qty}）`" :value="p.id" />
              </el-select>
              <el-input-number
                v-if="form.adjust_type === 'cost'"
                v-model="item.unit_cost"
                :min="0.01"
                :precision="2"
                class="col-num"
              />
              <el-input-number v-else v-model="item.quantity" :min="1" class="col-num" />
              <span class="col-stock">{{ products.find((p) => p.id === item.product_id)?.stock_qty ?? '—' }}</span>
              <el-button class="col-op" link type="danger" :disabled="form.items.length === 1" @click="removeItem(index)">
                <el-icon><Delete /></el-icon>
              </el-button>
            </div>
            <el-button link type="primary" @click="addItem"><el-icon><Plus /></el-icon>&nbsp;添加明细</el-button>
          </div>
        </el-form-item>

        <div class="total-line">金额影响：<b>{{ money(totalAmount()) }}</b></div>
        <el-alert
          v-if="form.adjust_type === 'cost'"
          type="info"
          :closable="false"
          show-icon
          title="成本调整不改变数量，只改写商品单位成本；金额影响按（新成本 - 原成本）× 当前库存计算"
          style="margin-top: 10px"
        />
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" @click="submit">保存为草稿</el-button>
      </template>
    </el-dialog>

    <el-drawer v-model="detailVisible" title="调整单详情" size="520px">
      <div v-if="current">
        <div class="detail-head">
          <div>
            <h3>{{ current.adjust_no }}</h3>
            <p>{{ current.warehouse_name }} · {{ current.created_by }}</p>
          </div>
          <el-tag :type="typeMap[current.adjust_type]?.type">{{ typeMap[current.adjust_type]?.text }}</el-tag>
        </div>
        <el-table :data="current.items" size="small">
          <el-table-column prop="product_name" label="商品" min-width="140" show-overflow-tooltip />
          <el-table-column prop="quantity" label="数量" width="70" align="right" />
          <el-table-column label="单位成本" width="100" align="right">
            <template #default="{ row }">{{ money(row.unit_cost) }}</template>
          </el-table-column>
          <el-table-column label="金额" width="110" align="right">
            <template #default="{ row }">{{ money(row.amount) }}</template>
          </el-table-column>
        </el-table>
        <div class="detail-total">金额影响：<b>{{ money(current.total_amount) }}</b></div>
        <p class="detail-remark">调整原因：{{ current.reason || '无' }}</p>
        <p class="detail-remark">状态：{{ statusMap[current.status]?.text }}<span v-if="current.posted_at"> · 过账 {{ String(current.posted_at).replace('T', ' ').slice(0, 16) }}</span></p>
      </div>
    </el-drawer>
  </div>
</template>

<style scoped>
.items-box { width: 100%; }
.item-head, .item-row { display: flex; gap: 8px; align-items: center; }
.item-head span { font-size: 12px; color: var(--text-secondary); }
.item-row { margin-bottom: 8px; }
.col-product { flex: 1; }
.col-num { width: 130px; }
.col-stock { width: 90px; text-align: right; color: var(--text-secondary); }
.col-op { width: 32px; }
.total-line { text-align: right; color: var(--text-secondary); font-size: 14px; }
.total-line b { color: #ef4444; font-size: 18px; }
.detail-head { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 16px; }
.detail-head h3 { margin: 0 0 6px; font-size: 16px; }
.detail-head p { margin: 0; color: var(--text-secondary); font-size: 13px; }
.detail-total { margin-top: 16px; padding-top: 12px; border-top: 1px solid var(--border); text-align: right; }
.detail-total b { color: #ef4444; font-size: 18px; }
.detail-remark { margin-top: 12px; color: var(--text-secondary); font-size: 13px; }
</style>
