<script setup>
import { onMounted, ref } from 'vue'
import { reconciliationApi } from '../api'

const loading = ref(false)
const data = ref({ receivable_total: 0, payable_total: 0, net: 0, receivables: [], payables: [] })
const tab = ref('receivable')

const money = (v) => Number(v || 0).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })

async function load() {
  loading.value = true
  try {
    data.value = await reconciliationApi.overview()
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
        <h1 class="page-title">对账总览</h1>
        <p class="page-subtitle">应收（客户欠款）与应付（欠供应商款）一站汇总，账龄一目了然，余额为负表示我方净应付。</p>
      </div>
      <div class="page-actions">
        <el-button @click="load">刷新</el-button>
      </div>
    </div>

    <el-row :gutter="16" style="margin-bottom: 16px">
      <el-col :span="8">
        <el-card shadow="never">
          <div class="stat-label">应收总额</div>
          <div class="stat-value up">¥{{ money(data.receivable_total) }}</div>
        </el-card>
      </el-col>
      <el-col :span="8">
        <el-card shadow="never">
          <div class="stat-label">应付总额</div>
          <div class="stat-value down">¥{{ money(data.payable_total) }}</div>
        </el-card>
      </el-col>
      <el-col :span="8">
        <el-card shadow="never">
          <div class="stat-label">净额（应收 − 应付）</div>
          <div class="stat-value" :class="{ up: data.net >= 0, down: data.net < 0 }">¥{{ money(data.net) }}</div>
        </el-card>
      </el-col>
    </el-row>

    <el-tabs v-model="tab">
      <el-tab-pane label="客户应收" name="receivable">
        <el-table :data="data.receivables" v-loading="loading" border>
          <el-table-column prop="customer" label="客户" min-width="160" />
          <el-table-column prop="level" label="等级" width="70" align="center" />
          <el-table-column label="应收余额" width="130" align="right">
            <template #default="{ row }">¥{{ money(row.balance) }}</template>
          </el-table-column>
          <el-table-column label="已收款" width="120" align="right">
            <template #default="{ row }">¥{{ money(row.paid) }}</template>
          </el-table-column>
          <el-table-column prop="orders" label="未结订单" width="90" align="center" />
          <el-table-column prop="age_days" label="账龄(天)" width="90" align="center" />
          <el-table-column prop="bucket" label="账龄区间" width="110" align="center" />
          <template #empty><el-empty description="暂无未结清的客户应收" /></template>
        </el-table>
      </el-tab-pane>
      <el-tab-pane label="供应商应付" name="payable">
        <el-table :data="data.payables" v-loading="loading" border>
          <el-table-column prop="supplier" label="供应商" min-width="160" />
          <el-table-column prop="category" label="品类" width="120" />
          <el-table-column label="应付余额" width="130" align="right">
            <template #default="{ row }">¥{{ money(row.balance) }}</template>
          </el-table-column>
          <el-table-column label="已付款" width="120" align="right">
            <template #default="{ row }">¥{{ money(row.paid) }}</template>
          </el-table-column>
          <el-table-column prop="orders" label="未结订单" width="90" align="center" />
          <el-table-column prop="age_days" label="账龄(天)" width="90" align="center" />
          <el-table-column prop="bucket" label="账龄区间" width="110" align="center" />
          <template #empty><el-empty description="暂无未结清的供应商应付" /></template>
        </el-table>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<style scoped>
.stat-label { color: #909399; font-size: 12px; }
.stat-value { font-size: 22px; font-weight: 600; margin-top: 6px; }
.stat-value.up { color: #22c55e; }
.stat-value.down { color: #f56c6c; }
</style>