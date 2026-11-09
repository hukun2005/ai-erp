<script setup>
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { productApi, requisitionApi, supplierApi } from '../api'
import { useQuickCreate } from '../utils/quickCreate'
import { useUserStore } from '../stores/user'

const userStore = useUserStore()
const loading = ref(false)
const list = ref([])
const total = ref(0)
const page = ref(1)
const query = reactive({ keyword: '', status: '' })
const products = ref([])
const suppliers = ref([])

const dialogVisible = ref(false)
const detailVisible = ref(false)
const convertVisible = ref(false)
const current = ref(null)
const editingId = ref(null)
const supplierId = ref(null)
const form = reactive({
  needed_date: null,
  remark: '',
  items: [{ product_id: null, quantity: 1, est_price: 0, remark: '' }],
})

const statusMap = {
  draft: { text: '草稿', type: 'info' },
  pending: { text: '待审批', type: 'warning' },
  approved: { text: '已批准', type: 'success' },
  rejected: { text: '已驳回', type: 'danger' },
  converted: { text: '已转采购单', type: 'primary' },
  cancelled: { text: '已取消', type: 'info' },
}

async function load() {
  loading.value = true
  try {
    const data = await requisitionApi.list({ ...query, page: page.value, page_size: 20 })
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
    needed_date: null,
    remark: '',
    items: [{ product_id: null, quantity: 1, est_price: 0, remark: '' }],
  }
}

// 顶栏「新建」入口带 ?new=1 时直接打开申请弹窗
useQuickCreate(openCreate)

function openCreate() {
  editingId.value = null
  Object.assign(form, blank())
  dialogVisible.value = true
}

function openEdit(row) {
  editingId.value = row.id
  Object.assign(form, {
    needed_date: row.needed_date,
    remark: row.remark,
    items: row.items.map((i) => ({
      product_id: i.product_id,
      quantity: i.quantity,
      est_price: i.est_price,
      remark: i.remark || '',
    })),
  })
  dialogVisible.value = true
}

function onProductChange(item) {
  const product = products.value.find((p) => p.id === item.product_id)
  if (product) item.est_price = product.cost_price
}

function addItem() {
  form.items.push({ product_id: null, quantity: 1, est_price: 0, remark: '' })
}

function removeItem(index) {
  if (form.items.length === 1) return
  form.items.splice(index, 1)
}

async function submit() {
  if (form.items.some((i) => !i.product_id || i.quantity <= 0)) {
    return ElMessage.warning('请完整填写申请明细')
  }
  const payload = { ...form, items: form.items.map((i) => ({ ...i })) }
  if (editingId.value) {
    await requisitionApi.update(editingId.value, payload)
    ElMessage.success('采购申请已更新')
  } else {
    await requisitionApi.create(payload)
    ElMessage.success('采购申请已创建')
  }
  dialogVisible.value = false
  load()
}

async function viewDetail(row) {
  current.value = await requisitionApi.detail(row.id)
  detailVisible.value = true
}

async function changeStatus(row, action, label) {
  const result = await requisitionApi.changeStatus(row.id, action)
  // 多级审批：本级通过但整单未完成时仍是 pending
  if (action === 'approve' && result?.status === 'pending') {
    ElMessage.success('本级已通过，等待下一级审批')
  } else {
    ElMessage.success(`已${label}`)
  }
  load()
}

async function reject(row) {
  const { value } = await ElMessageBox.prompt('请填写驳回原因', `驳回 ${row.req_no}`, {
    inputValidator: (val) => (val && val.trim() ? true : '请填写驳回原因'),
  })
  await requisitionApi.changeStatus(row.id, 'reject', value)
  ElMessage.success('已驳回')
  load()
}

function openConvert(row) {
  current.value = row
  supplierId.value = null
  convertVisible.value = true
}

async function doConvert() {
  if (!supplierId.value) return ElMessage.warning('请选择供应商')
  const res = await requisitionApi.convert(current.value.id, supplierId.value)
  ElMessage.success(`已生成采购订单 ${res.order_no}`)
  convertVisible.value = false
  load()
}

async function remove(row) {
  await ElMessageBox.confirm(`确定删除采购申请 ${row.req_no} 吗？`, '删除确认', { type: 'warning' })
  await requisitionApi.remove(row.id)
  ElMessage.success('已删除')
  load()
}

function money(value) {
  return '¥' + Number(value || 0).toLocaleString('zh-CN', { minimumFractionDigits: 2 })
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
        <h1 class="page-title">采购申请</h1>
        <p class="page-subtitle">需求先申请、再审批、后转采购订单，杜绝口头采购与重复下单</p>
      </div>
      <el-button v-perm="'requisition:manage'" type="primary" @click="openCreate">
        <el-icon><Plus /></el-icon>&nbsp;新建采购申请
      </el-button>
    </div>

    <div class="card">
      <div class="toolbar">
        <el-input v-model="query.keyword" placeholder="搜索申请单号" clearable style="width: 240px" @keyup.enter="search" @clear="search" />
        <el-select v-model="query.status" placeholder="全部状态" clearable style="width: 140px" @change="search">
          <el-option v-for="(v, k) in statusMap" :key="k" :label="v.text" :value="k" />
        </el-select>
        <el-button @click="search">查询</el-button>
      </div>

      <el-table :data="list" v-loading="loading">
        <el-table-column prop="req_no" label="申请单号" width="170" />
        <el-table-column label="需求日期" width="120">
          <template #default="{ row }">{{ row.needed_date ? String(row.needed_date).slice(0, 10) : '—' }}</template>
        </el-table-column>
        <el-table-column prop="items.length" label="明细行数" width="90" align="center" />
        <el-table-column label="预计金额" width="130" align="right">
          <template #default="{ row }"><b class="money">{{ money(row.total_amount) }}</b></template>
        </el-table-column>
        <el-table-column label="状态" width="110" align="center">
          <template #default="{ row }">
            <el-tag :type="statusMap[row.status]?.type" size="small">{{ statusMap[row.status]?.text }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="created_by" label="申请人" width="100" />
        <el-table-column label="操作" width="360" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="viewDetail(row)">详情</el-button>
            <el-button v-if="row.status === 'draft'" v-perm="'requisition:manage'" link type="primary" @click="openEdit(row)">编辑</el-button>
            <el-button v-if="row.status === 'draft'" v-perm="'requisition:manage'" link type="warning" @click="changeStatus(row, 'submit', '提交审批')">提交</el-button>
            <el-button v-if="row.status === 'pending' && userStore.hasAnyPerm(['requisition:approve', 'purchase:approve'])" link type="success" @click="changeStatus(row, 'approve', '审批通过')">通过</el-button>
            <el-button v-if="row.status === 'pending' && userStore.hasAnyPerm(['requisition:approve', 'purchase:approve'])" link type="danger" @click="reject(row)">驳回</el-button>
            <el-button v-if="row.status === 'approved'" v-perm="'requisition:manage'" link type="success" @click="openConvert(row)">转采购单</el-button>
            <el-button v-if="!['converted', 'cancelled'].includes(row.status)" v-perm="'requisition:manage'" link type="info" @click="changeStatus(row, 'cancel', '取消')">取消</el-button>
            <el-button v-if="row.status === 'draft'" v-perm="'requisition:manage'" link type="danger" @click="remove(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>

      <div class="pager">
        <el-pagination v-model:current-page="page" :page-size="20" :total="total" layout="total, prev, pager, next" @current-change="load" />
      </div>
    </div>

    <el-dialog v-model="dialogVisible" :title="editingId ? '编辑采购申请' : '新建采购申请'" width="760px" top="6vh">
      <el-form :model="form" label-width="90px">
        <el-row :gutter="14">
          <el-col :span="12">
            <el-form-item label="需求日期">
              <el-date-picker v-model="form.needed_date" type="date" value-format="YYYY-MM-DDTHH:mm:ss" style="width: 100%" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="备注">
              <el-input v-model="form.remark" placeholder="选填" />
            </el-form-item>
          </el-col>
        </el-row>

        <el-form-item label="申请明细">
          <div class="items-box">
            <div class="item-head">
              <span class="col-product">商品</span>
              <span class="col-num">数量</span>
              <span class="col-num">预计单价</span>
              <span class="col-amount">小计</span>
              <span class="col-op"></span>
            </div>
            <div v-for="(item, index) in form.items" :key="index" class="item-row">
              <el-select v-model="item.product_id" filterable placeholder="选择商品" class="col-product" @change="onProductChange(item)">
                <el-option v-for="p in products" :key="p.id" :label="`${p.name}（库存 ${p.stock_qty}）`" :value="p.id" />
              </el-select>
              <el-input-number v-model="item.quantity" :min="1" class="col-num" />
              <el-input-number v-model="item.est_price" :min="0" :precision="2" class="col-num" />
              <span class="col-amount money">{{ money(item.quantity * item.est_price) }}</span>
              <el-button class="col-op" link type="danger" :disabled="form.items.length === 1" @click="removeItem(index)">
                <el-icon><Delete /></el-icon>
              </el-button>
            </div>
            <el-button link type="primary" @click="addItem"><el-icon><Plus /></el-icon>&nbsp;添加明细</el-button>
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" @click="submit">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="convertVisible" title="转采购订单" width="420px">
      <p class="convert-tip">将采购申请 {{ current?.req_no }} 转成采购订单草稿，请选择供应商：</p>
      <el-select v-model="supplierId" filterable placeholder="选择供应商" style="width: 100%">
        <el-option v-for="s in suppliers" :key="s.id" :label="s.name" :value="s.id" />
      </el-select>
      <template #footer>
        <el-button @click="convertVisible = false">取消</el-button>
        <el-button type="primary" @click="doConvert">生成采购订单</el-button>
      </template>
    </el-dialog>

    <el-drawer v-model="detailVisible" title="采购申请详情" size="520px">
      <div v-if="current">
        <div class="detail-head">
          <div>
            <h3>{{ current.req_no }}</h3>
            <p>申请人 {{ current.created_by }} · 需求日期 {{ current.needed_date ? String(current.needed_date).slice(0, 10) : '未设置' }}</p>
          </div>
          <el-tag :type="statusMap[current.status]?.type">{{ statusMap[current.status]?.text }}</el-tag>
        </div>
        <el-table :data="current.items" size="small">
          <el-table-column prop="product_name" label="商品" min-width="140" show-overflow-tooltip />
          <el-table-column prop="quantity" label="数量" width="70" align="center" />
          <el-table-column label="预计单价" width="95" align="right">
            <template #default="{ row }">{{ money(row.est_price) }}</template>
          </el-table-column>
          <el-table-column label="小计" width="100" align="right">
            <template #default="{ row }">{{ money(row.amount) }}</template>
          </el-table-column>
        </el-table>
        <div class="detail-total">预计合计：<b>{{ money(current.total_amount) }}</b></div>
        <p class="detail-remark">备注：{{ current.remark || '无' }}</p>
        <p v-if="current.approved_by" class="detail-remark">审批人：{{ current.approved_by }}</p>
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
.convert-tip { margin: 0 0 12px; color: var(--text-secondary); font-size: 13px; }
.detail-head { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 16px; }
.detail-head h3 { margin: 0 0 6px; font-size: 16px; }
.detail-head p { margin: 0; color: var(--text-secondary); font-size: 13px; }
.detail-total { margin-top: 16px; padding-top: 12px; border-top: 1px solid var(--border); text-align: right; }
.detail-total b { color: #ef4444; font-size: 18px; }
.detail-remark { margin-top: 12px; color: var(--text-secondary); font-size: 13px; }
</style>
