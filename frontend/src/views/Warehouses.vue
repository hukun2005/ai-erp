<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { warehouseApi } from '../api'

const list = ref([])
const loading = ref(false)
const keyword = ref('')
const selectedId = ref(null)

const warehouseDialog = ref(false)
const warehouseForm = reactive({
  id: null,
  code: '',
  name: '',
  address: '',
  manager: '',
  is_default: false,
  is_active: true,
})

const locationDialog = ref(false)
const locationForm = reactive({ id: null, code: '', name: '', is_active: true })
const submitting = ref(false)

const current = computed(() => list.value.find((w) => w.id === selectedId.value) || null)
const totalStock = computed(() => list.value.reduce((sum, w) => sum + (w.stock_qty || 0), 0))

async function loadWarehouses(selectId) {
  loading.value = true
  try {
    const data = await warehouseApi.list({ keyword: keyword.value, page: 1, page_size: 200 })
    list.value = data.items
    if (selectId) selectedId.value = selectId
    if (!list.value.some((w) => w.id === selectedId.value)) {
      selectedId.value = list.value[0]?.id || null
    }
  } finally {
    loading.value = false
  }
}

function select(row) {
  selectedId.value = row.id
}

function openWarehouseCreate() {
  Object.assign(warehouseForm, {
    id: null,
    code: '',
    name: '',
    address: '',
    manager: '',
    is_default: list.value.length === 0,
    is_active: true,
  })
  warehouseDialog.value = true
}

function openWarehouseEdit(row) {
  Object.assign(warehouseForm, {
    id: row.id,
    code: row.code,
    name: row.name,
    address: row.address,
    manager: row.manager,
    is_default: row.is_default,
    is_active: row.is_active,
  })
  warehouseDialog.value = true
}

async function submitWarehouse() {
  if (!warehouseForm.code.trim() || !warehouseForm.name.trim()) {
    ElMessage.warning('仓库编码与名称不能为空')
    return
  }
  submitting.value = true
  try {
    const payload = {
      code: warehouseForm.code.trim(),
      name: warehouseForm.name.trim(),
      address: warehouseForm.address,
      manager: warehouseForm.manager,
      is_default: warehouseForm.is_default,
      is_active: warehouseForm.is_active,
    }
    if (warehouseForm.id) {
      await warehouseApi.update(warehouseForm.id, payload)
      ElMessage.success('仓库已更新')
      await loadWarehouses(warehouseForm.id)
    } else {
      await warehouseApi.create(payload)
      ElMessage.success('仓库已创建')
      await loadWarehouses()
      selectedId.value = list.value[list.value.length - 1]?.id || null
    }
    warehouseDialog.value = false
  } finally {
    submitting.value = false
  }
}

async function removeWarehouse(row) {
  await ElMessageBox.confirm(
    `确定删除仓库「${row.name}」吗？仓库下不能有库存。`,
    '删除仓库',
    { type: 'warning' }
  )
  await warehouseApi.remove(row.id)
  ElMessage.success('仓库已删除')
  loadWarehouses()
}

function openLocationCreate() {
  if (!current.value) return
  Object.assign(locationForm, { id: null, code: '', name: '', is_active: true })
  locationDialog.value = true
}

function openLocationEdit(loc) {
  Object.assign(locationForm, {
    id: loc.id,
    code: loc.code,
    name: loc.name,
    is_active: loc.is_active,
  })
  locationDialog.value = true
}

async function submitLocation() {
  if (!locationForm.code.trim()) {
    ElMessage.warning('库位编码不能为空')
    return
  }
  submitting.value = true
  try {
    const payload = {
      code: locationForm.code.trim(),
      name: locationForm.name,
      is_active: locationForm.is_active,
    }
    if (locationForm.id) {
      await warehouseApi.updateLocation(current.value.id, locationForm.id, payload)
      ElMessage.success('库位已更新')
    } else {
      await warehouseApi.createLocation(current.value.id, payload)
      ElMessage.success('库位已创建')
    }
    locationDialog.value = false
    await loadWarehouses(current.value.id)
  } finally {
    submitting.value = false
  }
}

async function removeLocation(loc) {
  await ElMessageBox.confirm(`确定删除库位「${loc.code}」吗？`, '删除库位', { type: 'warning' })
  await warehouseApi.removeLocation(current.value.id, loc.id)
  ElMessage.success('库位已删除')
  loadWarehouses(current.value.id)
}

onMounted(loadWarehouses)
</script>

<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h1 class="page-title">仓库与库位</h1>
        <p class="page-subtitle">
          共 {{ list.length }} 个仓库、库存在库 {{ totalStock }} 件；采购入库与销售出库优先使用默认仓库
        </p>
      </div>
      <el-button type="primary" @click="openWarehouseCreate">
        <el-icon><Plus /></el-icon>&nbsp;新建仓库
      </el-button>
    </div>

    <div class="split">
      <div class="card">
        <div class="panel-head">
          <h3>仓库列表</h3>
          <el-input
            v-model="keyword"
            placeholder="搜索仓库编码 / 名称"
            clearable
            style="width: 200px"
            @keyup.enter="loadWarehouses()"
            @clear="loadWarehouses()"
          />
        </div>
        <el-table :data="list" v-loading="loading" highlight-current-row @row-click="select">
          <el-table-column prop="code" label="编码" width="80" />
          <el-table-column label="仓库" min-width="150">
            <template #default="{ row }">
              <span class="name">{{ row.name }}</span>
              <el-tag v-if="row.is_default" size="small" type="success" effect="plain" class="tag">默认</el-tag>
              <el-tag v-if="!row.is_active" size="small" type="info" effect="plain" class="tag">停用</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="manager" label="负责人" width="90" />
          <el-table-column label="库存" width="90" align="right">
            <template #default="{ row }">{{ row.stock_qty }}</template>
          </el-table-column>
          <el-table-column prop="location_count" label="库位" width="70" align="center" />
          <el-table-column label="操作" width="110" align="right">
            <template #default="{ row }">
              <el-button link type="primary" @click.stop="openWarehouseEdit(row)">编辑</el-button>
              <el-button link type="danger" :disabled="row.is_default" @click.stop="removeWarehouse(row)">
                删除
              </el-button>
            </template>
          </el-table-column>
        </el-table>
      </div>

      <div class="card">
        <div class="panel-head">
          <div>
            <h3>库位管理</h3>
            <span class="hint">{{ current ? `${current.code} ${current.name}` : '请选择左侧仓库' }}</span>
          </div>
          <el-button type="primary" plain :disabled="!current" @click="openLocationCreate">
            <el-icon><Plus /></el-icon>&nbsp;新建库位
          </el-button>
        </div>
        <el-table :data="current?.locations || []" v-loading="loading" size="small">
          <el-table-column prop="code" label="库位编码" width="110" />
          <el-table-column prop="name" label="位置说明" min-width="140" show-overflow-tooltip />
          <el-table-column label="库存" width="80" align="right">
            <template #default="{ row }">{{ row.stock_qty }}</template>
          </el-table-column>
          <el-table-column label="状态" width="80" align="center">
            <template #default="{ row }">
              <el-tag size="small" :type="row.is_active ? 'success' : 'info'" effect="plain">
                {{ row.is_active ? '启用' : '停用' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="110" align="right">
            <template #default="{ row }">
              <el-button link type="primary" @click="openLocationEdit(row)">编辑</el-button>
              <el-button link type="danger" @click="removeLocation(row)">删除</el-button>
            </template>
          </el-table-column>
        </el-table>
        <div v-if="current && !current.locations.length" class="empty-text">该仓库还没有库位，请先新建</div>
      </div>
    </div>

    <el-dialog v-model="warehouseDialog" :title="warehouseForm.id ? '编辑仓库' : '新建仓库'" width="520px">
      <el-form label-width="90px">
        <el-form-item label="仓库编码" required>
          <el-input v-model="warehouseForm.code" placeholder="如 MAIN、EAST" maxlength="30" />
        </el-form-item>
        <el-form-item label="仓库名称" required>
          <el-input v-model="warehouseForm.name" placeholder="如 主仓（上海）" maxlength="80" />
        </el-form-item>
        <el-form-item label="负责人">
          <el-input v-model="warehouseForm.manager" maxlength="50" />
        </el-form-item>
        <el-form-item label="地址">
          <el-input v-model="warehouseForm.address" maxlength="200" />
        </el-form-item>
        <el-form-item label="默认仓库">
          <el-switch v-model="warehouseForm.is_default" active-text="采购入库、销售扣减优先使用" />
        </el-form-item>
        <el-form-item label="状态">
          <el-switch v-model="warehouseForm.is_active" active-text="启用" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="warehouseDialog = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="submitWarehouse">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="locationDialog" :title="locationForm.id ? '编辑库位' : '新建库位'" width="480px">
      <el-form label-width="90px">
        <el-form-item label="所属仓库">
          <span>{{ current?.name }}</span>
        </el-form-item>
        <el-form-item label="库位编码" required>
          <el-input v-model="locationForm.code" placeholder="如 A-01" maxlength="30" />
        </el-form-item>
        <el-form-item label="位置说明">
          <el-input v-model="locationForm.name" placeholder="如 A 区重型货架" maxlength="80" />
        </el-form-item>
        <el-form-item label="状态">
          <el-switch v-model="locationForm.is_active" active-text="启用" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="locationDialog = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="submitLocation">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.split {
  display: grid;
  grid-template-columns: minmax(0, 1.35fr) minmax(0, 1fr);
  gap: 16px;
  align-items: start;
}

.panel-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
}

.panel-head h3 {
  margin: 0;
  font-size: 15px;
}

.hint {
  display: block;
  margin-top: 4px;
  font-size: 12px;
  color: var(--text-secondary);
}

.name {
  font-weight: 500;
}

.tag {
  margin-left: 6px;
}

.empty-text {
  padding: 24px 0;
  text-align: center;
  font-size: 13px;
  color: #9ca3af;
}
</style>
