<script setup>
import { onMounted, reactive, ref } from 'vue'
import { reportApi } from '../api'

const loading = ref(false)
const tab = ref('sales')
const range = ref([])
const overview = ref(null)
const sales = ref({ by_customer: [], by_product: [] })
const purchase = ref({ by_supplier: [], by_product: [] })
const production = ref({ totals: {}, rows: [] })

function defaultRange() {
  const end = new Date()
  const start = new Date()
  start.setDate(end.getDate() - 29)
  const fmt = (d) => d.toISOString().slice(0, 10)
  return [fmt(start), fmt(end)]
}

function params() {
  return { start: range.value?.[0] || '', end: range.value?.[1] || '' }
}

async function load() {
  loading.value = true
  const p = params()
  try {
    const [o, s, pu, pr] = await Promise.all([
      reportApi.overview(p),
      reportApi.sales(p),
      reportApi.purchase(p),
      reportApi.production(p),
    ])
    overview.value = o
    sales.value = s
    purchase.value = pu
    production.value = pr
  } finally {
    loading.value = false
  }
}

function money(value) {
  return '¥' + Number(value || 0).toLocaleString('zh-CN', { minimumFractionDigits: 2 })
}

function shortTime(value) {
  return value ? String(value).replace('T', ' ').slice(0, 16) : '—'
}

const prodStatus = {
  draft: '草稿',
  released: '已下达',
  in_progress: '生产中',
  completed: '已完工',
  cancelled: '已取消',
}

onMounted(() => {
  range.value = defaultRange()
  load()
})
</script>

<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h1 class="page-title">报表中心</h1>
        <p class="page-subtitle">综合报表、销售报表、采购报表与生产报表，按时间区间统一汇总</p>
      </div>
      <div class="header-filter">
        <el-date-picker
          v-model="range"
          type="daterange"
          value-format="YYYY-MM-DD"
          range-separator="至"
          start-placeholder="开始日期"
          end-placeholder="结束日期"
          style="width: 260px"
        />
        <el-button type="primary" :loading="loading" @click="load">查询</el-button>
      </div>
    </div>

    <div v-if="overview" class="summary-grid">
      <div class="summary-card">
        <span class="label">销售额</span>
        <b class="value">{{ money(overview.sales.amount) }}</b>
        <span class="extra">{{ overview.sales.order_count }} 张订单</span>
      </div>
      <div class="summary-card">
        <span class="label">采购额</span>
        <b class="value">{{ money(overview.purchase.amount) }}</b>
        <span class="extra">{{ overview.purchase.order_count }} 张订单</span>
      </div>
      <div class="summary-card">
        <span class="label">库存资金占用</span>
        <b class="value">{{ money(overview.inventory_value) }}</b>
        <span class="extra">入库 {{ overview.stock.in_qty }} / 出库 {{ overview.stock.out_qty }}</span>
      </div>
      <div class="summary-card">
        <span class="label">应收账款</span>
        <b class="value">{{ money(overview.receivable_total) }}</b>
        <span class="extra">未结金额</span>
      </div>
      <div class="summary-card">
        <span class="label">应付账款</span>
        <b class="value">{{ money(overview.payable_total) }}</b>
        <span class="extra">未付金额</span>
      </div>
      <div class="summary-card">
        <span class="label">生产完工</span>
        <b class="value">{{ overview.production.completed_qty }}</b>
        <span class="extra">{{ overview.production.order_count }} 张生产单 · 计划 {{ overview.production.plan_qty }}</span>
      </div>
    </div>

    <div class="card">
      <el-tabs v-model="tab">
        <el-tab-pane label="销售报表" name="sales">
          <h4 class="section-title">客户维度</h4>
          <el-table :data="sales.by_customer" size="small" v-loading="loading">
            <el-table-column prop="customer_name" label="客户" min-width="160" show-overflow-tooltip />
            <el-table-column prop="order_count" label="订单数" width="90" align="center" />
            <el-table-column label="销售金额" width="140" align="right">
              <template #default="{ row }">{{ money(row.amount) }}</template>
            </el-table-column>
          </el-table>

          <h4 class="section-title">商品维度（含毛利）</h4>
          <el-table :data="sales.by_product" size="small" v-loading="loading">
            <el-table-column prop="product_name" label="商品" min-width="160" show-overflow-tooltip />
            <el-table-column prop="sku" label="SKU" width="110" />
            <el-table-column prop="quantity" label="销量" width="80" align="right" />
            <el-table-column label="销售额" width="130" align="right">
              <template #default="{ row }">{{ money(row.amount) }}</template>
            </el-table-column>
            <el-table-column label="成本" width="120" align="right">
              <template #default="{ row }">{{ money(row.cost) }}</template>
            </el-table-column>
            <el-table-column label="毛利" width="120" align="right">
              <template #default="{ row }">
                <b :class="row.gross_profit >= 0 ? 'profit' : 'loss'">{{ money(row.gross_profit) }}</b>
              </template>
            </el-table-column>
          </el-table>
        </el-tab-pane>

        <el-tab-pane label="采购报表" name="purchase">
          <h4 class="section-title">供应商维度</h4>
          <el-table :data="purchase.by_supplier" size="small" v-loading="loading">
            <el-table-column prop="supplier_name" label="供应商" min-width="160" show-overflow-tooltip />
            <el-table-column prop="order_count" label="订单数" width="90" align="center" />
            <el-table-column label="采购金额" width="140" align="right">
              <template #default="{ row }">{{ money(row.amount) }}</template>
            </el-table-column>
          </el-table>

          <h4 class="section-title">商品维度</h4>
          <el-table :data="purchase.by_product" size="small" v-loading="loading">
            <el-table-column prop="product_name" label="商品" min-width="160" show-overflow-tooltip />
            <el-table-column prop="sku" label="SKU" width="110" />
            <el-table-column prop="quantity" label="采购量" width="90" align="right" />
            <el-table-column label="采购金额" width="130" align="right">
              <template #default="{ row }">{{ money(row.amount) }}</template>
            </el-table-column>
          </el-table>
        </el-tab-pane>

        <el-tab-pane label="生产报表" name="production">
          <div class="prod-totals">
            <span>生产订单 <b>{{ production.totals.order_count }}</b></span>
            <span>计划产量 <b>{{ production.totals.plan_qty }}</b></span>
            <span>完工订单 <b>{{ production.totals.completed_count }}</b></span>
            <span>材料成本 <b>{{ money(production.totals.material_cost) }}</b></span>
            <span>生产费用 <b>{{ money(production.totals.expense_cost) }}</b></span>
            <span>总成本 <b class="total">{{ money(production.totals.total_cost) }}</b></span>
          </div>
          <el-table :data="production.rows" size="small" v-loading="loading">
            <el-table-column prop="order_no" label="生产单号" width="160" />
            <el-table-column prop="product_name" label="成品" min-width="150" show-overflow-tooltip />
            <el-table-column prop="quantity" label="数量" width="70" align="right" />
            <el-table-column label="状态" width="90" align="center">
              <template #default="{ row }">{{ prodStatus[row.status] || row.status }}</template>
            </el-table-column>
            <el-table-column label="工序" width="80" align="center">
              <template #default="{ row }">{{ row.process_done }}/{{ row.process_total }}</template>
            </el-table-column>
            <el-table-column label="材料成本" width="110" align="right">
              <template #default="{ row }">{{ money(row.material_cost) }}</template>
            </el-table-column>
            <el-table-column label="生产费用" width="110" align="right">
              <template #default="{ row }">{{ money(row.expense_cost) }}</template>
            </el-table-column>
            <el-table-column label="总成本" width="110" align="right">
              <template #default="{ row }">{{ money(row.total_cost) }}</template>
            </el-table-column>
            <el-table-column label="创建时间" width="140">
              <template #default="{ row }">{{ shortTime(row.created_at) }}</template>
            </el-table-column>
          </el-table>
        </el-tab-pane>
      </el-tabs>
    </div>
  </div>
</template>

<style scoped>
.header-filter { display: flex; gap: 10px; align-items: center; }
.summary-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 12px; margin-bottom: 14px; }
.summary-card { background: #fff; border: 1px solid var(--border); border-radius: 10px; padding: 14px 16px; display: flex; flex-direction: column; gap: 4px; }
.summary-card .label { font-size: 13px; color: var(--text-secondary); }
.summary-card .value { font-size: 22px; }
.summary-card .extra { font-size: 12px; color: var(--text-secondary); }
.section-title { margin: 16px 0 8px; font-size: 14px; font-weight: 600; }
.section-title:first-child { margin-top: 0; }
.profit { color: #10b981; }
.loss { color: #ef4444; }
.prod-totals { display: flex; flex-wrap: wrap; gap: 20px; padding: 10px 14px; background: var(--fill-2, #f6f8fa); border-radius: 8px; margin-bottom: 12px; font-size: 13px; color: var(--text-secondary); }
.prod-totals b { color: var(--text-primary); }
.prod-totals .total { color: #ef4444; font-size: 16px; }
</style>
