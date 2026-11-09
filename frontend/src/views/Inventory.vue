<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRouteKeyword } from '../utils/routeQuery'
import { ElMessage } from 'element-plus'
import { inventoryApi, warehouseApi } from '../api'
import { useUserStore } from '../stores/user'

const userStore = useUserStore()
const canAdjust = userStore.hasPerm('inventory:adjust')

const summary = ref({})
const list = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const loading = ref(false)
const stockExporting = ref(false)
const activeType = ref('all')
const query = reactive({ keyword: '', only_low: false })
const typeParam = computed(() => (activeType.value === 'all' ? undefined : activeType.value))
const route = useRouteKeyword(query, () => {
  page.value = 1
  distPage.value = 1
  logPage.value = 1
  distQuery.keyword = query.keyword
  logQuery.keyword = query.keyword
  refreshAll()
})

// 看板「库存预警」卡片用 ?low=1 直达只看预警商品
if (route.query.low === '1') query.only_low = true
watch(
  () => route.query.low,
  (value) => {
    const low = value === '1'
    if (low === query.only_low) return
    query.only_low = low
    page.value = 1
    refreshAll()
  },
)

const warehouses = ref([])
const distList = ref([])
const distTotal = ref(0)
const distPage = ref(1)
const distLoading = ref(false)
const distExporting = ref(false)
const distQuery = reactive({ keyword: '', warehouse_id: null })

const logs = ref([])
const logTotal = ref(0)
const logPage = ref(1)
const logLoading = ref(false)
const logQuery = reactive({ keyword: '', change_type: '', warehouse_id: null })
const logRange = ref([])
const exporting = ref(false)

const dialogVisible = ref(false)
const adjustForm = reactive({
  product_id: null,
  product_name: '',
  change_type: 'in',
  quantity: 1,
  reason: '',
  warehouse_id: null,
  location_id: null,
})
const submitLoading = ref(false)

const adjustLocations = computed(() => {
  const warehouse = warehouses.value.find((w) => w.id === adjustForm.warehouse_id)
  return warehouse ? warehouse.locations : []
})

async function loadWarehouses() {
  warehouses.value = await warehouseApi.options()
}

async function loadSummary() {
  summary.value = await inventoryApi.summary()
}

async function loadDistribution() {
  distLoading.value = true
  try {
    const data = await inventoryApi.distribution({
      keyword: distQuery.keyword,
      warehouse_id: distQuery.warehouse_id || undefined,
      product_type: typeParam.value,
      page: distPage.value,
      page_size: 10,
    })
    distList.value = data.items
    distTotal.value = data.total
  } finally {
    distLoading.value = false
  }
}

function searchDistribution() {
  distPage.value = 1
  loadDistribution()
}

async function loadStock() {
  loading.value = true
  try {
    const data = await inventoryApi.stock({
      ...query,
      product_type: typeParam.value,
      page: page.value,
      page_size: pageSize.value,
    })
    list.value = data.items
    total.value = data.total
  } finally {
    loading.value = false
  }
}

async function loadLogs() {
  logLoading.value = true
  try {
    const data = await inventoryApi.logs({
      ...logQuery,
      warehouse_id: logQuery.warehouse_id || undefined,
      product_type: typeParam.value,
      start_date: logRange.value?.[0] || '',
      end_date: logRange.value?.[1] || '',
      page: logPage.value,
      page_size: 10,
    })
    logs.value = data.items
    logTotal.value = data.total
  } finally {
    logLoading.value = false
  }
}

function searchLogs() {
  logPage.value = 1
  loadLogs()
}

async function saveBlob(blob, filename) {
  if (!(blob instanceof Blob) || blob.size === 0) {
    ElMessage.error('导出失败：返回的文件为空，请稍后重试')
    return false
  }
  if (blob.type.includes('application/json')) {
    let detail = ''
    try {
      const json = JSON.parse((await blob.text()).trim())
      detail = json?.detail || ''
    } catch {
      // 解析失败时使用通用提示
    }
    ElMessage.error(detail ? `导出失败：${detail}` : '导出失败：服务返回了错误信息，请检查筛选条件')
    return false
  }
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
  return true
}

async function exportDistribution() {
  if (distExporting.value) return
  if (distLoading.value) {
    ElMessage.warning('库存分布正在加载，请稍后再导出')
    return
  }
  if (distQuery.warehouse_id && !warehouses.value.some((w) => w.id === distQuery.warehouse_id)) {
    ElMessage.warning('所选仓库已不存在，请重新选择仓库')
    return
  }
  if (!distTotal.value) {
    ElMessage.warning('当前筛选条件下没有可导出的库存分布记录')
    return
  }
  const keyword = distQuery.keyword.trim()
  if (keyword.length > 50) {
    ElMessage.warning('搜索关键字不能超过 50 个字符')
    return
  }
  const warehouse = warehouses.value.find((w) => w.id === distQuery.warehouse_id)
  distExporting.value = true
  try {
    const blob = await inventoryApi.exportDistribution({
      keyword,
      warehouse_id: distQuery.warehouse_id || undefined,
      product_type: typeParam.value,
    })
    const typeName = activeType.value === 'all' ? '' : `${typeText(activeType.value)}_`
    if (!(await saveBlob(blob, `库存分布报表_${typeName}${warehouse?.code || '全部仓库'}_${new Date().toISOString().slice(0, 10)}.xlsx`))) return
    ElMessage.success(`库存分布报表已导出${warehouse ? `（${warehouse.code} ${warehouse.name}）` : '（全部仓库）'}`)
  } catch (err) {
    if (!err?.response) ElMessage.error('报表导出失败，请检查网络后重试')
  } finally {
    distExporting.value = false
  }
}

async function exportStock() {
  if (stockExporting.value) return
  if (loading.value) {
    ElMessage.warning('库存正在加载，请稍后再导出')
    return
  }
  if (!total.value) {
    ElMessage.warning('当前筛选条件下没有可导出的库存记录')
    return
  }
  const keyword = query.keyword.trim()
  if (keyword.length > 50) {
    ElMessage.warning('搜索关键字不能超过 50 个字符')
    return
  }
  stockExporting.value = true
  try {
    const blob = await inventoryApi.exportStock({
      keyword,
      only_low: query.only_low,
      product_type: typeParam.value,
    })
    const typeName = activeType.value === 'all' ? '' : `${typeText(activeType.value)}_`
    const scope = `${typeName}${query.only_low ? '预警商品' : '全部商品'}`
    if (!(await saveBlob(blob, `库存报表_${scope}_${new Date().toISOString().slice(0, 10)}.xlsx`))) return
    ElMessage.success(`库存报表已导出（${scope}）`)
  } catch (err) {
    if (!err?.response) ElMessage.error('报表导出失败，请检查网络后重试')
  } finally {
    stockExporting.value = false
  }
}

async function exportExcel() {
  if (exporting.value) return
  if (logLoading.value) {
    ElMessage.warning('流水正在加载，请稍后再导出')
    return
  }
  if (!logTotal.value) {
    ElMessage.warning('当前筛选条件下没有可导出的流水记录')
    return
  }
  const [start, end] = logRange.value || []
  if (start && end && start > end) {
    ElMessage.warning('开始日期不能晚于结束日期')
    return
  }
  if (logQuery.warehouse_id && !warehouses.value.some((w) => w.id === logQuery.warehouse_id)) {
    ElMessage.warning('所选仓库已不存在，请重新选择仓库')
    return
  }
  const keyword = logQuery.keyword.trim()
  if (keyword.length > 50) {
    ElMessage.warning('搜索关键字不能超过 50 个字符')
    return
  }

  exporting.value = true
  try {
    const blob = await inventoryApi.exportLogs({
      ...logQuery,
      keyword,
      warehouse_id: logQuery.warehouse_id || undefined,
      product_type: typeParam.value,
      start_date: start || '',
      end_date: end || '',
    })
    const warehouse = warehouses.value.find((w) => w.id === logQuery.warehouse_id)
    const typeName = activeType.value === 'all' ? '' : `${typeText(activeType.value)}_`
    if (!(await saveBlob(blob, `库存出入库报表_${typeName}${warehouse?.code || '全部仓库'}_${new Date().toISOString().slice(0, 10)}.xlsx`))) return
    ElMessage.success('报表已导出，可用 Excel 打开')
  } catch (err) {
    // 服务端返回的错误已由请求拦截器提示，这里只处理本地/网络异常
    if (!err?.response) ElMessage.error('报表导出失败，请检查网络后重试')
  } finally {
    exporting.value = false
  }
}

function search() {
  page.value = 1
  loadStock()
}

function refreshAll() {
  loadWarehouses()
  loadSummary()
  loadStock()
  loadDistribution()
  loadLogs()
}

function openAdjust(row) {
  const warehouse = warehouses.value.find((w) => w.is_default && w.is_active) || warehouses.value[0]
  Object.assign(adjustForm, {
    product_id: row.id,
    product_name: `${row.sku} ${row.name}`,
    change_type: 'in',
    quantity: 1,
    reason: '',
    warehouse_id: warehouse?.id || null,
    location_id: warehouse?.locations?.[0]?.id || null,
  })
  dialogVisible.value = true
}

function onWarehouseChange() {
  // 切换仓库后库位默认取该仓库第一个可用库位
  const locations = adjustLocations.value.filter((loc) => loc.is_active)
  adjustForm.location_id = locations[0]?.id || null
}

async function submitAdjust() {
  if (!adjustForm.warehouse_id || !adjustForm.location_id) {
    ElMessage.warning('请选择仓库与库位')
    return
  }
  if (!adjustForm.quantity || adjustForm.quantity <= 0) {
    ElMessage.warning('请输入大于 0 的数量')
    return
  }
  submitLoading.value = true
  try {
    const data = await inventoryApi.adjust({
      product_id: adjustForm.product_id,
      change_type: adjustForm.change_type,
      quantity: adjustForm.quantity,
      warehouse_id: adjustForm.warehouse_id,
      location_id: adjustForm.location_id,
      reason: adjustForm.reason,
    })
    ElMessage.success('库存已更新')
    if (data?.auto_alert?.sent) {
      ElMessage.success(data.auto_alert.message)
    }
    dialogVisible.value = false
    refreshAll()
  } finally {
    submitLoading.value = false
  }
}

function onTypeChange() {
  page.value = 1
  distPage.value = 1
  logPage.value = 1
  loadStock()
  loadDistribution()
  loadLogs()
}

function typeText(type) {
  return { raw_material: '原料', finished_good: '成品' }[type] || '-'
}

function typeTag(type) {
  return type === 'raw_material' ? 'warning' : 'success'
}

function statusTag(status) {
  return status === '缺货' ? 'danger' : status === '预警' ? 'warning' : 'success'
}

// 30 天内到期或已过期的批次标红，提醒优先出库
function expiringSoon(value) {
  if (!value) return false
  const days = (new Date(value).getTime() - Date.now()) / 86400000
  return days <= 30
}

function changeTag(type) {
  return { in: 'success', out: 'danger', check: 'warning' }[type] || 'info'
}

function changeText(type) {
  return { in: '入库', out: '出库', check: '盘点' }[type] || type
}

function money(value) {
  return '¥' + Number(value || 0).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

onMounted(refreshAll)
</script>

<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h1 class="page-title">库存管理</h1>
        <p class="page-subtitle">实时库存、出入库登记与盘点流水（{{ userStore.roleName }}视角）</p>
      </div>
      <el-button @click="refreshAll">
        <el-icon><Refresh /></el-icon>&nbsp;刷新
      </el-button>
    </div>

    <div class="metric-group">
      <div class="metric-group-head">
        <h3>原料库存</h3>
        <span>用于采购、BOM 用料与生产领料</span>
      </div>
      <div class="metric-grid">
        <div class="metric-card">
          <span>原料商品</span>
          <strong>{{ summary.raw_material?.product_count || 0 }}</strong>
          <p>其中缺货 {{ summary.raw_material?.out_of_stock_count || 0 }} 个</p>
        </div>
        <div class="metric-card">
          <span>库存总量</span>
          <strong>{{ summary.raw_material?.total_qty || 0 }}</strong>
          <p>按商品自身计量单位汇总</p>
        </div>
        <div class="metric-card">
          <span>库存金额</span>
          <strong>{{ money(summary.raw_material?.total_value) }}</strong>
          <p>按成本价核算</p>
        </div>
        <div class="metric-card warning">
          <span>库存预警</span>
          <strong>{{ summary.raw_material?.low_stock_count || 0 }}</strong>
          <p>低于安全库存</p>
        </div>
      </div>
    </div>

    <div class="metric-group">
      <div class="metric-group-head">
        <h3>成品库存</h3>
        <span>用于销售出库与生产完工入库</span>
      </div>
      <div class="metric-grid">
        <div class="metric-card">
          <span>成品商品</span>
          <strong>{{ summary.finished_good?.product_count || 0 }}</strong>
          <p>其中缺货 {{ summary.finished_good?.out_of_stock_count || 0 }} 个</p>
        </div>
        <div class="metric-card">
          <span>库存总量</span>
          <strong>{{ summary.finished_good?.total_qty || 0 }}</strong>
          <p>按商品自身计量单位汇总</p>
        </div>
        <div class="metric-card">
          <span>库存金额</span>
          <strong>{{ money(summary.finished_good?.total_value) }}</strong>
          <p>按成本价核算</p>
        </div>
        <div class="metric-card warning">
          <span>库存预警</span>
          <strong>{{ summary.finished_good?.low_stock_count || 0 }}</strong>
          <p>低于安全库存</p>
        </div>
      </div>
    </div>

    <div class="card">
      <el-tabs v-model="activeType" class="type-tabs" @tab-change="onTypeChange">
        <el-tab-pane label="全部" name="all" />
        <el-tab-pane label="原料" name="raw_material" />
        <el-tab-pane label="成品" name="finished_good" />
      </el-tabs>
      <div class="toolbar">
        <el-input
          v-model="query.keyword"
          placeholder="搜索 SKU / 商品名称"
          clearable
          style="width: 240px"
          @keyup.enter="search"
          @clear="search"
        />
        <el-switch v-model="query.only_low" active-text="只看预警商品" @change="search" />
        <el-button @click="search">查询</el-button>
        <el-button
          type="primary"
          plain
          :loading="stockExporting"
          :disabled="loading"
          @click="exportStock"
        >
          <el-icon><Download /></el-icon>&nbsp;导出 Excel
        </el-button>
      </div>

      <el-table :data="list" v-loading="loading">
        <el-table-column prop="sku" label="SKU" width="110" />
        <el-table-column prop="name" label="商品" min-width="170" show-overflow-tooltip />
        <el-table-column prop="category" label="分类" width="110" />
        <el-table-column label="物料类型" width="90" align="center">
          <template #default="{ row }">
            <el-tag :type="typeTag(row.product_type)" size="small" effect="light">{{ typeText(row.product_type) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="当前库存" width="100" align="center">
          <template #default="{ row }">{{ row.stock_qty }} {{ row.unit }}</template>
        </el-table-column>
        <el-table-column label="可用" width="90" align="center">
          <template #default="{ row }">
            <span :class="{ 'text-warn': row.reserved_qty > 0 }">{{ row.available_qty ?? row.stock_qty }}</span>
          </template>
        </el-table-column>
        <el-table-column label="已占用" width="90" align="center">
          <template #default="{ row }">
            <span v-if="row.reserved_qty" class="reserved">-{{ row.reserved_qty }}</span>
            <span v-else class="no-perm">-</span>
          </template>
        </el-table-column>
        <el-table-column label="在途" width="90" align="center">
          <template #default="{ row }">
            <span v-if="row.in_transit_qty" class="in-transit">+{{ row.in_transit_qty }}</span>
            <span v-else class="no-perm">-</span>
          </template>
        </el-table-column>
        <el-table-column prop="safety_stock" label="安全库存" width="90" align="center" />
        <el-table-column label="库存金额" width="120" align="right">
          <template #default="{ row }">{{ money(row.stock_value) }}</template>
        </el-table-column>
        <el-table-column label="状态" width="80" align="center">
          <template #default="{ row }">
            <el-tag :type="statusTag(row.status)" size="small" effect="light">{{ row.status }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="90" fixed="right">
          <template #default="{ row }">
            <el-button v-perm="'inventory:adjust'" link type="primary" @click="openAdjust(row)">出入库</el-button>
            <span v-if="!canAdjust" class="no-perm">只读</span>
          </template>
        </el-table-column>
      </el-table>

      <div class="pager">
        <el-pagination
          v-model:current-page="page"
          :page-size="pageSize"
          :total="total"
          layout="total, prev, pager, next"
          @current-change="loadStock"
        />
      </div>
    </div>

    <div class="card dist-card">
      <div class="panel-head">
        <h3>库存分布（按仓库 / 库位）</h3>
        <span class="hint">共 {{ distTotal }} 条库位库存，出库时默认仓库优先扣减</span>
      </div>
      <div class="toolbar">
        <el-input
          v-model="distQuery.keyword"
          placeholder="搜索商品 / 仓库"
          clearable
          maxlength="50"
          style="width: 220px"
          @keyup.enter="searchDistribution"
          @clear="searchDistribution"
        />
        <el-select
          v-model="distQuery.warehouse_id"
          placeholder="全部仓库"
          clearable
          style="width: 200px"
          @change="searchDistribution"
        >
          <el-option
            v-for="w in warehouses"
            :key="w.id"
            :label="`${w.code} ${w.name}`"
            :value="w.id"
          />
        </el-select>
        <el-button @click="searchDistribution">查询</el-button>
        <el-button
          type="primary"
          plain
          :loading="distExporting"
          :disabled="distLoading"
          @click="exportDistribution"
        >
          <el-icon><Download /></el-icon>&nbsp;按仓库导出 Excel
        </el-button>
      </div>
      <el-table :data="distList" v-loading="distLoading" size="small">
        <el-table-column label="商品" min-width="190">
          <template #default="{ row }">{{ row.product_sku }} {{ row.product_name }}</template>
        </el-table-column>
        <el-table-column label="物料类型" width="90" align="center">
          <template #default="{ row }">
            <el-tag :type="typeTag(row.product_type)" size="small" effect="light">{{ typeText(row.product_type) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="warehouse_name" label="仓库" width="160" />
        <el-table-column label="库位" width="170">
          <template #default="{ row }">{{ row.location_code }} {{ row.location_name }}</template>
        </el-table-column>
        <el-table-column label="批次" width="130">
          <template #default="{ row }">
            <span v-if="row.batch_no">{{ row.batch_no }}</span>
            <span v-else class="no-perm">不启用批次</span>
          </template>
        </el-table-column>
        <el-table-column label="效期" width="120">
          <template #default="{ row }">
            <span v-if="row.expiry_date" :class="{ 'text-warn': expiringSoon(row.expiry_date) }">
              {{ String(row.expiry_date).slice(0, 10) }}
            </span>
            <span v-else class="no-perm">-</span>
          </template>
        </el-table-column>
        <el-table-column label="库位库存" width="110" align="right">
          <template #default="{ row }">{{ row.quantity }} {{ row.unit }}</template>
        </el-table-column>
        <el-table-column label="更新时间" width="170">
          <template #default="{ row }">{{ row.updated_at?.replace('T', ' ').slice(0, 19) }}</template>
        </el-table-column>
      </el-table>
      <div class="pager">
        <el-pagination
          v-model:current-page="distPage"
          :page-size="10"
          :total="distTotal"
          layout="total, prev, pager, next"
          @current-change="loadDistribution"
        />
      </div>
      <div v-if="!distLoading && !distList.length" class="empty-text">暂无库位库存数据</div>
    </div>

    <div class="card logs-card">
      <div class="panel-head">
        <h3>库存变动流水</h3>
        <span class="hint">出入库与盘点记录，用于库存追溯，可导出 Excel 报表</span>
      </div>
      <div class="toolbar log-toolbar">
        <el-input
          v-model="logQuery.keyword"
          placeholder="搜索商品 / 原因"
          clearable
          maxlength="50"
          style="width: 200px"
          @keyup.enter="searchLogs"
          @clear="searchLogs"
        />
        <el-select v-model="logQuery.change_type" placeholder="全部类型" clearable style="width: 120px" @change="searchLogs">
          <el-option label="入库" value="in" />
          <el-option label="出库" value="out" />
          <el-option label="盘点" value="check" />
        </el-select>
        <el-select
          v-model="logQuery.warehouse_id"
          placeholder="全部仓库"
          clearable
          style="width: 180px"
          @change="searchLogs"
        >
          <el-option
            v-for="w in warehouses"
            :key="w.id"
            :label="`${w.code} ${w.name}`"
            :value="w.id"
          />
        </el-select>
        <el-date-picker
          v-model="logRange"
          type="daterange"
          value-format="YYYY-MM-DD"
          range-separator="至"
          start-placeholder="开始日期"
          end-placeholder="结束日期"
          style="width: 260px"
          @change="searchLogs"
        />
        <el-button @click="searchLogs">查询</el-button>
        <el-button
          type="primary"
          plain
          :loading="exporting"
          :disabled="logLoading"
          @click="exportExcel"
        >
          <el-icon><Download /></el-icon>&nbsp;导出 Excel
        </el-button>
      </div>
      <el-table :data="logs" v-loading="logLoading" size="small">
        <el-table-column prop="created_at" label="时间" width="170">
          <template #default="{ row }">{{ row.created_at?.replace('T', ' ').slice(0, 19) }}</template>
        </el-table-column>
        <el-table-column label="商品" min-width="170">
          <template #default="{ row }">{{ row.product_sku }} {{ row.product_name }}</template>
        </el-table-column>
        <el-table-column label="物料类型" width="90" align="center">
          <template #default="{ row }">
            <el-tag :type="typeTag(row.product_type)" size="small" effect="light">{{ typeText(row.product_type) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="仓库 / 库位" min-width="170">
          <template #default="{ row }">
            {{ row.warehouse_name || '-' }} / {{ row.location_code || '-' }}
          </template>
        </el-table-column>
        <el-table-column label="类型" width="80" align="center">
          <template #default="{ row }">
            <el-tag :type="changeTag(row.change_type)" size="small" effect="plain">{{ changeText(row.change_type) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="业务类型" width="110" align="center">
          <template #default="{ row }">
            <span class="biz">{{ row.biz_type || '-' }}</span>
            <div v-if="row.ref_no" class="ref-no">{{ row.ref_no }}</div>
          </template>
        </el-table-column>
        <el-table-column label="变动" width="90" align="center">
          <template #default="{ row }">
            <b :class="row.change_qty > 0 ? 'up' : 'down'">{{ row.change_qty > 0 ? '+' : '' }}{{ row.change_qty }}</b>
          </template>
        </el-table-column>
        <el-table-column label="库位变动前 → 后" width="150" align="center">
          <template #default="{ row }">{{ row.stock_before }} → {{ row.stock_after }}</template>
        </el-table-column>
        <el-table-column prop="reason" label="备注" min-width="160" show-overflow-tooltip />
        <el-table-column prop="created_by" label="操作人" width="100" />
      </el-table>
      <div class="pager">
        <el-pagination
          v-model:current-page="logPage"
          :page-size="10"
          :total="logTotal"
          layout="total, prev, pager, next"
          @current-change="loadLogs"
        />
      </div>
      <div v-if="!logLoading && !logs.length" class="empty-text">暂无库存变动记录</div>
    </div>

    <el-dialog v-model="dialogVisible" title="库存调整" width="500px">
      <el-form label-width="90px">
        <el-form-item label="商品">{{ adjustForm.product_name }}</el-form-item>
        <el-form-item label="仓库">
          <el-select v-model="adjustForm.warehouse_id" style="width: 100%" @change="onWarehouseChange">
            <el-option
              v-for="w in warehouses"
              :key="w.id"
              :label="`${w.code} ${w.name}${w.is_default ? '（默认）' : ''}`"
              :value="w.id"
              :disabled="!w.is_active"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="库位">
          <el-select v-model="adjustForm.location_id" placeholder="请选择库位" style="width: 100%">
            <el-option
              v-for="loc in adjustLocations"
              :key="loc.id"
              :label="`${loc.code} ${loc.name}`"
              :value="loc.id"
              :disabled="!loc.is_active"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="调整类型">
          <el-radio-group v-model="adjustForm.change_type">
            <el-radio-button label="in">入库</el-radio-button>
            <el-radio-button label="out">出库</el-radio-button>
            <el-radio-button label="check">盘点</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item :label="adjustForm.change_type === 'check' ? '库位盘点后数量' : '数量'">
          <el-input-number v-model="adjustForm.quantity" :min="1" style="width: 100%" />
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="adjustForm.reason" placeholder="如：供应商到货入库 / 报废出库" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitLoading" @click="submitAdjust">确定</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.metric-group {
  margin-bottom: 16px;
}

.metric-group-head {
  display: flex;
  align-items: baseline;
  gap: 10px;
  margin-bottom: 8px;
}

.metric-group-head h3 {
  margin: 0;
  font-size: 15px;
}

.metric-group-head span {
  font-size: 12px;
  color: var(--text-secondary);
}

.type-tabs {
  margin-bottom: 4px;
}

.metric-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 14px;
  margin-bottom: 16px;
}

.metric-card {
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 16px 18px;
}

.metric-card span {
  font-size: 12px;
  color: var(--text-secondary);
}

.metric-card strong {
  display: block;
  font-size: 22px;
  margin: 6px 0 2px;
}

.metric-card p {
  margin: 0;
  font-size: 12px;
  color: #9ca3af;
}

.metric-card.warning strong {
  color: var(--warning);
}

.metric-card.danger strong {
  color: var(--danger);
}

.pager {
  display: flex;
  justify-content: flex-end;
  margin-top: 16px;
}

.logs-card {
  margin-top: 16px;
}

.dist-card {
  margin-top: 16px;
}

.ref-no {
  margin-top: 2px;
  font-size: 11px;
  color: #9ca3af;
}

.log-toolbar {
  margin-bottom: 12px;
}

.panel-head {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  margin-bottom: 12px;
}

.panel-head h3 {
  margin: 0;
  font-size: 15px;
}

.hint {
  font-size: 12px;
  color: var(--text-secondary);
}

.no-perm {
  font-size: 12px;
  color: #9ca3af;
}

.text-warn {
  color: #b45309;
  font-weight: 600;
}

.reserved {
  color: #b45309;
}

.in-transit {
  color: #b45309;
  font-weight: 600;
}

.up {
  color: var(--success);
}

.down {
  color: var(--danger);
}
</style>
