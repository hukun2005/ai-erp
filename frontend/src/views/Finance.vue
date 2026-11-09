<script setup>
import { nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import * as echarts from 'echarts'
import { financeApi } from '../api'
import { useUserStore } from '../stores/user'

const userStore = useUserStore()
const days = ref(30)
const overview = ref({})
const trendData = ref([])
const receivables = ref({ buckets: [], items: [] })
const loading = ref(false)
const chartRef = ref()
let chart = null

async function loadAll() {
  loading.value = true
  try {
    const [ov, tr, rc] = await Promise.all([
      financeApi.overview(days.value),
      financeApi.trend(days.value),
      financeApi.receivables(),
    ])
    overview.value = ov
    trendData.value = tr
    receivables.value = rc
    await nextTick()
    renderChart()
  } finally {
    loading.value = false
  }
}

function renderChart() {
  if (!chartRef.value) return
  if (!chart) chart = echarts.init(chartRef.value)
  const data = trendData.value
  chart.setOption(
    {
      tooltip: { trigger: 'axis' },
      legend: { data: ['营收', '成本', '毛利'], top: 0, right: 10, itemWidth: 10, itemHeight: 10 },
      grid: { left: 10, right: 20, top: 30, bottom: 10, containLabel: true },
      xAxis: {
        type: 'category',
        data: data.map((d) => d.date.slice(5)),
        axisLabel: { color: '#9ca3af' },
        axisLine: { lineStyle: { color: '#e5e7eb' } },
      },
      yAxis: {
        type: 'value',
        splitLine: { lineStyle: { color: '#f1f5f9' } },
        axisLabel: { color: '#9ca3af' },
      },
      series: [
        { name: '营收', type: 'line', smooth: true, showSymbol: false, data: data.map((d) => d.revenue), lineStyle: { width: 2, color: '#6366f1' } },
        { name: '成本', type: 'line', smooth: true, showSymbol: false, data: data.map((d) => d.cost), lineStyle: { width: 2, color: '#f59e0b' } },
        {
          name: '毛利',
          type: 'line',
          smooth: true,
          showSymbol: false,
          data: data.map((d) => d.profit),
          lineStyle: { width: 2, color: '#10b981' },
          areaStyle: { color: 'rgba(16,185,129,0.10)' },
        },
      ],
    },
    true
  )
}

function onResize() {
  chart?.resize()
}

function money(value) {
  return '¥' + Number(value || 0).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function bucketTag(name) {
  return { '0-30天': 'success', '31-60天': 'warning', '60天以上': 'danger' }[name] || 'info'
}

onMounted(() => {
  loadAll()
  window.addEventListener('resize', onResize)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', onResize)
  chart?.dispose()
  chart = null
})
</script>

<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h1 class="page-title">财务分析</h1>
        <p class="page-subtitle">营收、成本、毛利与应收账龄（{{ userStore.roleName }}视角）</p>
      </div>
      <el-select v-model="days" style="width: 140px" @change="loadAll">
        <el-option label="近 7 天" :value="7" />
        <el-option label="近 30 天" :value="30" />
        <el-option label="近 90 天" :value="90" />
      </el-select>
    </div>

    <div v-loading="loading">
      <div class="metric-grid">
        <div class="metric-card">
          <span>营业收入</span>
          <strong>{{ money(overview.revenue) }}</strong>
          <p>{{ overview.order_count || 0 }} 笔订单 · 客单价 {{ money(overview.avg_order_value) }}</p>
        </div>
        <div class="metric-card">
          <span>销售成本</span>
          <strong>{{ money(overview.cost) }}</strong>
          <p>按商品成本价核算</p>
        </div>
        <div class="metric-card success">
          <span>毛利</span>
          <strong>{{ money(overview.gross_profit) }}</strong>
          <p>毛利率 {{ overview.gross_margin || 0 }}%</p>
        </div>
        <div class="metric-card warning">
          <span>待收账款</span>
          <strong>{{ money(overview.receivable) }}</strong>
          <p>已确认订单 − 退货 − 已收款</p>
        </div>
        <div class="metric-card payable">
          <span>待付账款</span>
          <strong>{{ money(overview.payable) }}</strong>
          <p>已入库采购 − 退货 − 已付款</p>
        </div>
      </div>

      <div class="metric-grid small">
        <div class="metric-card">
          <span>库存占用资金</span>
          <strong>{{ money(overview.inventory_value) }}</strong>
        </div>
        <div class="metric-card">
          <span>库存周转天数</span>
          <strong>{{ overview.inventory_turnover_days || 0 }} 天</strong>
        </div>
        <div class="metric-card">
          <span>统计周期</span>
          <strong>近 {{ overview.period_days || days }} 天</strong>
        </div>
      </div>

      <div class="card chart-card">
        <div class="panel-head">
          <h3>收入 / 成本 / 毛利趋势</h3>
          <el-button @click="loadAll"><el-icon><Refresh /></el-icon>&nbsp;刷新</el-button>
        </div>
        <div ref="chartRef" class="trend-chart"></div>
      </div>

      <div class="card receivables-card">
        <div class="panel-head">
          <h3>应收账龄分析</h3>
          <span class="hint">合计 {{ money(receivables.total) }}</span>
        </div>
        <div class="bucket-row">
          <div v-for="b in receivables.buckets" :key="b.name" class="bucket-item">
            <el-tag :type="bucketTag(b.name)" effect="light" size="small">{{ b.name }}</el-tag>
            <strong>{{ money(b.value) }}</strong>
          </div>
        </div>
        <el-table :data="receivables.items" size="small">
          <el-table-column prop="customer_name" label="客户" min-width="180" />
          <el-table-column prop="level" label="等级" width="80" align="center" />
          <el-table-column prop="orders" label="未结订单" width="100" align="center" />
          <el-table-column label="账龄" width="100" align="center">
            <template #default="{ row }">{{ row.age_days }} 天</template>
          </el-table-column>
          <el-table-column label="账龄区间" width="110" align="center">
            <template #default="{ row }">
              <el-tag :type="bucketTag(row.bucket)" size="small" effect="plain">{{ row.bucket }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="应收金额" width="130" align="right">
            <template #default="{ row }"><b>{{ money(row.amount) }}</b></template>
          </el-table-column>
        </el-table>
        <div v-if="!receivables.items.length" class="empty-text">当前没有未结清账款</div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.metric-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 14px;
  margin-bottom: 14px;
}

.metric-grid.small {
  grid-template-columns: repeat(3, minmax(0, 1fr));
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

.metric-card.success strong {
  color: var(--success);
}

.metric-card.warning strong {
  color: var(--warning);
}

.metric-card.payable strong {
  color: #dc2626;
}

.chart-card {
  margin-bottom: 16px;
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

.hint {
  font-size: 12px;
  color: var(--text-secondary);
}

.trend-chart {
  height: 320px;
}

.bucket-row {
  display: flex;
  gap: 24px;
  margin-bottom: 14px;
  flex-wrap: wrap;
}

.bucket-item {
  display: flex;
  align-items: center;
  gap: 8px;
}

.bucket-item strong {
  font-size: 16px;
}

@media (max-width: 1200px) {
  .metric-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
