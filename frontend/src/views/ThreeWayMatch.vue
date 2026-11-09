<script setup>
import { onMounted, ref } from 'vue'
import { invoiceApi } from '../api'

const loading = ref(false)
const data = ref({ total: 0, matched: 0, warn: 0, items: [] })

function fmtMoney(v) {
  return Number(v || 0).toLocaleString('zh-CN', { maximumFractionDigits: 2 })
}

async function load() {
  loading.value = true
  try {
    data.value = await invoiceApi.threeWay()
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
        <h1 class="page-title">发票三单匹配</h1>
        <p class="page-subtitle">采购订单 ↔ 入库单 ↔ 进项发票自动比对，数量、单价、金额、税额差异与缺单一键预警。</p>
      </div>
      <el-button type="primary" plain :loading="loading" @click="load">
        <el-icon><Refresh /></el-icon>&nbsp;刷新
      </el-button>
    </div>

    <div class="stat-row">
      <div class="stat"><div class="stat-num">{{ data.total }}</div><div class="stat-label">采购单</div></div>
      <div class="stat ok"><div class="stat-num">{{ data.matched }}</div><div class="stat-label">三单一致</div></div>
      <div class="stat warn"><div class="stat-num">{{ data.warn }}</div><div class="stat-label">存在差异</div></div>
    </div>

    <div class="card">
      <el-table :data="data.items" v-loading="loading" stripe>
        <el-table-column prop="order_no" label="采购单号" min-width="140" fixed />
        <el-table-column prop="supplier" label="供应商" min-width="120" />
        <el-table-column label="采购数量" width="90" align="center">
          <template #default="{ row }">{{ row.po_qty }}</template>
        </el-table-column>
        <el-table-column label="入库数量" width="90" align="center">
          <template #default="{ row }">{{ row.inbound_qty }}</template>
        </el-table-column>
        <el-table-column label="采购金额" width="110" align="right">
          <template #default="{ row }">¥{{ fmtMoney(row.po_amount) }}</template>
        </el-table-column>
        <el-table-column label="入库金额" width="110" align="right">
          <template #default="{ row }">¥{{ fmtMoney(row.inbound_amount) }}</template>
        </el-table-column>
        <el-table-column label="发票金额" width="110" align="right">
          <template #default="{ row }">¥{{ fmtMoney(row.invoice_amount) }}</template>
        </el-table-column>
        <el-table-column label="发票税额" width="100" align="right">
          <template #default="{ row }">¥{{ fmtMoney(row.invoice_tax) }}</template>
        </el-table-column>
        <el-table-column prop="inbound_no" label="入库单" min-width="120" show-overflow-tooltip />
        <el-table-column prop="invoice_no" label="进项发票" min-width="130" show-overflow-tooltip />
        <el-table-column label="差异" min-width="200">
          <template #default="{ row }">
            <div v-if="row.diffs.length" class="diffs">
              <div v-for="(d, i) in row.diffs" :key="i" class="diff-item">
                <el-tag type="danger" size="small" effect="plain">{{ d.field }}</el-tag>
                <span class="diff-detail">{{ d.detail }}</span>
              </div>
            </div>
            <el-tag v-else type="success" size="small" effect="plain">一致</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="90" align="center">
          <template #default="{ row }">
            <el-tag :type="row.matched ? 'success' : 'warning'" size="small">
              {{ row.matched ? '通过' : '预警' }}
            </el-tag>
          </template>
        </el-table-column>
      </el-table>

      <div v-if="!data.items.length && !loading" class="empty-text">暂无在途采购单</div>
    </div>
  </div>
</template>

<style scoped>
.stat-row {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 12px;
  margin-bottom: 14px;
}

.stat {
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 14px 16px;
}

.stat-num {
  font-size: 24px;
  font-weight: 700;
  color: var(--brand);
}

.stat.ok .stat-num {
  color: #16a34a;
}

.stat.warn .stat-num {
  color: #f59e0b;
}

.stat-label {
  margin-top: 2px;
  font-size: 12.5px;
  color: var(--text-secondary);
}

.diffs {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.diff-item {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  line-height: 1.5;
}

.diff-detail {
  font-size: 12px;
  color: var(--text-secondary);
}
</style>