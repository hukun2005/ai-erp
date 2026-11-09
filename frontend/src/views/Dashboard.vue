<script setup>
/**
 * 经营看板：先给结论（KPI），再给动作（待办与快捷入口），最后给依据（趋势、洞察、明细）。
 * 卡片上的次要说明必须和主指标同源，避免“库存预警”下面写着草稿单数量这类错位信息。
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import * as echarts from 'echarts'
import { aiApi, approvalApi, dashboardApi } from '../api'
import { useUserStore } from '../stores/user'

const router = useRouter()
const userStore = useUserStore()

const summary = ref({})
const trend = ref([])
const insights = ref([])
const recentOrders = ref([])
const approvalCount = ref(0)
const loading = ref(true)
const refreshing = ref(false)
const trendDays = ref(30)

const trendRef = ref()
const pieRef = ref()
let trendChart = null
let pieChart = null

const todayLabel = new Intl.DateTimeFormat('zh-CN', {
  month: 'long',
  day: 'numeric',
  weekday: 'long',
}).format(new Date())

function money(value) {
  return '¥' + Number(value || 0).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function compactMoney(value) {
  const amount = Number(value || 0)
  if (amount >= 10000) return `¥${(amount / 10000).toFixed(1)} 万`
  return money(amount)
}

const avgOrderValue = computed(() => {
  const orders = Number(summary.value.today_orders || 0)
  if (!orders) return 0
  return Number(summary.value.today_sales || 0) / orders
})

// 6 个核心指标：每张卡都带一个可点击的去处，点一下就能处理对应的事
const cards = computed(() => [
  {
    key: 'today_sales',
    label: '今日销售额',
    icon: 'Money',
    color: '#6366f1',
    value: money(summary.value.today_sales),
    hint: `环比昨日 ${summary.value.today_growth ?? 0}%`,
    hintTone:
      Number(summary.value.today_growth || 0) === 0 ? 'muted' : Number(summary.value.today_growth) < 0 ? 'down' : 'up',
    to: '/sales-orders',
  },
  {
    key: 'month_sales',
    label: '本月销售额',
    icon: 'TrendCharts',
    color: '#8b5cf6',
    value: money(summary.value.month_sales),
    hint: `环比上月 ${summary.value.month_growth ?? 0}%`,
    hintTone:
      Number(summary.value.month_growth || 0) === 0 ? 'muted' : Number(summary.value.month_growth) < 0 ? 'down' : 'up',
    to: '/reports',
  },
  {
    key: 'today_orders',
    label: '今日有效订单',
    icon: 'Tickets',
    color: '#0ea5e9',
    value: Number(summary.value.today_orders || 0),
    hint: `客单价 ${compactMoney(avgOrderValue.value)}`,
    hintTone: 'muted',
    to: '/sales-orders',
  },
  {
    key: 'inventory_value',
    label: '库存资金占用',
    icon: 'Box',
    color: '#f59e0b',
    value: money(summary.value.inventory_value),
    hint: `${summary.value.product_count || 0} 个在售商品`,
    hintTone: 'muted',
    to: '/inventory',
  },
  {
    key: 'low_stock_count',
    label: '库存预警商品',
    icon: 'Warning',
    color: '#ef4444',
    value: Number(summary.value.low_stock_count || 0),
    hint: '低于安全库存，需补货',
    hintTone: Number(summary.value.low_stock_count || 0) > 0 ? 'down' : 'muted',
    to: '/inventory',
    query: { low: '1' },
  },
  {
    key: 'pending_purchase_count',
    label: '待入库采购单',
    icon: 'Van',
    color: '#10b981',
    value: Number(summary.value.pending_purchase_count || 0),
    hint: '已下单，等待收货',
    hintTone: Number(summary.value.pending_purchase_count || 0) > 0 ? 'up' : 'muted',
    to: '/purchase-orders',
    query: { status: 'ordered' },
  },
])

const quickActions = computed(() =>
  [
    { label: '新建销售订单', icon: 'Sell', to: '/sales-orders', query: { new: '1' }, perm: 'sales:manage' },
    {
      label: '新建采购订单',
      icon: 'ShoppingCart',
      to: '/purchase-orders',
      query: { new: '1' },
      perm: 'purchase:manage',
    },
    { label: '登记入库', icon: 'Download', to: '/inbounds', query: { new: '1' }, perm: 'inbound:manage' },
    { label: '问 AI 一句', icon: 'MagicStick', to: '/ai', query: {}, perm: 'ai:assist' },
  ].filter((action) => userStore.hasPerm(action.perm)),
)

// 待办顺序按“最影响业务”排：审批 → 开单 → 补货 → 收货
const todos = computed(() =>
  [
    {
      key: 'approvals',
      label: '待审批订单',
      hint: '通过后才会扣减库存',
      count: approvalCount.value,
      to: '/approvals',
      permAny: ['sales:approve', 'purchase:approve'],
    },
    {
      key: 'draft',
      label: '草稿销售单',
      hint: '还没提交审批，客户可能还在等',
      count: Number(summary.value.draft_sales_count || 0),
      to: '/sales-orders',
      query: { status: 'draft' },
      perm: 'sales:view',
    },
    {
      key: 'low',
      label: '库存预警商品',
      hint: '低于安全库存，建议尽快补货',
      count: Number(summary.value.low_stock_count || 0),
      to: '/inventory',
      query: { low: '1' },
      perm: 'inventory:view',
    },
    {
      key: 'purchase',
      label: '待收货采购单',
      hint: '供应商已发货或待发货',
      count: Number(summary.value.pending_purchase_count || 0),
      to: '/purchase-orders',
      query: { status: 'ordered' },
      perm: 'purchase:view',
    },
  ].filter((item) => (item.permAny ? userStore.hasAnyPerm(item.permAny) : userStore.hasPerm(item.perm))),
)

const todoTotal = computed(() => todos.value.reduce((sum, item) => sum + item.count, 0))

const statusMap = {
  draft: { text: '草稿', type: 'info' },
  pending: { text: '待审批', type: 'warning' },
  confirmed: { text: '已确认', type: 'primary' },
  completed: { text: '已完成', type: 'success' },
  cancelled: { text: '已取消', type: 'danger' },
}

const levelMap = {
  danger: { text: '紧急', type: 'danger' },
  warning: { text: '关注', type: 'warning' },
  success: { text: '良好', type: 'success' },
  info: { text: '提示', type: 'info' },
}

const hasTrendData = computed(() => trend.value.some((item) => Number(item.amount || 0) > 0))

function renderTrend() {
  if (!trendChart) return
  const dates = trend.value.map((x) => x.date.slice(5))
  trendChart.setOption({
    tooltip: { trigger: 'axis', valueFormatter: (v) => '¥' + Number(v).toLocaleString() },
    grid: { left: 8, right: 12, top: 34, bottom: 4, containLabel: true },
    legend: { data: ['销售额', '订单数'], top: 0, right: 4, itemWidth: 10, itemHeight: 10 },
    xAxis: {
      type: 'category',
      data: dates,
      axisLine: { lineStyle: { color: '#e5e7eb' } },
      axisLabel: { color: '#9ca3af', fontSize: 11 },
    },
    yAxis: [
      { type: 'value', splitLine: { lineStyle: { color: '#f1f5f9' } }, axisLabel: { color: '#9ca3af', fontSize: 11 } },
      { type: 'value', splitLine: { show: false }, axisLabel: { color: '#9ca3af', fontSize: 11 } },
    ],
    series: [
      {
        name: '销售额',
        type: 'line',
        smooth: true,
        showSymbol: false,
        data: trend.value.map((x) => x.amount),
        lineStyle: { color: '#6366f1', width: 2 },
        areaStyle: {
          color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: 'rgba(99,102,241,0.25)' },
            { offset: 1, color: 'rgba(99,102,241,0.02)' },
          ]),
        },
      },
      {
        name: '订单数',
        type: 'bar',
        yAxisIndex: 1,
        barWidth: 7,
        data: trend.value.map((x) => x.orders),
        itemStyle: { color: 'rgba(139,92,246,0.32)', borderRadius: [4, 4, 0, 0] },
      },
    ],
  })
}

async function renderPie() {
  const data = await dashboardApi.categoryDistribution()
  if (!pieChart) pieChart = echarts.init(pieRef.value)
  pieChart.setOption({
    tooltip: { trigger: 'item', formatter: '{b}<br/>¥{c} ({d}%)' },
    legend: {
      bottom: 0,
      icon: 'circle',
      itemWidth: 8,
      itemHeight: 8,
      textStyle: { fontSize: 11, color: '#6b7280' },
    },
    color: ['#6366f1', '#8b5cf6', '#0ea5e9', '#10b981', '#f59e0b'],
    series: [
      {
        type: 'pie',
        radius: ['46%', '68%'],
        center: ['50%', '42%'],
        avoidLabelOverlap: true,
        itemStyle: { borderRadius: 6, borderColor: '#fff', borderWidth: 2 },
        label: { show: false },
        data,
      },
    ],
  })
}

async function loadTrend() {
  trend.value = await dashboardApi.trend(trendDays.value)
  renderTrend()
}

function resize() {
  trendChart?.resize()
  pieChart?.resize()
}

async function loadApprovalCount() {
  if (!userStore.hasAnyPerm(['sales:approve', 'purchase:approve'])) return
  try {
    const data = await approvalApi.pending()
    approvalCount.value = Array.isArray(data.items) ? data.items.length : Number(data.total || 0)
  } catch {
    approvalCount.value = 0
  }
}

async function loadAll() {
  const [s, ins, orders] = await Promise.all([
    dashboardApi.summary(),
    aiApi.insights(),
    dashboardApi.recentOrders(),
  ])
  summary.value = s
  insights.value = ins.slice(0, 4)
  recentOrders.value = orders
  await loadTrend()
  await renderPie()
  await loadApprovalCount()
}

function openInsight(item) {
  if (!item || item.type !== 'stock' || !item.sku) return
  router.push({ path: '/inventory', query: { keyword: item.sku } })
}

function insightClickable(item) {
  return item?.type === 'stock' && Boolean(item.sku)
}

async function refresh() {
  if (refreshing.value) return
  refreshing.value = true
  try {
    await loadAll()
  } finally {
    refreshing.value = false
  }
}

onMounted(async () => {
  try {
    trendChart = echarts.init(trendRef.value)
    await loadAll()
  } finally {
    loading.value = false
  }
  window.addEventListener('resize', resize)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', resize)
  trendChart?.dispose()
  pieChart?.dispose()
})
</script>

<template>
  <div class="page" v-loading="loading">
    <div class="page-header">
      <div>
        <h1 class="page-title">经营看板</h1>
        <p class="page-subtitle">
          {{ todayLabel }} ·
          <template v-if="todoTotal">有 {{ todoTotal }} 项待处理</template>
          <template v-else>今天没有待办，可以安心开发新客户</template>
        </p>
      </div>
      <div class="header-actions">
        <el-button :loading="refreshing" @click="refresh">
          <el-icon><Refresh /></el-icon>&nbsp;刷新
        </el-button>
        <el-button v-if="userStore.hasPerm('ai:assist')" type="primary" plain @click="router.push('/ai')">
          <el-icon><MagicStick /></el-icon>&nbsp;问 AI 要建议
        </el-button>
      </div>
    </div>

    <div v-if="quickActions.length" class="quick-bar">
      <span class="quick-label">常用操作</span>
      <button
        v-for="action in quickActions"
        :key="action.label"
        type="button"
        class="quick-item"
        @click="router.push({ path: action.to, query: action.query })"
      >
        <el-icon><component :is="action.icon" /></el-icon>
        <span>{{ action.label }}</span>
      </button>
    </div>

    <div class="stat-grid">
      <button
        v-for="card in cards"
        :key="card.key"
        type="button"
        class="card stat-card"
        @click="router.push({ path: card.to, query: card.query || {} })"
      >
        <span class="stat-icon" :style="{ background: card.color + '16', color: card.color }">
          <el-icon><component :is="card.icon" /></el-icon>
        </span>
        <span class="stat-body">
          <span class="stat-label">{{ card.label }}</span>
          <strong class="stat-value">{{ card.value }}</strong>
          <span class="stat-hint" :class="card.hintTone">{{ card.hint }}</span>
        </span>
      </button>
    </div>

    <div class="row row-main">
      <div class="card chart-card">
        <div class="card-head">
          <div>
            <h3>销售趋势</h3>
            <p class="card-sub">按天统计已确认与已完成的销售订单</p>
          </div>
          <el-radio-group v-model="trendDays" size="small" @change="loadTrend">
            <el-radio-button :value="7">7 天</el-radio-button>
            <el-radio-button :value="30">30 天</el-radio-button>
          </el-radio-group>
        </div>
        <div class="chart-wrap">
          <div ref="trendRef" class="chart trend-chart"></div>
          <div v-if="!hasTrendData && !loading" class="chart-overlay">
            <el-icon><DataLine /></el-icon>
            <p>这段时间还没有销售记录</p>
            <router-link v-if="userStore.hasPerm('sales:manage')" to="/sales-orders?new=1">
              去开第一张销售订单 →
            </router-link>
          </div>
        </div>
      </div>

      <div class="card todo-card">
        <div class="card-head">
          <h3>今日待办</h3>
          <span class="todo-total" :class="{ clear: !todoTotal }">{{ todoTotal }}</span>
        </div>
        <div v-if="todoTotal" class="todo-list">
          <router-link
            v-for="item in todos"
            :key="item.key"
            :to="{ path: item.to, query: item.query || {} }"
            class="todo-item"
            :class="{ quiet: !item.count }"
          >
            <span class="todo-count">{{ item.count }}</span>
            <span class="todo-text">
              <strong>{{ item.label }}</strong>
              <small>{{ item.hint }}</small>
            </span>
            <el-icon class="todo-arrow"><ArrowRight /></el-icon>
          </router-link>
        </div>
        <div v-else class="todo-empty">
          <el-icon><CircleCheck /></el-icon>
          <p>今天没有待办</p>
          <span>审批、草稿单、库存预警都清空了</span>
        </div>
      </div>
    </div>

    <div class="row row-secondary">
      <div class="card">
        <div class="card-head">
          <h3>
            <el-icon class="ai-icon"><MagicStick /></el-icon>
            AI 经营洞察
          </h3>
          <router-link v-if="userStore.hasPerm('ai:assist')" to="/ai" class="more-link">更多 →</router-link>
        </div>
        <div class="insight-list">
          <div
            v-for="(item, index) in insights"
            :key="index"
            class="insight-item"
            :class="{ clickable: insightClickable(item) }"
            :title="insightClickable(item) ? '点击查看该商品的库存详情' : ''"
            @click="openInsight(item)"
          >
            <el-tag :type="levelMap[item.level]?.type || 'info'" size="small" effect="light" round>
              {{ levelMap[item.level]?.text || '提示' }}
            </el-tag>
            <div class="insight-content">
              <strong>{{ item.title }}</strong>
              <p>{{ item.detail }}</p>
              <span class="insight-action">{{ item.action }}</span>
            </div>
          </div>
          <div v-if="!insights.length" class="mini-empty">暂无洞察，等有订单数据后会自动生成</div>
        </div>
      </div>

      <div class="card">
        <div class="card-head">
          <h3>最新销售订单</h3>
          <router-link to="/sales-orders" class="more-link">全部 →</router-link>
        </div>
        <el-table :data="recentOrders" size="small" :show-header="true">
          <el-table-column prop="order_no" label="订单号" min-width="150" />
          <el-table-column prop="customer_name" label="客户" min-width="120" show-overflow-tooltip />
          <el-table-column label="金额" width="110" align="right">
            <template #default="{ row }">
              <span class="money">{{ money(row.total_amount) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="状态" width="86" align="center">
            <template #default="{ row }">
              <el-tag :type="statusMap[row.status]?.type" size="small" effect="light">
                {{ statusMap[row.status]?.text }}
              </el-tag>
            </template>
          </el-table-column>
        </el-table>
        <div v-if="!recentOrders.length" class="mini-empty">还没有订单</div>
      </div>

      <div class="card pie-card">
        <div class="card-head">
          <h3>品类销售占比</h3>
        </div>
        <div ref="pieRef" class="chart pie-chart"></div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.header-actions {
  display: flex;
  gap: 8px;
}

.quick-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 14px;
}

.quick-label {
  font-size: 12px;
  color: var(--text-muted);
  margin-right: 2px;
}

.quick-item {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 32px;
  padding: 0 12px;
  border: 1px solid var(--border);
  border-radius: 999px;
  background: #fff;
  color: var(--text);
  font-size: 12.5px;
  cursor: pointer;
  transition: border-color 0.16s, color 0.16s, background 0.16s;
}

.quick-item .el-icon {
  color: var(--brand);
}

.quick-item:hover {
  border-color: #c7cbf5;
  background: #f8f9ff;
  color: var(--brand-dark);
}

.stat-grid {
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  gap: 12px;
  margin-bottom: 14px;
}

.stat-card {
  display: flex;
  gap: 11px;
  align-items: flex-start;
  padding: 14px;
  text-align: left;
  cursor: pointer;
  font-family: inherit;
  transition: box-shadow 0.18s ease, transform 0.18s ease, border-color 0.18s ease;
}

.stat-card:hover {
  box-shadow: 0 8px 20px rgba(15, 23, 42, 0.08);
  transform: translateY(-1px);
  border-color: #dcdfe6;
}

.stat-icon {
  width: 36px;
  height: 36px;
  border-radius: 10px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 18px;
  flex-shrink: 0;
}

.stat-body {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.stat-label {
  font-size: 12px;
  color: var(--text-secondary);
  white-space: nowrap;
}

.stat-value {
  font-size: 18px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.stat-hint {
  font-size: 11px;
  color: var(--text-muted);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.stat-hint.up {
  color: var(--success);
}

.stat-hint.down {
  color: var(--danger);
}

.row {
  display: grid;
  gap: 14px;
  margin-bottom: 14px;
}

.row-main {
  grid-template-columns: 2fr 1fr;
}

.row-secondary {
  grid-template-columns: 1fr 1.25fr 0.85fr;
}

.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 10px;
}

.card-head h3 {
  font-size: 14px;
  margin: 0;
  display: flex;
  align-items: center;
  gap: 6px;
}

.card-sub {
  margin: 3px 0 0;
  font-size: 11.5px;
  color: var(--text-muted);
}

.ai-icon {
  color: #6366f1;
}

.chart {
  width: 100%;
}

.chart-wrap {
  position: relative;
}

.trend-chart {
  height: 268px;
}

.pie-chart {
  height: 268px;
}

/* 没数据时用浮层盖住空坐标系，而不是把图表容器藏起来（隐藏后 ECharts 会按 0 宽度初始化） */
.chart-overlay {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 6px;
  background: #fff;
  color: var(--text-muted);
  font-size: 12.5px;
  border-radius: 10px;
}

.chart-overlay .el-icon {
  font-size: 26px;
  color: #cbd5e1;
}

.chart-overlay p {
  margin: 0;
}

.chart-overlay a {
  color: var(--brand);
  text-decoration: none;
}

.more-link {
  font-size: 12px;
  color: #6366f1;
  text-decoration: none;
}

.todo-total {
  min-width: 24px;
  height: 22px;
  padding: 0 8px;
  border-radius: 999px;
  background: #fef3c7;
  color: #b45309;
  font-size: 12px;
  font-weight: 600;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}

.todo-total.clear {
  background: #ecfdf5;
  color: #047857;
}

.todo-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.todo-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 9px 10px;
  border-radius: 9px;
  text-decoration: none;
  color: inherit;
  transition: background 0.16s;
}

.todo-item:hover {
  background: #f7f8ff;
}

.todo-item.quiet {
  opacity: 0.55;
}

.todo-count {
  width: 30px;
  height: 30px;
  border-radius: 9px;
  background: #eef2ff;
  color: #4338ca;
  font-weight: 700;
  font-size: 13px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  font-variant-numeric: tabular-nums;
}

.todo-item.quiet .todo-count {
  background: #f1f5f9;
  color: #64748b;
}

.todo-text {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 1px;
}

.todo-text strong {
  font-size: 13px;
  font-weight: 500;
}

.todo-text small {
  font-size: 11.5px;
  color: var(--text-muted);
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.todo-arrow {
  color: #cbd5e1;
  font-size: 13px;
}

.todo-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 4px;
  padding: 34px 10px;
  color: var(--text-muted);
  font-size: 12.5px;
  text-align: center;
}

.todo-empty .el-icon {
  font-size: 28px;
  color: #34d399;
}

.todo-empty p {
  margin: 4px 0 0;
  color: var(--text);
  font-weight: 500;
}

.todo-empty span {
  font-size: 11.5px;
}

.insight-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
  max-height: 268px;
  overflow-y: auto;
}

.insight-item {
  display: flex;
  gap: 10px;
  padding-bottom: 10px;
  border-bottom: 1px dashed var(--border);
}

.insight-item:last-child {
  border-bottom: none;
}

.insight-item.clickable {
  cursor: pointer;
  border-radius: 8px;
  transition: background 0.18s ease;
}

.insight-item.clickable:hover {
  background: var(--bg-soft, #f6f7fb);
}

.insight-item.clickable:hover .insight-action {
  text-decoration: underline;
}

.insight-content strong {
  display: block;
  font-size: 13px;
  margin-bottom: 3px;
}

.insight-content p {
  margin: 0 0 4px;
  font-size: 12px;
  color: var(--text-secondary);
  line-height: 1.55;
}

.insight-action {
  font-size: 12px;
  color: #6366f1;
}

.mini-empty {
  padding: 26px 10px;
  text-align: center;
  color: var(--text-muted);
  font-size: 12.5px;
}

@media (max-width: 1500px) {
  .stat-grid {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
}

@media (max-width: 1180px) {
  .row-main,
  .row-secondary {
    grid-template-columns: 1fr;
  }

  .stat-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .chart-overlay,
  .trend-chart,
  .pie-chart {
    height: 230px;
  }
}
</style>
