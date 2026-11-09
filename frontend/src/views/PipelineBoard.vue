<script setup>
import { computed, onMounted, ref } from 'vue'
import { pipelineApi } from '../api'

const loading = ref(false)
const total = ref(0)
const stuck = ref({ order: 0, outbound: 0, invoice: 0, receipt: 0 })
const items = ref([])

const STATUS_TEXT = {
  order: '订单',
  outbound: '出库',
  invoice: '开票',
  receipt: '收款',
}

const STAGE_CLASS = { done: 'done', partial: 'partial', pending: 'pending', none: 'none' }

const stuckCount = computed(() => stuck.value.order + stuck.value.outbound + stuck.value.invoice + stuck.value.receipt)

function fmtMoney(v) {
  return Number(v || 0).toLocaleString('zh-CN', { maximumFractionDigits: 0 })
}

async function load() {
  loading.value = true
  try {
    const data = await pipelineApi.salesBoard()
    total.value = data.total || 0
    stuck.value = data.stuck || { order: 0, outbound: 0, invoice: 0, receipt: 0 }
    items.value = data.items || []
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h1 class="page-title">报价 → 收款 全链路看板</h1>
        <p class="page-subtitle">每张销售订单的报价、下单、出库、开票、收款进度一览，快速定位卡在哪一环。</p>
      </div>
      <el-button type="primary" plain :loading="loading" @click="load">
        <el-icon><Refresh /></el-icon>&nbsp;刷新
      </el-button>
    </div>

    <div class="stat-row">
      <div class="stat">
        <div class="stat-num">{{ total }}</div>
        <div class="stat-label">待跟进订单</div>
      </div>
      <div class="stat warn">
        <div class="stat-num">{{ stuck.order }}</div>
        <div class="stat-label">卡在订单</div>
      </div>
      <div class="stat warn">
        <div class="stat-num">{{ stuck.outbound }}</div>
        <div class="stat-label">卡在出库</div>
      </div>
      <div class="stat warn">
        <div class="stat-num">{{ stuck.invoice }}</div>
        <div class="stat-label">卡在开票</div>
      </div>
      <div class="stat warn">
        <div class="stat-num">{{ stuck.receipt }}</div>
        <div class="stat-label">卡在收款</div>
      </div>
    </div>

    <div class="card">
      <el-table :data="items" v-loading="loading" stripe>
        <el-table-column prop="order_no" label="订单号" min-width="150" fixed />
        <el-table-column prop="customer" label="客户" min-width="120" />
        <el-table-column label="金额" width="110" align="right">
          <template #default="{ row }">¥{{ fmtMoney(row.order_amount) }}</template>
        </el-table-column>
        <el-table-column label="链路进度" min-width="380">
          <template #default="{ row }">
            <div class="steps">
              <div
                v-for="s in row.stages"
                :key="s.key"
                class="step"
                :class="STAGE_CLASS[s.status]"
                :title="s.text"
              >
                <span class="dot"></span>
                <span class="step-label">{{ s.label }}</span>
              </div>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="出库" width="90" align="center">
          <template #default="{ row }">{{ row.shipped_qty }}/{{ row.ordered_qty }}</template>
        </el-table-column>
        <el-table-column label="已开票" width="110" align="right">
          <template #default="{ row }">¥{{ fmtMoney(row.invoice_amount) }}</template>
        </el-table-column>
        <el-table-column label="已收款" width="110" align="right">
          <template #default="{ row }">¥{{ fmtMoney(row.paid_amount) }}</template>
        </el-table-column>
        <el-table-column label="瓶颈" width="110" align="center">
          <template #default="{ row }">
            <el-tag v-if="row.bottleneck" type="warning" size="small" effect="plain">{{ row.bottleneck }}</el-tag>
            <el-tag v-else type="success" size="small" effect="plain">已走完</el-tag>
          </template>
        </el-table-column>
      </el-table>

      <div v-if="!items.length && !loading" class="empty-text">暂无待跟进订单</div>
    </div>
  </div>
</template>

<style scoped>
.stat-row {
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: 12px;
  margin-bottom: 14px;
}

.stat {
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 14px 16px;
}

.stat.warn .stat-num {
  color: #f59e0b;
}

.stat-num {
  font-size: 24px;
  font-weight: 700;
  color: var(--brand);
}

.stat-label {
  margin-top: 2px;
  font-size: 12.5px;
  color: var(--text-secondary);
}

.steps {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.step {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 12px;
  color: var(--text-secondary);
}

.step .dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: #cbd5e1;
}

.step.done .dot {
  background: #22c55e;
}

.step.partial .dot {
  background: #f59e0b;
}

.step.pending .dot {
  background: #94a3b8;
}

.step.none .dot {
  background: #e2e8f0;
  border: 1px dashed #cbd5e1;
}

.step.done {
  color: #16a34a;
  font-weight: 600;
}
</style>