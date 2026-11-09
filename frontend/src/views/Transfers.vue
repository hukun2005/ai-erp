<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { transferApi, warehouseApi } from '../api'

const loading = ref(false)
const list = ref([])
const total = ref(0)
const page = ref(1)
const query = reactive({ keyword: '', status: '' })
const stats = ref({ in_transit_orders: 0, in_transit_qty: 0 })

const warehouses = ref([])
const sourceStock = ref([])
const dialogVisible = ref(false)
const detailVisible = ref(false)
const current = ref(null)
const submitting = ref(false)

const statusMap = {
  draft: { text: '草稿', type: 'info' },
  in_transit: { text: '在途', type: 'warning' },
  completed: { text: '已完成', type: 'success' },
  cancelled: { text: '已取消', type: 'danger' },
}

const blankForm = () => ({
  from_warehouse_id: null,
  to_warehouse_id: null,
  remark: '',
  items: [{ key: '', product_id: null, from_location_id: null, quantity: 1, to_location_id: null }],
})
const form = reactive(blankForm())

const activeWarehouses = computed(() => warehouses.value.filter((w) => w.is_active))
const toWarehouse = computed(() => warehouses.value.find((w) => w.id === form.to_warehouse_id))
const toLocations = computed(() =>
  (toWarehouse.value?.locations || []).filter((loc) => loc.is_active)
)

async function load() {
  loading.value = true
  try {
    const data = await transferApi.list({ ...query, page: page.value, page_size: 20 })
    list.value = data.items
    total.value = data.total
    stats.value = {
      in_transit_orders: data.in_transit_orders || 0,
      in_transit_qty: data.in_transit_qty || 0,
    }
  } finally {
    loading.value = false
  }
}

function search() {
  page.value = 1
  load()
}

async function loadSourceStock() {
  sourceStock.value = []
  if (!form.from_warehouse_id) return
  sourceStock.value = await transferApi.sourceStock({ warehouse_id: form.from_warehouse_id })
}

async function onFromChange() {
  if (form.to_warehouse_id === form.from_warehouse_id) form.to_warehouse_id = null
  form.items = [{ key: '', product_id: null, from_location_id: null, quantity: 1, to_location_id: null }]
  await loadSourceStock()
}

function onToChange() {
  const defaultLoc = toLocations.value[0]?.id || null
  form.items.forEach((item) => {
    if (!toLocations.value.some((loc) => loc.id === item.to_location_id)) {
      item.to_location_id = defaultLoc
    }
  })
}

function onSourceSelect(item) {
  const [pid, lid] = (item.key || '').split(':')
  item.product_id = pid ? Number(pid) : null
  item.from_location_id = lid ? Number(lid) : null
  const row = findStock(item)
  if (row && item.quantity > row.quantity) item.quantity = row.quantity
}

function findStock(item) {
  return sourceStock.value.find(
    (s) => s.product_id === item.product_id && s.location_id === item.from_location_id
  )
}

function availableOf(item) {
  return findStock(item)?.quantity || 0
}

function addItem() {
  form.items.push({
    key: '',
    product_id: null,
    from_location_id: null,
    quantity: 1,
    to_location_id: toLocations.value[0]?.id || null,
  })
}

function removeItem(index) {
  if (form.items.length === 1) return
  form.items.splice(index, 1)
}

async function openCreate() {
  Object.assign(form, blankForm())
  sourceStock.value = []
  dialogVisible.value = true
  if (!warehouses.value.length) warehouses.value = await warehouseApi.options()
}

async function submit() {
  if (!form.from_warehouse_id || !form.to_warehouse_id) {
    return ElMessage.warning('请选择调出与调入仓库')
  }
  if (form.from_warehouse_id === form.to_warehouse_id) {
    return ElMessage.warning('调出仓库与调入仓库不能相同')
  }
  for (const item of form.items) {
    if (!item.key || !item.product_id) return ElMessage.warning('请完整填写调拨商品')
    if (item.quantity <= 0) return ElMessage.warning('调拨数量必须大于 0')
    if (item.quantity > availableOf(item)) {
      return ElMessage.warning(`「${findStock(item)?.product_name}」该库位仅剩 ${availableOf(item)} 件`)
    }
    if (!item.to_location_id) return ElMessage.warning('请选择调入库位')
  }
  submitting.value = true
  try {
    await transferApi.create({
      from_warehouse_id: form.from_warehouse_id,
      to_warehouse_id: form.to_warehouse_id,
      remark: form.remark,
      items: form.items.map((i) => ({
        product_id: i.product_id,
        quantity: i.quantity,
        from_location_id: i.from_location_id,
        to_location_id: i.to_location_id,
      })),
    })
    ElMessage.success('调拨单已创建（草稿），确认无误后点「发出」')
    dialogVisible.value = false
    load()
  } finally {
    submitting.value = false
  }
}

async function viewDetail(row) {
  current.value = await transferApi.detail(row.id)
  detailVisible.value = true
}

async function changeStatus(row, action, label) {
  await ElMessageBox.confirm(`确定要执行「${label}」吗？`, '操作确认', { type: 'warning' })
  const data = await transferApi.changeStatus(row.id, action)
  const alert = data?.auto_alert
  if (alert?.sent) {
    ElMessage.success(`操作成功，${alert.message}`)
  } else {
    ElMessage.success('操作成功')
  }
  load()
}

async function remove(row) {
  await ElMessageBox.confirm(`确定删除草稿调拨单 ${row.transfer_no} 吗？`, '删除确认', {
    type: 'warning',
  })
  await transferApi.remove(row.id)
  ElMessage.success('已删除')
  load()
}

function fmtTime(value) {
  return value ? value.replace('T', ' ').slice(0, 16) : ''
}

onMounted(async () => {
  load()
  warehouses.value = await warehouseApi.options()
})
</script>

<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h1 class="page-title">库存调拨</h1>
        <p class="page-subtitle">仓库之间的库位级调拨：发出扣减源仓，到达确认后进入目标仓</p>
      </div>
      <el-button v-perm="'transfer:manage'" type="primary" @click="openCreate">
        <el-icon><Plus /></el-icon>&nbsp;新建调拨单
      </el-button>
    </div>

    <div class="card">
      <div class="toolbar">
        <el-input
          v-model="query.keyword"
          placeholder="搜索调拨单号 / 仓库 / 备注"
          clearable
          style="width: 260px"
          @keyup.enter="search"
          @clear="search"
        />
        <el-select v-model="query.status" placeholder="全部状态" clearable style="width: 140px" @change="search">
          <el-option v-for="(v, k) in statusMap" :key="k" :label="v.text" :value="k" />
        </el-select>
        <el-button @click="search">查询</el-button>
        <div class="stat-line">
          在途：<b>{{ stats.in_transit_orders }}</b> 单 / <b>{{ stats.in_transit_qty }}</b> 件
        </div>
      </div>

      <el-table :data="list" v-loading="loading">
        <el-table-column prop="transfer_no" label="调拨单号" width="160" />
        <el-table-column prop="from_warehouse_name" label="调出仓库" min-width="120" show-overflow-tooltip />
        <el-table-column prop="to_warehouse_name" label="调入仓库" min-width="120" show-overflow-tooltip />
        <el-table-column label="数量" width="90" align="right">
          <template #default="{ row }">{{ row.total_qty }}</template>
        </el-table-column>
        <el-table-column label="状态" width="100" align="center">
          <template #default="{ row }">
            <el-tag :type="statusMap[row.status]?.type" effect="light" size="small">
              {{ row.status_label }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="created_by" label="申请人" width="100" />
        <el-table-column label="创建时间" width="150">
          <template #default="{ row }">{{ fmtTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column prop="remark" label="备注" min-width="140" show-overflow-tooltip />
        <el-table-column label="操作" width="260" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="viewDetail(row)">详情</el-button>
            <el-button
              v-if="row.status === 'draft'"
              v-perm="'transfer:manage'"
              link
              type="warning"
              @click="changeStatus(row, 'send', '发出调拨（源仓扣减）')"
            >发出</el-button>
            <el-button
              v-if="row.status === 'in_transit'"
              v-perm="'transfer:manage'"
              link
              type="success"
              @click="changeStatus(row, 'complete', '确认到达（目标仓入库）')"
            >确认到达</el-button>
            <el-button
              v-if="row.status === 'draft' || row.status === 'in_transit'"
              v-perm="'transfer:manage'"
              link
              type="info"
              @click="changeStatus(row, 'cancel', '取消调拨')"
            >取消</el-button>
            <el-button
              v-if="row.status === 'draft'"
              v-perm="'transfer:manage'"
              link
              type="danger"
              @click="remove(row)"
            >删除</el-button>
          </template>
        </el-table-column>
      </el-table>

      <div class="pager">
        <el-pagination
          v-model:current-page="page"
          :page-size="20"
          :total="total"
          layout="total, prev, pager, next"
          @current-change="load"
        />
      </div>
    </div>

    <el-dialog v-model="dialogVisible" title="新建调拨单" width="800px" top="6vh">
      <el-form :model="form" label-width="90px">
        <el-form-item label="调出仓库" required>
          <el-select v-model="form.from_warehouse_id" filterable placeholder="选择调出仓库" style="width: 100%" @change="onFromChange">
            <el-option v-for="w in activeWarehouses" :key="w.id" :label="`${w.name}（${w.code}）`" :value="w.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="调入仓库" required>
          <el-select v-model="form.to_warehouse_id" filterable placeholder="选择调入仓库" style="width: 100%" @change="onToChange">
            <el-option
              v-for="w in activeWarehouses.filter((x) => x.id !== form.from_warehouse_id)"
              :key="w.id"
              :label="`${w.name}（${w.code}）`"
              :value="w.id"
            />
          </el-select>
        </el-form-item>

        <el-form-item label="调拨明细">
          <div class="items-box">
            <div class="item-head">
              <span class="col-product">商品 / 源库位（现有）</span>
              <span class="col-num">数量</span>
              <span class="col-num">调入库位</span>
              <span class="col-op"></span>
            </div>
            <div class="empty-tip" v-if="!form.from_warehouse_id">请先选择调出仓库</div>
            <div v-else-if="!sourceStock.length" class="empty-tip">该仓库暂无可调拨库存</div>
            <div v-for="(item, index) in form.items" :key="index" class="item-row">
              <el-select
                v-model="item.key"
                filterable
                placeholder="选择商品与源库位"
                class="col-product"
                @change="onSourceSelect(item)"
              >
                <el-option
                  v-for="s in sourceStock"
                  :key="`${s.product_id}:${s.location_id}`"
                  :label="`${s.product_name} · ${s.location_code} 现有 ${s.quantity} ${s.unit}`"
                  :value="`${s.product_id}:${s.location_id}`"
                />
              </el-select>
              <el-input-number v-model="item.quantity" :min="1" :max="availableOf(item) || 1" class="col-num" />
              <el-select v-model="item.to_location_id" placeholder="调入库位" class="col-num">
                <el-option v-for="loc in toLocations" :key="loc.id" :label="`${loc.code} ${loc.name}`" :value="loc.id" />
              </el-select>
              <el-button class="col-op" link type="danger" :disabled="form.items.length === 1" @click="removeItem(index)">
                <el-icon><Delete /></el-icon>
              </el-button>
            </div>
            <el-button link type="primary" :disabled="!form.from_warehouse_id" @click="addItem">
              <el-icon><Plus /></el-icon>&nbsp;添加明细
            </el-button>
          </div>
        </el-form-item>

        <el-form-item label="备注"><el-input v-model="form.remark" type="textarea" :rows="2" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="submit">保存为草稿</el-button>
      </template>
    </el-dialog>

    <el-drawer v-model="detailVisible" title="调拨单详情" size="540px">
      <div v-if="current">
        <div class="detail-head">
          <div>
            <h3>{{ current.transfer_no }}</h3>
            <p>{{ current.from_warehouse_name }} → {{ current.to_warehouse_name }} · {{ fmtTime(current.created_at) }}</p>
          </div>
          <el-tag :type="statusMap[current.status]?.type" effect="light">{{ current.status_label }}</el-tag>
        </div>
        <el-table :data="current.items" size="small">
          <el-table-column prop="product_name" label="商品" min-width="130" show-overflow-tooltip />
          <el-table-column prop="from_location_code" label="源库位" width="80" align="center" />
          <el-table-column prop="to_location_code" label="目标库位" width="90" align="center" />
          <el-table-column label="数量" width="80" align="right">
            <template #default="{ row }">{{ row.quantity }} {{ row.unit }}</template>
          </el-table-column>
        </el-table>
        <div class="detail-total"><span>合计数量：<b>{{ current.total_qty }}</b></span></div>
        <p class="detail-remark">申请人：{{ current.created_by || '-' }}</p>
        <p class="detail-remark">发出时间：{{ fmtTime(current.sent_at) || '未发出' }}</p>
        <p class="detail-remark">到达时间：{{ fmtTime(current.completed_at) || '未到达' }}</p>
        <p class="detail-remark">备注：{{ current.remark || '无' }}</p>
      </div>
    </el-drawer>
  </div>
</template>

<style scoped>
.stat-line {
  margin-left: auto;
  color: var(--text-secondary);
  font-size: 13px;
}

.stat-line b {
  color: #f59e0b;
  font-size: 15px;
}

.items-box {
  width: 100%;
}

.item-head,
.item-row {
  display: flex;
  gap: 8px;
  align-items: center;
}

.item-head span {
  font-size: 12px;
  color: var(--text-secondary);
}

.item-row {
  margin-bottom: 8px;
}

.empty-tip {
  padding: 8px 0 12px;
  color: var(--text-secondary);
  font-size: 13px;
}

.col-product {
  flex: 1;
}

.col-num {
  width: 150px;
}

.col-op {
  width: 32px;
}

.detail-head {
  display: flex;
  justify-content: space-between;
  margin-bottom: 16px;
}

.detail-head h3 {
  margin: 0 0 6px;
}

.detail-head p {
  margin: 0;
  color: var(--text-secondary);
  font-size: 13px;
}

.detail-total {
  margin-top: 16px;
  padding-top: 12px;
  border-top: 1px solid var(--border);
  text-align: right;
}

.detail-total b {
  color: #f59e0b;
  font-size: 18px;
}

.detail-remark {
  margin-top: 10px;
  color: var(--text-secondary);
  font-size: 13px;
}
</style>
