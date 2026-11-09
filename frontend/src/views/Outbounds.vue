<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { outboundApi } from '../api'
import PrintDialog from '../components/PrintDialog.vue'
import ScanInput from '../components/ScanInput.vue'
import { useUserStore } from '../stores/user'

const userStore = useUserStore()

const loading = ref(false)
const list = ref([])
const total = ref(0)
const amountSum = ref(0)
const page = ref(1)
const pageSize = ref(20)
const query = reactive({ keyword: '', status: '', sort: 'created_desc' })
const dateRange = ref(null)
const busyId = ref(null)
const printVisible = ref(false)
const printIds = ref([])

function openPrint(ids) {
  const rows = (Array.isArray(ids) ? ids : [ids]).filter(Boolean)
  if (!rows.length) {
    ElMessage.warning('没有可打印的出库单')
    return
  }
  printIds.value = rows
  printVisible.value = true
}

const detailVisible = ref(false)
const detailLoading = ref(false)
const current = ref(null)

const statusMap = {
  draft: { text: '待发货', type: 'info' },
  pending: { text: '待审批', type: 'warning' },
  shipped: { text: '已出库', type: 'primary' },
  signed: { text: '已签收', type: 'success' },
  cancelled: { text: '已取消', type: 'danger' },
}

const statusOptions = Object.entries(statusMap).map(([value, item]) => ({ value, label: item.text }))
const sortOptions = [
  { value: 'created_desc', label: '创建时间（新→旧）' },
  { value: 'date_desc', label: '发货日期（新→旧）' },
  { value: 'date_asc', label: '发货日期（旧→新）' },
  { value: 'amount_desc', label: '金额（高→低）' },
  { value: 'amount_asc', label: '金额（低→高）' },
]

const canManage = computed(() => userStore.hasPerm('outbound:manage'))
const hasFilter = computed(
  () => !!query.keyword.trim() || !!query.status || !!dateRange.value?.length,
)

function money(value) {
  return Number(value || 0).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function formatTime(value) {
  return value ? String(value).replace('T', ' ').slice(0, 16) : '—'
}

async function load() {
  loading.value = true
  try {
    const data = await outboundApi.list({
      keyword: query.keyword,
      status: query.status,
      sort: query.sort,
      start_date: dateRange.value?.[0] || '',
      end_date: dateRange.value?.[1] || '',
      page: page.value,
      page_size: pageSize.value,
    })
    list.value = data.items
    total.value = data.total
    amountSum.value = data.amount_sum || 0
  } finally {
    loading.value = false
  }
}

function search() {
  page.value = 1
  load()
}

function resetQuery() {
  query.keyword = ''
  query.status = ''
  query.sort = 'created_desc'
  dateRange.value = null
  search()
}

async function openDetail(row) {
  detailVisible.value = true
  detailLoading.value = true
  current.value = null
  scanHit.value = null
  try {
    current.value = await outboundApi.get(row.id)
  } finally {
    detailLoading.value = false
  }
}

// 扫码出货：待发货时累加数量（受订单剩余可发量限制）；已出库时只做核对定位
const scanHit = ref(null)

function rowClass({ row }) {
  return scanHit.value && row.id === scanHit.value ? 'row-scan' : ''
}

async function onScan(product) {
  if (!current.value) return
  if (current.value.status !== 'draft') {
    const item = (current.value.items || []).find((it) => it.product_id === product.id)
    if (!item) {
      ElMessage.warning(`「${product.name}」不在本张出库单里`)
      scanHit.value = null
      return
    }
    scanHit.value = item.id
    ElMessage.success(`已定位「${product.name}」，应发 ${item.quantity} 件`)
    return
  }
  const data = await outboundApi.scan(current.value.id, product.barcode || product.sku)
  current.value = data
  scanHit.value = data.scanned_item_id
  const line = (data.items || []).find((it) => it.id === data.scanned_item_id)
  ElMessage.success(`扫码 +1：「${product.name}」本单累计 ${line?.quantity ?? ''} 件`)
  await load()
}

async function act(row, action, label) {
  const confirmText = {
    ship: `确认出库 ${row.outbound_no}？确认后将扣减库存。`,
    submit: `把 ${row.outbound_no} 提交审批？审批通过后自动出库。`,
    approve: `审批通过 ${row.outbound_no}？通过后将扣减库存并出库。`,
    reject: `驳回 ${row.outbound_no}？驳回后回到待发货状态。`,
    sign: `登记 ${row.outbound_no} 已签收？签收不重复扣库存。`,
    cancel: `取消 ${row.outbound_no}？已出库的货会自动回库。`,
  }[action]
  try {
    await ElMessageBox.confirm(confirmText, label, { type: 'warning' })
  } catch {
    return
  }
  busyId.value = row.id
  try {
    const result = await outboundApi.changeStatus(row.id, { action })
    ElMessage.success(
      action === 'approve' && result?.status === 'pending' ? '本级已通过，等待下一级审批' : `${label}成功`
    )
    await load()
    if (detailVisible.value && current.value?.id === row.id) {
      current.value = await outboundApi.get(row.id)
    }
  } finally {
    busyId.value = null
  }
}

async function removeRow(row) {
  try {
    await ElMessageBox.confirm(`确定删除待发货出库单 ${row.outbound_no} 吗？`, '删除确认', { type: 'warning' })
  } catch {
    return
  }
  await outboundApi.remove(row.id)
  ElMessage.success('已删除')
  load()
}

onMounted(load)
</script>

<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h1 class="page-title">销售出库</h1>
        <p class="page-subtitle">
          确认出库才扣库存，支持一张销售单分多次发货；签收只留交付凭证，不重复动库存
        </p>
      </div>
      <el-tag v-if="!canManage" size="small" type="info" effect="plain">仅查看</el-tag>
    </div>

    <div class="card">
      <div class="toolbar">
        <el-input
          v-model="query.keyword"
          placeholder="搜索出库单号 / 物流单号 / 客户"
          clearable
          style="width: 250px"
          @keyup.enter="search"
          @clear="search"
        />
        <el-select v-model="query.status" placeholder="全部状态" clearable style="width: 140px" @change="search">
          <el-option v-for="item in statusOptions" :key="item.value" :label="item.label" :value="item.value" />
        </el-select>
        <el-button @click="search">查询</el-button>
        <el-button v-if="hasFilter" text @click="resetQuery">重置</el-button>
        <el-button plain @click="openPrint(list.map((row) => row.id))">打印本页 PDF</el-button>
        <span class="toolbar-count">共 {{ total }} 张 · 合计 ¥{{ money(amountSum) }}</span>
      </div>

      <div class="filter-bar">
        <el-date-picker
          v-model="dateRange"
          type="daterange"
          value-format="YYYY-MM-DD"
          start-placeholder="创建开始日"
          end-placeholder="创建结束日"
          style="width: 250px"
          @change="search"
        />
        <el-select v-model="query.sort" style="width: 176px" @change="search">
          <el-option v-for="option in sortOptions" :key="option.value" :label="option.label" :value="option.value" />
        </el-select>
      </div>

      <el-table :data="list" v-loading="loading" row-key="id">
        <el-table-column label="出库单号" width="170">
          <template #default="{ row }">
            <button type="button" class="order-link" @click="openDetail(row)">{{ row.outbound_no }}</button>
          </template>
        </el-table-column>
        <el-table-column prop="sales_order_no" label="销售订单" width="150" />
        <el-table-column prop="customer_name" label="客户" min-width="150" show-overflow-tooltip />
        <el-table-column label="数量" width="80" align="right">
          <template #default="{ row }">{{ row.total_quantity }}</template>
        </el-table-column>
        <el-table-column label="金额" width="120" align="right">
          <template #default="{ row }"><b>¥{{ money(row.total_amount) }}</b></template>
        </el-table-column>
        <el-table-column label="物流" min-width="170" show-overflow-tooltip>
          <template #default="{ row }">
            <span v-if="row.logistics_no">{{ row.logistics_company }} {{ row.logistics_no }}</span>
            <span v-else class="empty-text">未填写</span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="100" align="center">
          <template #default="{ row }">
            <el-tag :type="statusMap[row.status]?.type" effect="light" size="small">
              {{ statusMap[row.status]?.text || row.status }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="发货时间" width="150">
          <template #default="{ row }">{{ formatTime(row.shipped_at || row.ship_date) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="300" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="openDetail(row)">详情</el-button>
            <el-button link @click="openPrint(row.id)">打印</el-button>
            <template v-if="canManage">
              <el-button
                v-if="row.status === 'draft'"
                link
                type="success"
                :loading="busyId === row.id"
                @click="act(row, 'ship', '确认出库')"
              >出库</el-button>
              <el-button
                v-if="row.status === 'draft'"
                link
                type="warning"
                :loading="busyId === row.id"
                @click="act(row, 'submit', '提交审批')"
              >提交审批</el-button>
              <template v-if="row.status === 'pending'">
                <el-button
                  link
                  type="success"
                  :loading="busyId === row.id"
                  @click="act(row, 'approve', '审批通过')"
                >通过</el-button>
                <el-button
                  link
                  type="danger"
                  :loading="busyId === row.id"
                  @click="act(row, 'reject', '审批驳回')"
                >驳回</el-button>
              </template>
              <el-button
                v-if="row.status === 'shipped'"
                link
                type="success"
                :loading="busyId === row.id"
                @click="act(row, 'sign', '登记签收')"
              >签收</el-button>
              <el-button
                v-if="['draft', 'pending', 'shipped'].includes(row.status)"
                link
                type="warning"
                :loading="busyId === row.id"
                @click="act(row, 'cancel', '取消出库')"
              >取消</el-button>
              <el-button v-if="row.status === 'draft'" link type="danger" @click="removeRow(row)">删除</el-button>
            </template>
          </template>
        </el-table-column>
        <template #empty>
          <p class="empty-text">
            还没有出库单。到「销售订单」确认订单后，点详情里的「生成出库单」即可发货。
          </p>
        </template>
      </el-table>

      <div class="pager">
        <el-pagination
          v-model:current-page="page"
          :page-size="pageSize"
          :total="total"
          layout="prev, pager, next"
          @current-change="load"
        />
      </div>
    </div>

    <el-drawer v-model="detailVisible" title="出库单详情" size="620px">
      <div v-loading="detailLoading">
        <template v-if="current">
          <el-descriptions :column="2" border size="small">
            <el-descriptions-item label="出库单号">{{ current.outbound_no }}</el-descriptions-item>
            <el-descriptions-item label="销售订单">{{ current.sales_order_no }}</el-descriptions-item>
            <el-descriptions-item label="客户">{{ current.customer_name }}</el-descriptions-item>
            <el-descriptions-item label="仓库/库位">
              {{ current.warehouse_name }}{{ current.location_code ? ` / ${current.location_code}` : '' }}
            </el-descriptions-item>
            <el-descriptions-item label="状态">
              <el-tag :type="statusMap[current.status]?.type" size="small" effect="light">
                {{ statusMap[current.status]?.text || current.status }}
              </el-tag>
            </el-descriptions-item>
            <el-descriptions-item label="制单">{{ current.created_by }}</el-descriptions-item>
            <el-descriptions-item label="物流公司">{{ current.logistics_company || '—' }}</el-descriptions-item>
            <el-descriptions-item label="物流单号">{{ current.logistics_no || '—' }}</el-descriptions-item>
            <el-descriptions-item label="发货时间">{{ formatTime(current.shipped_at) }}</el-descriptions-item>
            <el-descriptions-item label="签收">
              {{ current.signed_by || '—' }} {{ current.signed_at ? formatTime(current.signed_at) : '' }}
            </el-descriptions-item>
          </el-descriptions>

          <h4 class="detail-title">
            出库明细
            <ScanInput
              v-if="current.status !== 'cancelled'"
              :placeholder="current.status === 'draft' ? '扫码出货：扫一次 +1 件' : '扫码核对出库商品'"
              style="width: 240px; margin-left: 10px"
              @select="onScan"
            />
          </h4>
          <el-table :data="current.items" size="small" border :row-class-name="rowClass">
            <el-table-column prop="product_name" label="商品" min-width="150" />
            <el-table-column prop="product_sku" label="SKU" width="110" />
            <el-table-column label="数量" width="80" align="right">
              <template #default="{ row }">{{ row.quantity }}</template>
            </el-table-column>
            <el-table-column label="单价" width="100" align="right">
              <template #default="{ row }">¥{{ money(row.unit_price) }}</template>
            </el-table-column>
            <el-table-column label="金额" width="110" align="right">
              <template #default="{ row }">¥{{ money(row.amount) }}</template>
            </el-table-column>
          </el-table>

          <p class="detail-remark">备注：{{ current.remark || '—' }}</p>
        </template>
      </div>
    </el-drawer>

    <PrintDialog v-model="printVisible" doc-type="outbound" :ids="printIds" title="打印销售出库单" />
  </div>
</template>

<style scoped>
.order-link {
  border: none;
  background: none;
  padding: 0;
  font: inherit;
  font-weight: 600;
  color: var(--brand);
  cursor: pointer;
}

.pager {
  display: flex;
  justify-content: flex-end;
  margin-top: 12px;
}

.detail-title {
  margin: 18px 0 10px;
  font-size: 14px;
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

:deep(.row-scan) {
  background: #eff6ff;
  outline: 1px solid #93c5fd;
}

.detail-remark {
  margin-top: 14px;
  color: var(--text-secondary);
  font-size: 13px;
}
</style>


