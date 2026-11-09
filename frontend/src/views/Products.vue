<script setup>
import { onMounted, reactive, ref } from 'vue'
import { useRouteKeyword } from '../utils/routeQuery'
import { ElMessage, ElMessageBox } from 'element-plus'
import { productApi } from '../api'
import { saveBlob } from '../utils/download'
import { useQuickCreate } from '../utils/quickCreate'

const loading = ref(false)
const list = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const categories = ref([])

const query = reactive({ keyword: '', category: '', product_type: '', only_low_stock: false })
useRouteKeyword(query, () => {
  page.value = 1
  load()
})

const dialogVisible = ref(false)
const editing = ref(false)
const formRef = ref()
const form = reactive({
  sku: '',
  barcode: '',
  name: '',
  category: '默认分类',
  product_type: 'finished_good',
  unit: '件',
  spec: '',
  cost_price: 0,
  sale_price: 0,
  stock_qty: 0,
  safety_stock: 0,
  is_active: true,
})

const rules = {
  sku: [{ required: true, message: '请输入 SKU', trigger: 'blur' }],
  name: [{ required: true, message: '请输入商品名称', trigger: 'blur' }],
}

async function load() {
  loading.value = true
  try {
    const data = await productApi.list({ ...query, page: page.value, page_size: pageSize.value })
    list.value = data.items
    total.value = data.total
  } finally {
    loading.value = false
  }
}

async function loadCategories() {
  categories.value = await productApi.categories()
}

function search() {
  page.value = 1
  load()
}

function resetQuery() {
  query.keyword = ''
  query.category = ''
  query.product_type = ''
  query.only_low_stock = false
  search()
}

// 顶栏「新建」入口带 ?new=1 时直接打开新建商品弹窗
useQuickCreate(openCreate)

function openCreate() {
  editing.value = false
  Object.assign(form, {
    sku: '',
    barcode: '',
    name: '',
    category: categories.value[0] || '默认分类',
    product_type: 'finished_good',
    unit: '件',
    spec: '',
    cost_price: 0,
    sale_price: 0,
    stock_qty: 0,
    safety_stock: 0,
    is_active: true,
  })
  dialogVisible.value = true
}

function openEdit(row) {
  editing.value = true
  Object.assign(form, row)
  dialogVisible.value = true
}

async function submit() {
  await formRef.value.validate()
  if (editing.value) {
    const { id, created_at, ...payload } = form
    await productApi.update(form.id, payload)
    ElMessage.success('商品已更新')
  } else {
    await productApi.create({ ...form })
    ElMessage.success('商品已创建')
  }
  dialogVisible.value = false
  load()
  loadCategories()
}

async function remove(row) {
  await ElMessageBox.confirm(`确定删除商品「${row.name}」吗？`, '删除确认', { type: 'warning' })
  await productApi.remove(row.id)
  ElMessage.success('已删除')
  load()
}

function typeText(type) {
  return { raw_material: '原料', finished_good: '成品' }[type] || '-'
}

function typeTag(type) {
  return type === 'raw_material' ? 'warning' : 'success'
}

function stockTag(row) {
  if (row.stock_qty <= 0) return 'danger'
  if (row.stock_qty <= row.safety_stock) return 'warning'
  return 'success'
}

const importVisible = ref(false)
const importResult = ref(null)
const importing = ref(false)

function openImport() {
  importResult.value = null
  importVisible.value = true
}

async function downloadTemplate() {
  saveBlob(await productApi.templateUrl(), '商品导入模板.xlsx')
}

async function onImportFile(uploadFile) {
  const file = uploadFile?.raw
  if (!file) return
  importing.value = true
  try {
    const result = await productApi.import(file)
    importResult.value = result
    ElMessage.success(`导入完成：新增 ${result.created} 条`)
    if (result.created) {
      load()
      loadCategories()
    }
  } finally {
    importing.value = false
  }
}

onMounted(() => {
  load()
  loadCategories()
})
</script>

<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h1 class="page-title">商品管理</h1>
        <p class="page-subtitle">维护商品档案、成本售价与库存安全线</p>
      </div>
      <div style="display: flex; gap: 8px">
        <el-button v-perm="'product:manage'" @click="openImport"><el-icon><Upload /></el-icon>&nbsp;批量导入</el-button>
        <el-button v-perm="'product:manage'" type="primary" @click="openCreate"><el-icon><Plus /></el-icon>&nbsp;新建商品</el-button>
      </div>
    </div>

    <div class="card">
      <div class="toolbar">
        <el-input v-model="query.keyword" placeholder="搜索 SKU / 条码 / 商品名称" clearable style="width: 240px" @keyup.enter="search" @clear="search" />
        <el-select v-model="query.category" placeholder="全部分类" clearable style="width: 160px" @change="search">
          <el-option v-for="c in categories" :key="c" :label="c" :value="c" />
        </el-select>
        <el-select v-model="query.product_type" placeholder="全部物料类型" clearable style="width: 150px" @change="search">
          <el-option label="原料" value="raw_material" />
          <el-option label="成品" value="finished_good" />
        </el-select>
        <el-switch v-model="query.only_low_stock" active-text="只看库存预警" @change="search" />
        <el-button @click="search">查询</el-button>
        <el-button text @click="resetQuery">重置</el-button>
      </div>

      <el-table :data="list" v-loading="loading" size="default">
        <el-table-column prop="sku" label="SKU" width="110" />
        <el-table-column prop="barcode" label="条码" width="150" show-overflow-tooltip>
          <template #default="{ row }">{{ row.barcode || '—' }}</template>
        </el-table-column>
        <el-table-column prop="name" label="商品名称" min-width="160" show-overflow-tooltip />
        <el-table-column prop="category" label="分类" width="110" />
        <el-table-column label="物料类型" width="90" align="center">
          <template #default="{ row }">
            <el-tag :type="typeTag(row.product_type)" size="small" effect="light">{{ typeText(row.product_type) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="spec" label="规格" width="100" show-overflow-tooltip />
        <el-table-column label="成本价" width="100" align="right">
          <template #default="{ row }">¥{{ row.cost_price.toFixed(2) }}</template>
        </el-table-column>
        <el-table-column label="售价" width="100" align="right">
          <template #default="{ row }"><b>¥{{ row.sale_price.toFixed(2) }}</b></template>
        </el-table-column>
        <el-table-column label="库存" width="110" align="center">
          <template #default="{ row }">
            <el-tag :type="stockTag(row)" effect="light" size="small">{{ row.stock_qty }} {{ row.unit }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="safety_stock" label="安全库存" width="90" align="center" />
        <el-table-column label="状态" width="80" align="center">
          <template #default="{ row }">
            <el-tag :type="row.is_active ? 'success' : 'info'" size="small" effect="plain">
              {{ row.is_active ? '在售' : '下架' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="120" fixed="right">
          <template #default="{ row }">
            <el-button v-perm="'product:manage'" link type="primary" @click="openEdit(row)">编辑</el-button>
            <el-button v-perm="'product:manage'" link type="danger" @click="remove(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>

      <div class="pager">
        <el-pagination
          v-model:current-page="page"
          :page-size="pageSize"
          :total="total"
          layout="total, prev, pager, next"
          @current-change="load"
        />
      </div>
    </div>

    <el-dialog v-model="importVisible" title="批量导入商品" width="660px">
      <el-alert
        type="info"
        :closable="false"
        show-icon
        title="请先下载模板按表头填写后上传。SKU 重复的行自动跳过；期初库存会写入库存流水，保证账实一致。"
      />
      <div style="display: flex; gap: 12px; align-items: center; margin: 14px 0">
        <el-button @click="downloadTemplate"><el-icon><Download /></el-icon>&nbsp;下载模板</el-button>
        <el-upload :show-file-list="false" :auto-upload="false" accept=".xlsx" :on-change="onImportFile">
          <el-button type="primary" :loading="importing">
            <el-icon><Upload /></el-icon>&nbsp;选择文件并导入
          </el-button>
        </el-upload>
      </div>
      <template v-if="importResult">
        <p style="font-size: 13px; color: var(--text-secondary); margin: 0 0 10px">
          新增 <b style="color: #16a34a">{{ importResult.created }}</b> 条，
          跳过 {{ importResult.skipped }} 条，
          失败 {{ importResult.error_count - importResult.skipped }} 条
        </p>
        <el-table v-if="importResult.errors.length" :data="importResult.errors" size="small" max-height="240">
          <el-table-column prop="row" label="行号" width="80" />
          <el-table-column prop="reason" label="原因" min-width="200" show-overflow-tooltip />
        </el-table>
      </template>
    </el-dialog>

    <el-dialog v-model="dialogVisible" :title="editing ? '编辑商品' : '新建商品'" width="600px">
      <el-form ref="formRef" :model="form" :rules="rules" label-width="90px">
        <el-row :gutter="14">
          <el-col :span="12">
            <el-form-item label="SKU" prop="sku">
              <el-input v-model="form.sku" :disabled="editing" placeholder="如 SKU-1001" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="条码">
              <el-input v-model="form.barcode" placeholder="扫码枪录入或手填，用于扫码开单" clearable />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="名称" prop="name">
              <el-input v-model="form.name" placeholder="商品名称" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="分类">
              <el-select v-model="form.category" filterable allow-create style="width: 100%">
                <el-option v-for="c in categories" :key="c" :label="c" :value="c" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="物料类型">
              <el-select v-model="form.product_type" style="width: 100%">
                <el-option label="原料" value="raw_material" />
                <el-option label="成品" value="finished_good" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="规格">
              <el-input v-model="form.spec" placeholder="如 12寸 / 标准款" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="单位">
              <el-input v-model="form.unit" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="是否在售">
              <el-switch v-model="form.is_active" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="成本价">
              <el-input-number v-model="form.cost_price" :min="0" :precision="2" style="width: 100%" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="销售价">
              <el-input-number v-model="form.sale_price" :min="0" :precision="2" style="width: 100%" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="当前库存">
              <el-input-number v-model="form.stock_qty" :min="0" style="width: 100%" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="安全库存">
              <el-input-number v-model="form.safety_stock" :min="0" style="width: 100%" />
            </el-form-item>
          </el-col>
        </el-row>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" @click="submit">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.pager {
  display: flex;
  justify-content: flex-end;
  margin-top: 16px;
}
</style>
