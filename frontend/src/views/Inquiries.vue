<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { inquiryApi, productApi, supplierApi } from '../api'

const loading = ref(false)
const list = ref([])
const total = ref(0)
const page = ref(1)
const query = reactive({ keyword: '', status: '' })
const products = ref([])
const suppliers = ref([])

const dialogVisible = ref(false)
const detailVisible = ref(false)
const current = ref(null)
const form = reactive({ product_id: null, quantity: 1, deadline: null, remark: '' })
const quoteForm = reactive({ supplier_id: null, unit_price: null, remark: '' })

const statusMap = {
  draft: { text: '草稿', type: 'info' },
  open: { text: '询价中', type: 'warning' },
  closed: { text: '已截止', type: 'info' },
  converted: { text: '已转采购单', type: 'success' },
}

const bestPrice = computed(() => (current.value?.quotes?.length ? current.value.quotes[0].unit_price : null))

async function load() {
  loading.value = true
  try {
    const data = await inquiryApi.list({ ...query, page: page.value, page_size: 20 })
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
  Object.assign(form, { product_id: null, quantity: 1, deadline: null, remark: '' })
  dialogVisible.value = true
}

async function submit() {
  if (!form.product_id || form.quantity <= 0) return ElMessage.warning('请选择商品并填写数量')
  await inquiryApi.create({ ...form })
  ElMessage.success('询价单已创建，可在详情中录入多家供应商报价')
  dialogVisible.value = false
  load()
}

async function viewDetail(row) {
  current.value = await inquiryApi.detail(row.id)
  quoteForm.supplier_id = null
  quoteForm.unit_price = null
  quoteForm.remark = ''
  detailVisible.value = true
}

async function addQuote() {
  if (!quoteForm.supplier_id || !quoteForm.unit_price) return ElMessage.warning('请选择供应商并填写单价')
  try {
    current.value = await inquiryApi.addQuote(current.value.id, { ...quoteForm })
    quoteForm.supplier_id = null
    quoteForm.unit_price = null
    quoteForm.remark = ''
    ElMessage.success('报价已登记')
    load()
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '登记失败')
  }
}

async function removeQuote(row) {
  await ElMessageBox.confirm(`确定删除 ${row.supplier_name} 的报价吗？`, '删除确认', { type: 'warning' })
  current.value = await inquiryApi.removeQuote(current.value.id, row.id)
  load()
}

async function convert(row) {
  await ElMessageBox.confirm(
    `按 ${row.supplier_name} 报价 ¥${row.unit_price} 转成采购订单草稿，确定继续吗？`,
    '选定报价',
    { type: 'warning' },
  )
  const res = await inquiryApi.convert(current.value.id, row.id)
  ElMessage.success(`已生成采购订单 ${res.order_no}`)
  current.value = res.inquiry
  load()
}

async function remove(row) {
  await ElMessageBox.confirm(`确定删除询价单 ${row.inquiry_no} 吗？`, '删除确认', { type: 'warning' })
  await inquiryApi.remove(row.id)
  ElMessage.success('已删除')
  load()
}

function money(value) {
  return '¥' + Number(value || 0).toLocaleString('zh-CN', { minimumFractionDigits: 2 })
}

function minPrice(row) {
  return row.quotes?.length ? Math.min(...row.quotes.map((q) => q.unit_price)) : null
}

onMounted(async () => {
  load()
  const [p, s] = await Promise.all([productApi.list({ page_size: 200 }), supplierApi.all()])
  products.value = p.items
  suppliers.value = s
})
</script>

<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h1 class="page-title">采购询价</h1>
        <p class="page-subtitle">一张询价单收集多家供应商报价，按最低价或综合条件选定后直接转采购订单</p>
      </div>
      <el-button v-perm="'purchase:manage'" type="primary" @click="openCreate">
        <el-icon><Plus /></el-icon>&nbsp;新建询价单
      </el-button>
    </div>

    <div class="card">
      <div class="toolbar">
        <el-input v-model="query.keyword" placeholder="搜索询价单号 / 商品" clearable style="width: 240px" @keyup.enter="search" @clear="search" />
        <el-select v-model="query.status" placeholder="全部状态" clearable style="width: 140px" @change="search">
          <el-option v-for="(v, k) in statusMap" :key="k" :label="v.text" :value="k" />
        </el-select>
        <el-button @click="search">查询</el-button>
      </div>

      <el-table :data="list" v-loading="loading">
        <el-table-column prop="inquiry_no" label="询价单号" width="175" />
        <el-table-column prop="product_name" label="商品" min-width="160" show-overflow-tooltip />
        <el-table-column prop="quantity" label="数量" width="80" align="right" />
        <el-table-column label="截止日期" width="120">
          <template #default="{ row }">{{ row.deadline ? String(row.deadline).slice(0, 10) : '—' }}</template>
        </el-table-column>
        <el-table-column label="供应商报价" width="120" align="center">
          <template #default="{ row }">{{ row.quotes.length }} 家</template>
        </el-table-column>
        <el-table-column label="最低报价" width="120" align="right">
          <template #default="{ row }">
            <b v-if="minPrice(row) !== null" class="money">{{ money(minPrice(row)) }}</b>
            <span v-else>—</span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="110" align="center">
          <template #default="{ row }">
            <el-tag :type="statusMap[row.status]?.type" size="small">{{ statusMap[row.status]?.text }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="190" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="viewDetail(row)">报价详情</el-button>
            <el-button v-if="row.status !== 'converted'" v-perm="'purchase:manage'" link type="danger" @click="remove(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>

      <div class="pager">
        <el-pagination v-model:current-page="page" :page-size="20" :total="total" layout="total, prev, pager, next" @current-change="load" />
      </div>
    </div>

    <el-dialog v-model="dialogVisible" title="新建询价单" width="520px">
      <el-form :model="form" label-width="90px">
        <el-form-item label="商品" required>
          <el-select v-model="form.product_id" filterable placeholder="选择询价商品" style="width: 100%">
            <el-option v-for="p in products" :key="p.id" :label="`${p.name}（库存 ${p.stock_qty}）`" :value="p.id" />
          </el-select>
        </el-form-item>
        <el-row :gutter="14">
          <el-col :span="12">
            <el-form-item label="采购数量" required>
              <el-input-number v-model="form.quantity" :min="1" style="width: 100%" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="截止日期">
              <el-date-picker v-model="form.deadline" type="date" value-format="YYYY-MM-DDTHH:mm:ss" style="width: 100%" />
            </el-form-item>
          </el-col>
        </el-row>
        <el-form-item label="备注">
          <el-input v-model="form.remark" type="textarea" :rows="2" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" @click="submit">创建</el-button>
      </template>
    </el-dialog>

    <el-drawer v-model="detailVisible" title="询价比价" size="560px">
      <div v-if="current">
        <div class="detail-head">
          <div>
            <h3>{{ current.inquiry_no }} · {{ current.product_name }}</h3>
            <p>采购数量 {{ current.quantity }} {{ current.unit }} · 经办 {{ current.created_by }}</p>
          </div>
          <el-tag :type="statusMap[current.status]?.type">{{ statusMap[current.status]?.text }}</el-tag>
        </div>

        <el-table :data="current.quotes" size="small">
          <el-table-column prop="supplier_name" label="供应商" min-width="130" show-overflow-tooltip />
          <el-table-column label="单价" width="95" align="right">
            <template #default="{ row }">
              <span :class="{ best: row.unit_price === bestPrice }">{{ money(row.unit_price) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="总价" width="110" align="right">
            <template #default="{ row }">{{ money(row.amount) }}</template>
          </el-table-column>
          <el-table-column prop="remark" label="备注" min-width="90" show-overflow-tooltip />
          <el-table-column v-if="current.status !== 'converted'" label="操作" width="110">
            <template #default="{ row }">
              <el-button link type="success" @click="convert(row)">选定</el-button>
              <el-button link type="danger" @click="removeQuote(row)">删除</el-button>
            </template>
          </el-table-column>
        </el-table>
        <p v-if="!current.quotes.length" class="empty-tip">还没有供应商报价，请在下方录入</p>

        <div v-if="current.status !== 'converted'" class="quote-form">
          <div class="form-title">录入供应商报价</div>
          <el-select v-model="quoteForm.supplier_id" filterable placeholder="供应商" style="width: 150px">
            <el-option v-for="s in suppliers" :key="s.id" :label="s.name" :value="s.id" />
          </el-select>
          <el-input-number v-model="quoteForm.unit_price" :min="0.01" :precision="2" placeholder="单价" style="width: 130px" />
          <el-input v-model="quoteForm.remark" placeholder="备注" style="width: 130px" />
          <el-button type="primary" size="small" @click="addQuote">添加报价</el-button>
        </div>
        <p class="detail-remark">备注：{{ current.remark || '无' }}</p>
      </div>
    </el-drawer>
  </div>
</template>

<style scoped>
.detail-head { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 16px; }
.detail-head h3 { margin: 0 0 6px; font-size: 16px; }
.detail-head p { margin: 0; color: var(--text-secondary); font-size: 13px; }
.best { color: #10b981; font-weight: 600; }
.empty-tip { color: var(--text-secondary); font-size: 13px; text-align: center; padding: 18px 0; }
.quote-form { margin-top: 14px; padding-top: 14px; border-top: 1px solid var(--border); display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
.form-title { width: 100%; font-size: 13px; color: var(--text-secondary); margin-bottom: 4px; }
.detail-remark { margin-top: 14px; color: var(--text-secondary); font-size: 13px; }
</style>
