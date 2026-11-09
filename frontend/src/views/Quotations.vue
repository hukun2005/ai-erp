<script setup>
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { customerApi, productApi, quoteApi } from '../api'

const loading = ref(false)
const list = ref([])
const total = ref(0)
const page = ref(1)
const query = reactive({ keyword: '', status: '' })
const customers = ref([])
const products = ref([])

const dialogVisible = ref(false)
const detailVisible = ref(false)
const current = ref(null)
const editingId = ref(null)
const form = reactive({
  customer_id: null,
  valid_until: null,
  discount_amount: 0,
  remark: '',
  items: [{ product_id: null, quantity: 1, unit_price: 0 }],
})

const statusMap = {
  draft: { text: '草稿', type: 'info' },
  sent: { text: '已发送', type: 'warning' },
  accepted: { text: '客户确认', type: 'success' },
  rejected: { text: '已失效', type: 'danger' },
  converted: { text: '已转订单', type: 'primary' },
}

async function load() {
  loading.value = true
  try {
    const data = await quoteApi.list({ ...query, page: page.value, page_size: 20 })
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

function blank() {
  return {
    customer_id: null,
    valid_until: null,
    discount_amount: 0,
    remark: '',
    items: [{ product_id: null, quantity: 1, unit_price: 0 }],
  }
}

function openCreate() {
  editingId.value = null
  Object.assign(form, blank())
  dialogVisible.value = true
}

function openEdit(row) {
  editingId.value = row.id
  Object.assign(form, {
    customer_id: row.customer_id,
    valid_until: row.valid_until,
    discount_amount: row.discount_amount,
    remark: row.remark,
    items: row.items.map((i) => ({ product_id: i.product_id, quantity: i.quantity, unit_price: i.unit_price })),
  })
  dialogVisible.value = true
}

function onProductChange(item) {
  const product = products.value.find((p) => p.id === item.product_id)
  if (product) item.unit_price = product.sale_price
}

function addItem() {
  form.items.push({ product_id: null, quantity: 1, unit_price: 0 })
}

function removeItem(index) {
  if (form.items.length === 1) return
  form.items.splice(index, 1)
}

async function submit() {
  if (!form.customer_id) return ElMessage.warning('请选择客户')
  if (form.items.some((i) => !i.product_id || i.quantity <= 0)) {
    return ElMessage.warning('请完整填写报价明细')
  }
  const payload = { ...form, items: form.items.map((i) => ({ ...i })) }
  if (editingId.value) {
    await quoteApi.update(editingId.value, payload)
    ElMessage.success('报价单已更新')
  } else {
    await quoteApi.create(payload)
    ElMessage.success('报价单已创建')
  }
  dialogVisible.value = false
  load()
}

async function viewDetail(row) {
  current.value = await quoteApi.detail(row.id)
  detailVisible.value = true
}

async function setStatus(row, status, label) {
  await quoteApi.changeStatus(row.id, status)
  ElMessage.success(`已${label}`)
  load()
}

async function convert(row) {
  await ElMessageBox.confirm(`将报价单 ${row.quote_no} 转成销售订单草稿，确定继续吗？`, '转销售订单', { type: 'warning' })
  const res = await quoteApi.convert(row.id)
  ElMessage.success(`已生成销售订单 ${res.order_no}`)
  load()
}

async function remove(row) {
  await ElMessageBox.confirm(`确定删除报价单 ${row.quote_no} 吗？`, '删除确认', { type: 'warning' })
  await quoteApi.remove(row.id)
  ElMessage.success('已删除')
  load()
}

function money(value) {
  return '¥' + Number(value || 0).toLocaleString('zh-CN', { minimumFractionDigits: 2 })
}

onMounted(async () => {
  load()
  const [c, p] = await Promise.all([customerApi.all(), productApi.list({ page_size: 200 })])
  customers.value = c
  products.value = p.items
})
</script>

<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h1 class="page-title">销售报价</h1>
        <p class="page-subtitle">先报价、再转销售订单，避免口头承诺；报价自带有效期与折扣</p>
      </div>
      <el-button v-perm="'quote:manage'" type="primary" @click="openCreate">
        <el-icon><Plus /></el-icon>&nbsp;新建报价单
      </el-button>
    </div>

    <div class="card">
      <div class="toolbar">
        <el-input v-model="query.keyword" placeholder="搜索报价单号 / 客户" clearable style="width: 240px" @keyup.enter="search" @clear="search" />
        <el-select v-model="query.status" placeholder="全部状态" clearable style="width: 140px" @change="search">
          <el-option v-for="(v, k) in statusMap" :key="k" :label="v.text" :value="k" />
        </el-select>
        <el-button @click="search">查询</el-button>
      </div>

      <el-table :data="list" v-loading="loading">
        <el-table-column prop="quote_no" label="报价单号" width="170" />
        <el-table-column prop="customer_name" label="客户" min-width="160" show-overflow-tooltip />
        <el-table-column label="有效期至" width="120">
          <template #default="{ row }">{{ row.valid_until ? String(row.valid_until).slice(0, 10) : '—' }}</template>
        </el-table-column>
        <el-table-column label="报价金额" width="130" align="right">
          <template #default="{ row }"><b class="money">{{ money(row.total_amount) }}</b></template>
        </el-table-column>
        <el-table-column label="状态" width="100" align="center">
          <template #default="{ row }">
            <el-tag :type="statusMap[row.status]?.type" size="small">{{ statusMap[row.status]?.text }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="created_by" label="报价人" width="100" />
        <el-table-column label="操作" width="330" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="viewDetail(row)">详情</el-button>
            <el-button v-if="row.status === 'draft'" v-perm="'quote:manage'" link type="primary" @click="openEdit(row)">编辑</el-button>
            <el-button v-if="row.status === 'draft'" v-perm="'quote:manage'" link type="warning" @click="setStatus(row, 'sent', '发送')">发送</el-button>
            <el-button v-if="row.status === 'sent'" v-perm="'quote:manage'" link type="success" @click="setStatus(row, 'accepted', '标记客户确认')">客户确认</el-button>
            <el-button v-if="['draft', 'sent'].includes(row.status)" v-perm="'quote:manage'" link type="info" @click="setStatus(row, 'rejected', '失效')">失效</el-button>
            <el-button v-if="['sent', 'accepted'].includes(row.status)" v-perm="'quote:manage'" link type="success" @click="convert(row)">转订单</el-button>
            <el-button v-if="row.status === 'draft'" v-perm="'quote:manage'" link type="danger" @click="remove(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>

      <div class="pager">
        <el-pagination v-model:current-page="page" :page-size="20" :total="total" layout="total, prev, pager, next" @current-change="load" />
      </div>
    </div>

    <el-dialog v-model="dialogVisible" :title="editingId ? '编辑报价单' : '新建报价单'" width="760px" top="6vh">
      <el-form :model="form" label-width="80px">
        <el-row :gutter="14">
          <el-col :span="12">
            <el-form-item label="客户" required>
              <el-select v-model="form.customer_id" filterable placeholder="选择客户" style="width: 100%">
                <el-option v-for="c in customers" :key="c.id" :label="c.name" :value="c.id" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="有效期至">
              <el-date-picker v-model="form.valid_until" type="date" value-format="YYYY-MM-DDTHH:mm:ss" style="width: 100%" />
            </el-form-item>
          </el-col>
        </el-row>

        <el-form-item label="报价明细">
          <div class="items-box">
            <div class="item-head">
              <span class="col-product">商品</span>
              <span class="col-num">数量</span>
              <span class="col-num">报价</span>
              <span class="col-amount">小计</span>
              <span class="col-op"></span>
            </div>
            <div v-for="(item, index) in form.items" :key="index" class="item-row">
              <el-select v-model="item.product_id" filterable placeholder="选择商品" class="col-product" @change="onProductChange(item)">
                <el-option v-for="p in products" :key="p.id" :label="p.name" :value="p.id" />
              </el-select>
              <el-input-number v-model="item.quantity" :min="1" class="col-num" />
              <el-input-number v-model="item.unit_price" :min="0" :precision="2" class="col-num" />
              <span class="col-amount money">{{ money(item.quantity * item.unit_price) }}</span>
              <el-button class="col-op" link type="danger" :disabled="form.items.length === 1" @click="removeItem(index)">
                <el-icon><Delete /></el-icon>
              </el-button>
            </div>
            <el-button link type="primary" @click="addItem"><el-icon><Plus /></el-icon>&nbsp;添加明细</el-button>
          </div>
        </el-form-item>

        <el-form-item label="优惠金额">
          <el-input-number v-model="form.discount_amount" :min="0" :precision="2" style="width: 200px" />
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="form.remark" type="textarea" :rows="2" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" @click="submit">保存</el-button>
      </template>
    </el-dialog>

    <el-drawer v-model="detailVisible" title="报价单详情" size="520px">
      <div v-if="current">
        <div class="detail-head">
          <div>
            <h3>{{ current.quote_no }}</h3>
            <p>{{ current.customer_name }} · 报价人 {{ current.created_by }}</p>
          </div>
          <el-tag :type="statusMap[current.status]?.type">{{ statusMap[current.status]?.text }}</el-tag>
        </div>
        <el-table :data="current.items" size="small">
          <el-table-column prop="product_name" label="商品" min-width="140" show-overflow-tooltip />
          <el-table-column prop="quantity" label="数量" width="70" align="center" />
          <el-table-column label="报价" width="90" align="right">
            <template #default="{ row }">{{ money(row.unit_price) }}</template>
          </el-table-column>
          <el-table-column label="小计" width="100" align="right">
            <template #default="{ row }">{{ money(row.amount) }}</template>
          </el-table-column>
        </el-table>
        <div class="detail-total">
          <span>优惠：{{ money(current.discount_amount) }}</span>
          <span>合计：<b>{{ money(current.total_amount) }}</b></span>
        </div>
        <p class="detail-remark">有效期至：{{ current.valid_until ? String(current.valid_until).slice(0, 10) : '未设置' }}</p>
        <p class="detail-remark">备注：{{ current.remark || '无' }}</p>
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
.col-num { width: 110px; }
.col-amount { width: 110px; text-align: right; }
.col-op { width: 32px; }
.detail-head { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 16px; }
.detail-head h3 { margin: 0 0 6px; font-size: 16px; }
.detail-head p { margin: 0; color: var(--text-secondary); font-size: 13px; }
.detail-total { display: flex; justify-content: space-between; margin-top: 16px; padding-top: 12px; border-top: 1px solid var(--border); }
.detail-total b { color: #ef4444; font-size: 18px; }
.detail-remark { margin-top: 12px; color: var(--text-secondary); font-size: 13px; }
</style>
