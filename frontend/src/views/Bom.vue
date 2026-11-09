<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { bomApi, productApi } from '../api'

const loading = ref(false)
const list = ref([])
const total = ref(0)
const page = ref(1)
const query = reactive({ keyword: '', status: '' })
const finishedProducts = ref([])
const rawMaterials = ref([])

const dialogVisible = ref(false)
const detailVisible = ref(false)
const current = ref(null)
const editingId = ref(null)
const dialogTitle = computed(() => (editingId.value ? '编辑 BOM' : '新建 BOM'))

const blank = () => ({
  product_id: null,
  name: '',
  version: 'V1.0',
  base_qty: 1,
  status: 'active',
  remark: '',
  items: [{ product_id: null, quantity: 1, remark: '' }],
})
const form = reactive(blank())

const statusMap = {
  active: { text: '启用', type: 'success' },
  inactive: { text: '停用', type: 'info' },
}

async function load() {
  loading.value = true
  try {
    const data = await bomApi.list({ ...query, page: page.value, page_size: 20 })
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
  editingId.value = null
  Object.assign(form, blank())
  dialogVisible.value = true
}

function openEdit(row) {
  editingId.value = row.id
  Object.assign(form, {
    product_id: row.product_id,
    name: row.name,
    version: row.version,
    base_qty: row.base_qty,
    status: row.status,
    remark: row.remark,
    items: row.items.map((item) => ({
      product_id: item.product_id,
      quantity: item.quantity,
      remark: item.remark || '',
    })),
  })
  dialogVisible.value = true
}

async function viewDetail(row) {
  current.value = await bomApi.detail(row.id)
  detailVisible.value = true
}

function onProductChange() {
  const product = finishedProducts.value.find((p) => p.id === form.product_id)
  if (product && !form.name) form.name = `${product.name} BOM`
}

function addItem() {
  form.items.push({ product_id: null, quantity: 1, remark: '' })
}

function removeItem(index) {
  if (form.items.length === 1) return
  form.items.splice(index, 1)
}

async function submit() {
  if (!form.product_id) return ElMessage.warning('请选择成品')
  if (form.items.some((i) => !i.product_id || i.quantity <= 0)) {
    return ElMessage.warning('请完整填写用料明细')
  }
  const payload = {
    product_id: form.product_id,
    name: form.name,
    version: form.version,
    base_qty: form.base_qty,
    status: form.status,
    remark: form.remark,
    items: form.items.map((i) => ({
      product_id: i.product_id,
      quantity: i.quantity,
      remark: i.remark,
    })),
  }
  if (editingId.value) {
    await bomApi.update(editingId.value, payload)
    ElMessage.success('BOM 已更新')
  } else {
    await bomApi.create(payload)
    ElMessage.success('BOM 已创建')
  }
  dialogVisible.value = false
  load()
}

async function remove(row) {
  await ElMessageBox.confirm(`确定删除 BOM ${row.bom_no} 吗？`, '删除确认', { type: 'warning' })
  await bomApi.remove(row.id)
  ElMessage.success('已删除')
  load()
}

function money(value) {
  return '¥' + Number(value || 0).toLocaleString('zh-CN', { minimumFractionDigits: 2 })
}

onMounted(async () => {
  load()
  const [finished, raw] = await Promise.all([
    productApi.list({ page_size: 200, product_type: 'finished_good' }),
    productApi.list({ page_size: 200, product_type: 'raw_material' }),
  ])
  finishedProducts.value = finished.items
  rawMaterials.value = raw.items
})
</script>

<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h1 class="page-title">BOM 清单</h1>
        <p class="page-subtitle">配置成品的标准用料，生产订单下达时按 BOM 自动展开领料需求</p>
      </div>
      <el-button v-perm="'bom:manage'" type="primary" @click="openCreate">
        <el-icon><Plus /></el-icon>&nbsp;新建 BOM
      </el-button>
    </div>

    <div class="card">
      <div class="toolbar">
        <el-input v-model="query.keyword" placeholder="搜索 BOM 编号 / 成品" clearable style="width: 240px" @keyup.enter="search" @clear="search" />
        <el-select v-model="query.status" placeholder="全部状态" clearable style="width: 140px" @change="search">
          <el-option label="启用" value="active" />
          <el-option label="停用" value="inactive" />
        </el-select>
        <el-button @click="search">查询</el-button>
      </div>

      <el-table :data="list" v-loading="loading">
        <el-table-column prop="bom_no" label="BOM 编号" width="170" />
        <el-table-column prop="product_name" label="成品" min-width="160" show-overflow-tooltip />
        <el-table-column prop="version" label="版本" width="80" align="center" />
        <el-table-column label="标准产出" width="100" align="right">
          <template #default="{ row }">{{ row.base_qty }} {{ row.unit }}</template>
        </el-table-column>
        <el-table-column prop="item_count" label="用料行数" width="90" align="center" />
        <el-table-column label="状态" width="90" align="center">
          <template #default="{ row }">
            <el-tag :type="statusMap[row.status]?.type" size="small">{{ statusMap[row.status]?.text }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="created_by" label="创建人" width="100" />
        <el-table-column label="操作" width="220" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="viewDetail(row)">详情</el-button>
            <el-button v-perm="'bom:manage'" link type="primary" @click="openEdit(row)">编辑</el-button>
            <el-button v-perm="'bom:manage'" link type="danger" @click="remove(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>

      <div class="pager">
        <el-pagination v-model:current-page="page" :page-size="20" :total="total" layout="total, prev, pager, next" @current-change="load" />
      </div>
    </div>

    <el-dialog v-model="dialogVisible" :title="dialogTitle" width="760px" top="6vh">
      <el-form :model="form" label-width="90px">
        <el-row :gutter="14">
          <el-col :span="12">
            <el-form-item label="成品" required>
              <el-select v-model="form.product_id" filterable placeholder="选择成品" style="width: 100%" @change="onProductChange">
                <el-option v-for="p in finishedProducts" :key="p.id" :label="p.name" :value="p.id" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="标准产出">
              <el-input-number v-model="form.base_qty" :min="0.01" :precision="2" style="width: 100%" />
            </el-form-item>
          </el-col>
        </el-row>
        <el-row :gutter="14">
          <el-col :span="12">
            <el-form-item label="名称">
              <el-input v-model="form.name" placeholder="选填，默认按成品命名" />
            </el-form-item>
          </el-col>
          <el-col :span="6">
            <el-form-item label="版本">
              <el-input v-model="form.version" />
            </el-form-item>
          </el-col>
          <el-col :span="6">
            <el-form-item label="状态">
              <el-select v-model="form.status" style="width: 100%">
                <el-option label="启用" value="active" />
                <el-option label="停用" value="inactive" />
              </el-select>
            </el-form-item>
          </el-col>
        </el-row>

        <el-form-item label="用料明细">
          <div class="items-box">
            <div class="item-head">
              <span class="col-product">物料</span>
              <span class="col-num">用量</span>
              <span class="col-remark">备注</span>
              <span class="col-op"></span>
            </div>
            <div v-for="(item, index) in form.items" :key="index" class="item-row">
              <el-select v-model="item.product_id" filterable placeholder="选择物料" class="col-product">
                <el-option v-for="p in rawMaterials" :key="p.id" :label="`${p.name}（库存 ${p.stock_qty}）`" :value="p.id" />
              </el-select>
              <el-input-number v-model="item.quantity" :min="0.01" :precision="2" class="col-num" />
              <el-input v-model="item.remark" placeholder="选填" class="col-remark" />
              <el-button class="col-op" link type="danger" :disabled="form.items.length === 1" @click="removeItem(index)">
                <el-icon><Delete /></el-icon>
              </el-button>
            </div>
            <el-button link type="primary" @click="addItem"><el-icon><Plus /></el-icon>&nbsp;添加物料</el-button>
          </div>
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

    <el-drawer v-model="detailVisible" title="BOM 详情" size="520px">
      <div v-if="current">
        <div class="detail-head">
          <div>
            <h3>{{ current.bom_no }} · {{ current.product_name }}</h3>
            <p>{{ current.version }} · 标准产出 {{ current.base_qty }} {{ current.unit }}</p>
          </div>
          <el-tag :type="statusMap[current.status]?.type">{{ statusMap[current.status]?.text }}</el-tag>
        </div>
        <el-table :data="current.items" size="small">
          <el-table-column prop="product_name" label="物料" min-width="140" show-overflow-tooltip />
          <el-table-column prop="quantity" label="用量" width="90" align="right" />
          <el-table-column label="参考成本" width="110" align="right">
            <template #default="{ row }">{{ money(row.cost_price) }}</template>
          </el-table-column>
          <el-table-column prop="remark" label="备注" min-width="100" />
        </el-table>
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
.col-remark { width: 160px; }
.col-op { width: 32px; }
.detail-head { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 16px; }
.detail-head h3 { margin: 0 0 6px; font-size: 16px; }
.detail-head p { margin: 0; color: var(--text-secondary); font-size: 13px; }
.detail-remark { margin-top: 14px; color: var(--text-secondary); font-size: 13px; }
</style>
