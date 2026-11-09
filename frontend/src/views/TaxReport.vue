<script setup>
import { onMounted, ref } from 'vue'
import { taxApi } from '../api'

const period = ref(new Date().toISOString().slice(0, 7))
const report = ref({ sales_tax: 0, purchase_tax: 0, certified_purchase_tax: 0, tax_payable: 0 })
const detail = ref([])
const loading = ref(false)
const direction = ref('')

const money = (v) =>
  '¥' + Number(v || 0).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })

async function load() {
  loading.value = true
  try {
    const [r, d] = await Promise.all([
      taxApi.report(period.value),
      taxApi.detail({ period: period.value, direction: direction.value || undefined, page_size: 200 }),
    ])
    report.value = r
    detail.value = d.items || []
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
        <h1 class="page-title">税务报表</h1>
        <p class="page-subtitle">按发票登记的销项/进项税额汇总；应纳税额 = 销项 − 已认证可抵扣进项。价税分离后，总账同步生成「应交税费」科目分录。</p>
      </div>
      <div class="head-actions">
        <el-date-picker
          v-model="period"
          type="month"
          value-format="YYYY-MM"
          placeholder="选择期间"
          style="width: 140px"
          :clearable="false"
          @change="load"
        />
        <el-button @click="load"><el-icon><Refresh /></el-icon>&nbsp;刷新</el-button>
      </div>
    </div>

    <el-row :gutter="16" style="margin-bottom: 16px">
      <el-col :span="6">
        <el-card shadow="never">
          <div class="stat-label">销项税额</div>
          <div class="stat-value">{{ money(report.sales_tax) }}</div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="never">
          <div class="stat-label">进项税额</div>
          <div class="stat-value">{{ money(report.purchase_tax) }}</div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="never">
          <div class="stat-label">已认证可抵扣</div>
          <div class="stat-value">{{ money(report.certified_purchase_tax) }}</div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="never">
          <div class="stat-label">应纳税额</div>
          <div class="stat-value" :class="report.tax_payable >= 0 ? 'warn' : 'ok'">{{ money(report.tax_payable) }}</div>
        </el-card>
      </el-col>
    </el-row>

    <el-card shadow="never">
      <div class="panel-head">
        <h3>税额明细（{{ period }}）</h3>
        <el-radio-group v-model="direction" size="small" @change="load">
          <el-radio-button label="">全部</el-radio-button>
          <el-radio-button label="sales">销项</el-radio-button>
          <el-radio-button label="purchase">进项</el-radio-button>
        </el-radio-group>
      </div>
      <el-table v-loading="loading" :data="detail" size="small" border>
        <el-table-column prop="invoice_no" label="发票号码" width="150" />
        <el-table-column label="方向" width="80" align="center">
          <template #default="{ row }">
            <el-tag size="small" :type="row.direction === 'sales' ? 'success' : 'warning'" effect="plain">
              {{ row.direction === 'sales' ? '销项' : '进项' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="partner_name" label="往来方" min-width="140" />
        <el-table-column prop="issue_date" label="开票日期" width="110" />
        <el-table-column label="不含税金额" width="120" align="right">
          <template #default="{ row }">{{ money(row.amount) }}</template>
        </el-table-column>
        <el-table-column label="税率" width="80" align="center">
          <template #default="{ row }">{{ row.tax_rate }}%</template>
        </el-table-column>
        <el-table-column label="税额" width="110" align="right">
          <template #default="{ row }">{{ money(row.tax_amount) }}</template>
        </el-table-column>
        <el-table-column label="价税合计" width="120" align="right">
          <template #default="{ row }">{{ money(row.total_amount) }}</template>
        </el-table-column>
        <el-table-column label="认证" width="80" align="center">
          <template #default="{ row }">
            <el-tag v-if="row.certified" size="small" type="success">已认证</el-tag>
            <span v-else class="muted">未认证</span>
          </template>
        </el-table-column>
        <template #empty><el-empty description="该期间没有发票税额记录" /></template>
      </el-table>
    </el-card>
  </div>
</template>

<style scoped>
.head-actions {
  display: flex;
  align-items: center;
  gap: 10px;
}
.stat-label {
  color: #909399;
  font-size: 12px;
}
.stat-value {
  font-size: 22px;
  font-weight: 600;
  margin-top: 6px;
}
.stat-value.warn {
  color: #f56c6c;
}
.stat-value.ok {
  color: #22c55e;
}
.panel-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 10px;
}
.panel-head h3 {
  margin: 0;
  font-size: 15px;
}
.muted {
  color: #909399;
  font-size: 12px;
}
</style>
