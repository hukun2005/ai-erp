<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  approvalApi,
  outboundApi,
  payablesApi,
  purchaseApi,
  receivablesApi,
  requisitionApi,
  returnsApi,
  salesApi,
  stocktakeApi,
} from '../api'

const loading = ref(false)
const list = ref([])
const query = reactive({ type: '' })
const page = ref(1)
const pageSize = ref(20)
const total = ref(0)
const stats = ref(null)

const actionVisible = ref(false)
const submitting = ref(false)
const form = reactive({ row: null, action: 'approve', comment: '' })

const logsVisible = ref(false)
const logs = ref([])
const logTitle = ref('')

const typeMap = {
  sales: { text: '销售订单', type: 'primary' },
  purchase: { text: '采购订单', type: 'warning' },
  requisition: { text: '采购申请', type: 'success' },
  outbound: { text: '销售出库', type: 'primary' },
  sales_return: { text: '销售退货', type: 'danger' },
  purchase_return: { text: '采购退货', type: 'danger' },
  payment: { text: '收款单', type: 'success' },
  supplier_payment: { text: '付款单', type: 'warning' },
  stocktake: { text: '盘点单', type: 'info' },
}

// 每种单据走各自的审批接口：统一成 changeStatus(id, action, comment) 的形式
const ORDER_APIS = {
  sales: { changeStatus: (id, action, comment) => salesApi.changeStatus(id, action, comment) },
  purchase: { changeStatus: (id, action, comment) => purchaseApi.changeStatus(id, action, comment) },
  requisition: { changeStatus: (id, action, comment) => requisitionApi.changeStatus(id, action, comment) },
  outbound: { changeStatus: (id, action, comment) => outboundApi.changeStatus(id, { action, comment }) },
  sales_return: { changeStatus: (id, action, comment) => returnsApi.salesStatus(id, action, comment) },
  purchase_return: { changeStatus: (id, action, comment) => returnsApi.purchaseStatus(id, action, comment) },
  payment: {
    changeStatus: (id, action, comment) =>
      action === 'approve' ? receivablesApi.approvePayment(id, comment) : receivablesApi.rejectPayment(id, comment),
  },
  supplier_payment: {
    changeStatus: (id, action, comment) =>
      action === 'approve' ? payablesApi.approvePayment(id, comment) : payablesApi.rejectPayment(id, comment),
  },
  stocktake: { changeStatus: (id, action, comment) => stocktakeApi.changeStatus(id, action, comment) },
}

async function load() {
  loading.value = true
  try {
    const data = await approvalApi.pending({ type: query.type, page: page.value, page_size: pageSize.value })
    list.value = data.items
    total.value = data.total
    stats.value = data.stats
  } finally {
    loading.value = false
  }
}

function onTypeChange() {
  page.value = 1
  load()
}

function money(value) {
  return '¥' + Number(value || 0).toLocaleString('zh-CN', { minimumFractionDigits: 2 })
}

function datetime(value) {
  return String(value || '').replace('T', ' ').slice(0, 16)
}

function date(value) {
  return String(value || '').slice(0, 10)
}

function openAction(row, action) {
  form.row = row
  form.action = action
  form.comment = ''
  actionVisible.value = true
}

async function submitAction() {
  if (form.action === 'reject' && !form.comment.trim()) {
    ElMessage.warning('驳回时请填写审批意见')
    return
  }
  submitting.value = true
  try {
    const api = ORDER_APIS[form.row.order_type]
    if (!api) {
      ElMessage.warning('该单据类型暂不支持在这里审批')
      return
    }
    const result = await api.changeStatus(form.row.order_id, form.action, form.comment)
    if (form.action === 'approve') {
      // 会签未齐或还有下一级时，单据仍处于待审批
      ElMessage.success(result?.status === 'pending' ? '本级已通过，等待下一级审批' : '已审批通过')
    } else {
      ElMessage.success('已驳回')
    }
    actionVisible.value = false
    load()
  } finally {
    submitting.value = false
  }
}

async function viewLogs(row) {
  const data = await approvalApi.logs(row.order_type, row.order_id)
  logs.value = data.items || []
  logTitle.value = `${row.order_no} 审批轨迹`
  logsVisible.value = true
}

const ACTION_TEXT = { submit: '提交', approve: '通过', reject: '驳回' }

onMounted(load)
</script>

<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h1 class="page-title">待办审批</h1>
        <p class="page-subtitle">销售订单、采购订单、采购申请提交后进入待审批，按规则逐级审批；支持会签/或签与委托代理，驳回会退回并留下意见。</p>
      </div>
      <el-button :loading="loading" @click="load"><el-icon><Refresh /></el-icon>&nbsp;刷新</el-button>
    </div>

    <div class="cards">
      <div class="card stat">
        <span>待审批总数</span>
        <b>{{ total }}</b>
      </div>
      <div class="card stat">
        <span>销售待审批</span>
        <b>{{ stats?.sales || 0 }}</b>
      </div>
      <div class="card stat">
        <span>采购待审批</span>
        <b>{{ stats?.purchase || 0 }}</b>
      </div>
      <div class="card stat">
        <span>采购申请待审批</span>
        <b>{{ stats?.requisition || 0 }}</b>
      </div>
      <div class="card stat">
        <span>超时待办</span>
        <b class="overdue">{{ stats?.overdue || 0 }}</b>
      </div>
    </div>

    <div class="card">
      <div class="toolbar">
        <el-radio-group v-model="query.type" size="small" @change="onTypeChange">
          <el-radio-button value="">全部</el-radio-button>
          <el-radio-button value="sales">销售</el-radio-button>
          <el-radio-button value="purchase">采购</el-radio-button>
          <el-radio-button value="requisition">采购申请</el-radio-button>
          <el-radio-button value="outbound">出库</el-radio-button>
          <el-radio-button value="sales_return">销售退货</el-radio-button>
          <el-radio-button value="purchase_return">采购退货</el-radio-button>
          <el-radio-button value="payment">收款</el-radio-button>
          <el-radio-button value="supplier_payment">付款</el-radio-button>
          <el-radio-button value="stocktake">盘点</el-radio-button>
        </el-radio-group>
        <span class="hint">审批通过销售订单会立即扣减库存，请先确认库存充足</span>
      </div>

      <el-table :data="list" v-loading="loading">
        <el-table-column label="类型" width="100" align="center">
          <template #default="{ row }">
            <el-tag :type="typeMap[row.order_type]?.type" effect="light" size="small">
              {{ typeMap[row.order_type]?.text }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="order_no" label="单号" width="180" />
        <el-table-column prop="partner_name" label="客户 / 供应商" min-width="160" show-overflow-tooltip />
        <el-table-column label="金额" width="130" align="right">
          <template #default="{ row }"><b class="money">{{ money(row.total_amount) }}</b></template>
        </el-table-column>
        <el-table-column prop="item_summary" label="商品摘要" min-width="220" show-overflow-tooltip />
        <el-table-column label="审批进度" width="110" align="center">
          <template #default="{ row }">
            <el-tag v-if="row.approval_required > 1" size="small" type="warning" effect="light">
              第 {{ row.current_level }}/{{ row.approval_required }} 级
            </el-tag>
            <span v-else class="empty-text">单级</span>
          </template>
        </el-table-column>
        <el-table-column label="已等待" width="150" align="center">
          <template #default="{ row }">
            <el-tag v-if="row.escalated" size="small" type="danger" effect="dark">
              已升级 {{ row.waiting_hours }}h
            </el-tag>
            <el-tag v-else-if="row.overdue" size="small" type="danger" effect="light">
              超时 {{ row.waiting_hours }}h
            </el-tag>
            <span v-else class="empty-text">{{ row.waiting_hours ?? 0 }}h</span>
          </template>
        </el-table-column>
        <el-table-column prop="created_by" label="提交人" width="100" />
        <el-table-column label="订单日期" width="150">
          <template #default="{ row }">{{ date(row.order_date) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="190" fixed="right">
          <template #default="{ row }">
            <el-button link type="success" @click="openAction(row, 'approve')">通过</el-button>
            <el-button link type="danger" @click="openAction(row, 'reject')">驳回</el-button>
            <el-button link type="primary" @click="viewLogs(row)">轨迹</el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-pagination
        v-if="total > pageSize"
        v-model:current-page="page"
        :page-size="pageSize"
        :total="total"
        layout="total, prev, pager, next"
        class="pager"
        @current-change="load"
      />
      <div v-if="!list.length && !loading" class="empty-text">暂无待审批订单</div>
    </div>

    <el-dialog
      v-model="actionVisible"
      :title="`${form.action === 'approve' ? '审批通过' : '审批驳回'} · ${form.row?.order_no || ''}`"
      width="520px"
    >
      <el-form label-width="90px">
        <el-form-item label="订单金额">
          <b class="money">{{ money(form.row?.total_amount) }}</b>
        </el-form-item>
        <el-form-item label="审批意见">
          <el-input
            v-model="form.comment"
            type="textarea"
            :rows="3"
            :placeholder="form.action === 'reject' ? '请说明驳回原因（必填）' : '选填'"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="actionVisible = false">取消</el-button>
        <el-button :type="form.action === 'approve' ? 'success' : 'danger'" :loading="submitting" @click="submitAction">
          确认{{ form.action === 'approve' ? '通过' : '驳回' }}
        </el-button>
      </template>
    </el-dialog>

    <el-drawer v-model="logsVisible" :title="logTitle" size="460px">
      <el-timeline v-if="logs.length">
        <el-timeline-item
          v-for="log in logs"
          :key="log.id"
          :timestamp="datetime(log.created_at)"
          :type="log.action === 'approve' ? 'success' : log.action === 'reject' ? 'danger' : 'primary'"
        >
          <b>{{ ACTION_TEXT[log.action] || log.action }}</b> · {{ log.operator }}（{{ log.role }}）
          <p v-if="log.comment" class="log-comment">{{ log.comment }}</p>
        </el-timeline-item>
      </el-timeline>
      <div v-else class="empty-text">暂无审批记录</div>
    </el-drawer>
  </div>
</template>

<style scoped>
.cards {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: 12px;
  margin-bottom: 14px;
}

.stat {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.stat span {
  font-size: 12px;
  color: var(--text-secondary);
}

.stat b {
  font-size: 19px;
}

.hint {
  font-size: 12px;
  color: var(--text-secondary);
}

.toolbar {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 10px;
}

.log-comment {
  margin: 6px 0 0;
  font-size: 12px;
  color: var(--text-secondary);
}

.empty-text {
  padding: 18px 0;
  text-align: center;
  color: var(--text-secondary);
  font-size: 13px;
}

.overdue {
  color: #dc2626;
}
</style>
