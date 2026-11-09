<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import * as echarts from 'echarts'
import { aiApi, productApi, salesApi } from '../api'
import { useUserStore } from '../stores/user'

const userStore = useUserStore()

const allTabs = [
  { key: 'chat', label: '智能问答', icon: 'ChatDotRound', perm: 'ai:assist' },
  { key: 'insights', label: '经营洞察', icon: 'DataAnalysis', perm: 'ai:insight' },
  { key: 'replenishment', label: '智能补货', icon: 'Box', perm: 'ai:replenish' },
  { key: 'forecast', label: '销售预测', icon: 'TrendCharts', perm: 'ai:forecast' },
]
const tabs = computed(() => allTabs.filter((t) => userStore.hasPerm(t.perm)))
const activeTab = ref(tabs.value[0]?.key || 'chat')
const aiStatus = ref({})
const loading = ref(false)
const canCreatePurchase = computed(() => userStore.hasPerm('purchase:manage'))

/* ---------------- 问答 ---------------- */
// 推荐提问由角色决定（后端 role_meta.ai_prompts）
const quickPrompts = computed(() => userStore.aiPrompts)
const messages = ref([
  {
    role: 'assistant',
    content:
      '你好，我是你的 AI 经营助理。我已经接入了商品、订单、库存和客户数据，可以直接用自然语言向我提问。',
    source: 'system',
  },
])
const input = ref('')
const chatBox = ref()

function scrollChat() {
  nextTick(() => {
    if (chatBox.value) chatBox.value.scrollTop = chatBox.value.scrollHeight
  })
}

async function send(text) {
  const question = (text ?? input.value).trim()
  if (!question || loading.value) return
  messages.value.push({ role: 'user', content: question })
  input.value = ''
  scrollChat()
  loading.value = true
  try {
    const history = messages.value
      .filter((m) => m.source !== 'system')
      .slice(-6, -1)
      .map((m) => ({ role: m.role, content: m.content }))
    const res = await aiApi.chat({ message: question, history })
    messages.value.push({
      role: 'assistant',
      content: res.answer,
      source: res.source,
      data: res.data || [],
      toolsUsed: res.tools_used || [],
      draft: res.draft || null,
      created: false,
      saving: false,
    })
  } catch (err) {
    // 错误提示由拦截器处理
  } finally {
    loading.value = false
    scrollChat()
  }
}

/** 确认建单：把 AI 给出的预览提交为草稿订单。AI 本身不写库，落库始终由用户触发。 */
async function confirmDraft(msg) {
  if (!msg.draft?.draft || msg.created) return
  msg.saving = true
  try {
    await salesApi.create(msg.draft.draft)
    msg.created = true
    ElMessage.success('草稿订单已创建，可在「销售订单」中确认扣库存')
  } finally {
    msg.saving = false
  }
}

const FIELD_LABELS = {
  name: '名称',
  sku: 'SKU',
  customer: '客户',
  order_no: '单号',
  status: '状态',
  order_date: '日期',
  total_amount: '金额',
  unit_price: '单价',
  quantity: '数量',
  amount: '金额',
  orders: '单数',
  level: '等级',
  age_days: '账龄',
  unit: '单位',
  stock_qty: '库存',
  safety_stock: '安全库存',
  in_transit: '在途',
  days_of_cover: '可支撑天数',
  created_by: '下单人',
  product: '商品',
}

function labelOf(key) {
  return FIELD_LABELS[key] || key
}

/* ---------------- 经营洞察 ---------------- */
const insights = ref([])
const insightLoading = ref(false)

async function loadInsights() {
  insightLoading.value = true
  try {
    insights.value = await aiApi.insights()
  } finally {
    insightLoading.value = false
  }
}

/* ---------------- 智能补货 ---------------- */
const replenishment = ref([])
const repLoading = ref(false)
const selectedRows = ref([])
const leadTime = ref(15)

async function loadReplenishment() {
  repLoading.value = true
  try {
    replenishment.value = await aiApi.replenishment(leadTime.value)
    selectedRows.value = []
  } finally {
    repLoading.value = false
  }
}

const selectedCost = computed(() =>
  selectedRows.value.reduce((sum, r) => sum + r.estimated_cost, 0)
)

async function createPurchase() {
  const ids = selectedRows.value.map((r) => r.product_id)
  await ElMessageBox.confirm(
    ids.length ? `将为选中的 ${ids.length} 个商品生成采购单，是否继续？` : `将为全部补货建议生成采购单，是否继续？`,
    '一键生成采购单',
    { type: 'info' }
  )
  const res = await aiApi.createPurchase(ids.length ? ids : null)
  ElMessage.success(`已生成 ${res.count} 张采购单，可在采购订单页面查看`)
  loadReplenishment()
}

function onSelectionChange(rows) {
  selectedRows.value = rows
}

/* ---------------- 销售预测 ---------------- */
const forecast = ref(null)
const forecastProduct = ref(null)
const products = ref([])
const forecastRef = ref()
let forecastChart = null

async function loadForecast() {
  const data = await aiApi.forecast({ product_id: forecastProduct.value || undefined, days: 14 })
  forecast.value = data
  await nextTick()
  if (!forecastChart && forecastRef.value) {
    forecastChart = echarts.init(forecastRef.value)
  }
  renderForecast()
}

function renderForecast() {
  if (!forecastChart || !forecast.value) return
  const history = forecast.value.history
  const future = forecast.value.forecast
  const all = [...history, ...future]
  forecastChart.setOption(
    {
      tooltip: { trigger: 'axis' },
      grid: { left: 10, right: 20, top: 20, bottom: 10, containLabel: true },
      legend: { data: ['历史数据', 'AI 预测'], top: 0, right: 10, itemWidth: 10, itemHeight: 10 },
      xAxis: {
        type: 'category',
        data: all.map((x) => x.date.slice(5)),
        axisLabel: { color: '#9ca3af' },
        axisLine: { lineStyle: { color: '#e5e7eb' } },
      },
      yAxis: { type: 'value', splitLine: { lineStyle: { color: '#f1f5f9' } }, axisLabel: { color: '#9ca3af' } },
      series: [
        {
          name: '历史数据',
          type: 'line',
          smooth: true,
          showSymbol: false,
          data: history.map((x) => x.value),
          lineStyle: { color: '#6366f1', width: 2 },
          areaStyle: {
            color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
              { offset: 0, color: 'rgba(99,102,241,0.22)' },
              { offset: 1, color: 'rgba(99,102,241,0.02)' },
            ]),
          },
        },
        {
          name: 'AI 预测',
          type: 'line',
          smooth: true,
          showSymbol: false,
          data: [...Array(history.length - 1).fill(null), history[history.length - 1]?.value, ...future.map((x) => x.value)],
          lineStyle: { color: '#f59e0b', width: 2, type: 'dashed' },
        },
      ],
    },
    true
  )
}

function onResize() {
  forecastChart?.resize()
}

onMounted(async () => {
  aiStatus.value = await aiApi.status()
  if (userStore.hasPerm('product:view')) {
    const p = await productApi.list({ page_size: 200 })
    products.value = p.items
  }
  window.addEventListener('resize', onResize)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', onResize)
  forecastChart?.dispose()
  forecastChart = null
})

function switchTab(key) {
  activeTab.value = key
  if (key === 'insights' && !insights.value.length) loadInsights()
  if (key === 'replenishment' && !replenishment.value.length) loadReplenishment()
  if (key === 'forecast' && !forecast.value) loadForecast()
}

function money(value) {
  return '¥' + Number(value || 0).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

const levelMap = {
  danger: { text: '紧急', type: 'danger' },
  warning: { text: '关注', type: 'warning' },
  success: { text: '良好', type: 'success' },
  info: { text: '提示', type: 'info' },
}
</script>

<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h1 class="page-title">AI 经营助手</h1>
        <p class="page-subtitle">
          当前模式：{{ aiStatus.mode || '加载中…' }} · 所有结论基于系统实时业务数据
        </p>
      </div>
      <el-tag :type="aiStatus.llm_enabled ? 'success' : 'info'" effect="plain" round>
        {{ aiStatus.llm_enabled ? '大模型已启用' : '规则引擎（可配置大模型）' }}
      </el-tag>
    </div>

    <div class="tab-bar">
      <button
        v-for="tab in tabs"
        :key="tab.key"
        class="tab-btn"
        :class="{ active: activeTab === tab.key }"
        @click="switchTab(tab.key)"
      >
        <el-icon><component :is="tab.icon" /></el-icon>
        {{ tab.label }}
      </button>
    </div>

    <!-- 智能问答 -->
    <div v-if="activeTab === 'chat'" class="chat-layout">
      <div class="card chat-card">
        <div ref="chatBox" class="chat-body">
          <div v-for="(msg, index) in messages" :key="index" class="message" :class="msg.role">
            <div class="avatar">
              <el-icon v-if="msg.role === 'assistant'"><MagicStick /></el-icon>
              <el-icon v-else><User /></el-icon>
            </div>
            <div class="bubble-wrap">
              <div class="bubble">{{ msg.content }}</div>
              <span v-if="msg.role === 'assistant' && msg.source !== 'system'" class="source-tag">
                {{ msg.source === 'llm' ? '大模型生成' : '数据规则引擎' }}
                <template v-if="msg.toolsUsed && msg.toolsUsed.length">
                  · 数据来自 {{ msg.toolsUsed.join(' / ') }}
                </template>
              </span>
              <el-table
                v-if="msg.data && msg.data.length"
                :data="msg.data.slice(0, 10)"
                size="small"
                border
                max-height="260"
                class="answer-table"
              >
                <el-table-column
                  v-for="key in Object.keys(msg.data[0]).slice(0, 6)"
                  :key="key"
                  :prop="key"
                  :label="labelOf(key)"
                  min-width="110"
                  show-overflow-tooltip
                />
              </el-table>
              <div v-if="msg.draft && msg.draft.found" class="draft-card">
                <div class="draft-head">
                  <span>待确认订单 · {{ msg.draft.customer.name }}</span>
                  <b>¥{{ Number(msg.draft.total_amount || 0).toLocaleString('zh-CN', { minimumFractionDigits: 2 }) }}</b>
                </div>
                <el-table :data="msg.draft.items" size="small" border class="answer-table">
                  <el-table-column prop="name" label="商品" min-width="120" show-overflow-tooltip />
                  <el-table-column prop="quantity" label="数量" width="70" align="right" />
                  <el-table-column prop="price_source" label="价格来源" width="100" />
                  <el-table-column label="小计" width="110" align="right">
                    <template #default="{ row }">
                      ¥{{ Number(row.amount || 0).toLocaleString('zh-CN', { minimumFractionDigits: 2 }) }}
                    </template>
                  </el-table-column>
                </el-table>
                <div v-if="msg.draft.warnings && msg.draft.warnings.length" class="draft-warn">
                  <p v-for="(warn, wi) in msg.draft.warnings" :key="wi">⚠️ {{ warn }}</p>
                </div>
                <div class="draft-actions">
                  <el-button
                    type="primary"
                    size="small"
                    :loading="msg.saving"
                    :disabled="msg.created"
                    @click="confirmDraft(msg)"
                  >
                    {{ msg.created ? '已建单' : '确认建单' }}
                  </el-button>
                  <span class="draft-hint">确认后生成草稿订单，仍需在「销售订单」里确认才会扣库存</span>
                </div>
              </div>
            </div>
          </div>
          <div v-if="loading" class="message assistant">
            <div class="avatar"><el-icon><MagicStick /></el-icon></div>
            <div class="bubble thinking">正在分析经营数据…</div>
          </div>
        </div>

        <div class="quick-prompts">
          <el-tag
            v-for="p in quickPrompts"
            :key="p"
            class="prompt-tag"
            effect="plain"
            round
            @click="send(p)"
          >
            {{ p }}
          </el-tag>
        </div>

        <div class="chat-input">
          <el-input
            v-model="input"
            type="textarea"
            :rows="2"
            resize="none"
            placeholder="例如：帮我分析一下这个月的经营情况（Enter 发送，Shift+Enter 换行）"
            @keydown.enter.exact.prevent="send()"
          />
          <el-button type="primary" :loading="loading" @click="send()">
            <el-icon><Promotion /></el-icon>&nbsp;发送
          </el-button>
        </div>
      </div>

      <div class="card side-card">
        <h3 class="side-title">AI 能帮你做什么</h3>
        <div class="ability">
          <div class="ability-item">
            <el-icon><Box /></el-icon>
            <div><strong>自动补货测算</strong><p>结合日均销量、安全库存和采购周期计算建议补货量</p></div>
          </div>
          <div class="ability-item">
            <el-icon><TrendCharts /></el-icon>
            <div><strong>销售趋势预测</strong><p>基于近 30 天数据做线性回归预测未来走势</p></div>
          </div>
          <div class="ability-item">
            <el-icon><Warning /></el-icon>
            <div><strong>风险自动识别</strong><p>滞销库存、客户流失、销售下滑自动预警</p></div>
          </div>
          <div class="ability-item">
            <el-icon><Document /></el-icon>
            <div><strong>一键生成单据</strong><p>补货建议可自动转换为采购草稿单，减少录入工作量</p></div>
          </div>
        </div>
      </div>
    </div>

    <!-- 经营洞察 -->
    <div v-if="activeTab === 'insights'" class="card">
      <div class="panel-head">
        <h3>经营洞察</h3>
        <el-button :loading="insightLoading" @click="loadInsights"><el-icon><Refresh /></el-icon>&nbsp;重新分析</el-button>
      </div>
      <div v-loading="insightLoading" class="insight-grid">
        <div v-for="(item, index) in insights" :key="index" class="insight-card">
          <div class="insight-top">
            <el-tag :type="levelMap[item.level]?.type || 'info'" effect="light" round size="small">
              {{ levelMap[item.level]?.text || '提示' }}
            </el-tag>
            <span class="insight-type">{{ item.type }}</span>
          </div>
          <strong>{{ item.title }}</strong>
          <p>{{ item.detail }}</p>
          <div class="insight-action"><el-icon><Opportunity /></el-icon>{{ item.action }}</div>
        </div>
        <div v-if="!insights.length && !insightLoading" class="empty-text">点击「重新分析」生成洞察</div>
      </div>
    </div>

    <!-- 智能补货 -->
    <div v-if="activeTab === 'replenishment'" class="card">
      <div class="panel-head">
        <div>
          <h3>智能补货建议</h3>
          <p class="hint">补货量 = 日均销量 ×（采购周期 + 10 天）+ 1.2 × 安全库存 − 当前库存 − 在途采购</p>
        </div>
        <div class="panel-actions">
          <span class="hint">采购周期</span>
          <el-input-number v-model="leadTime" :min="1" :max="90" size="small" @change="loadReplenishment" />
          <span class="hint">天</span>
          <el-button v-if="canCreatePurchase" type="primary" @click="createPurchase">
            <el-icon><DocumentAdd /></el-icon>&nbsp;一键生成采购单
            <span v-if="selectedRows.length">（选中 {{ selectedRows.length }} 项 / {{ money(selectedCost) }}）</span>
          </el-button>
        </div>
      </div>

      <el-table
        v-loading="repLoading"
        :data="replenishment"
        size="default"
        @selection-change="onSelectionChange"
      >
        <el-table-column type="selection" width="46" />
        <el-table-column prop="name" label="商品" min-width="150" show-overflow-tooltip />
        <el-table-column prop="sku" label="SKU" width="110" />
        <el-table-column prop="stock_qty" label="当前库存" width="90" align="center" />
        <el-table-column label="在途" width="80" align="center">
          <template #default="{ row }">
            <span :style="row.in_transit > 0 ? 'color:#d97706;font-weight:600' : ''">{{ row.in_transit || 0 }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="safety_stock" label="安全库存" width="90" align="center" />
        <el-table-column prop="avg_daily_sales" label="日均销量" width="90" align="center" />
        <el-table-column label="可支撑" width="90" align="center">
          <template #default="{ row }">
            {{ row.days_of_cover === null ? '—' : row.days_of_cover + ' 天' }}
          </template>
        </el-table-column>
        <el-table-column label="建议补货" width="100" align="center">
          <template #default="{ row }">
            <b class="suggest">{{ row.suggested_qty }} {{ row.unit }}</b>
          </template>
        </el-table-column>
        <el-table-column label="预计成本" width="120" align="right">
          <template #default="{ row }">{{ money(row.estimated_cost) }}</template>
        </el-table-column>
        <el-table-column prop="supplier_name" label="建议供应商" min-width="140" show-overflow-tooltip />
        <el-table-column label="优先级" width="80" align="center">
          <template #default="{ row }">
            <el-tag :type="row.priority === '高' ? 'danger' : 'warning'" size="small" effect="light">
              {{ row.priority }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="reason" label="触发原因" min-width="180" show-overflow-tooltip />
      </el-table>
      <div v-if="!repLoading && !replenishment.length" class="empty-text">当前库存健康，暂无补货建议</div>
    </div>

    <!-- 销售预测 -->
    <div v-if="activeTab === 'forecast'" class="card">
      <div class="panel-head">
        <h3>销售预测（未来 14 天）</h3>
        <div class="panel-actions">
          <el-select v-model="forecastProduct" placeholder="全部商品（整体销售额）" clearable style="width: 220px" @change="loadForecast">
            <el-option v-for="p in products" :key="p.id" :label="p.name" :value="p.id" />
          </el-select>
          <el-button @click="loadForecast"><el-icon><Refresh /></el-icon>&nbsp;重新预测</el-button>
        </div>
      </div>

      <div v-if="forecast">
        <div class="forecast-summary">
          <div class="fs-item">
            <span>最近 7 天日均</span>
            <strong>{{ money(forecast.summary.recent_daily_avg) }}</strong>
          </div>
          <div class="fs-item">
            <span>预测日均</span>
            <strong>{{ money(forecast.summary.forecast_daily_avg) }}</strong>
          </div>
          <div class="fs-item">
            <span>预测 14 天合计</span>
            <strong>{{ money(forecast.summary.forecast_total) }}</strong>
          </div>
          <div class="fs-item">
            <span>趋势判断</span>
            <strong :class="forecast.summary.change_percent < -5 ? 'down' : 'up'">
              {{ forecast.summary.trend }} {{ Math.abs(forecast.summary.change_percent) }}%
            </strong>
          </div>
        </div>
        <div ref="forecastRef" class="forecast-chart"></div>
        <p class="hint forecast-note">
          预测基于近 30 天历史数据的线性趋势拟合，仅作经营参考；大促、季节因素需要结合实际情况调整。
        </p>
      </div>
    </div>
  </div>
</template>

<style scoped>
.tab-bar {
  display: flex;
  gap: 8px;
  margin-bottom: 16px;
}

.tab-btn {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 9px 18px;
  border-radius: 10px;
  border: 1px solid var(--border);
  background: #fff;
  color: var(--text-secondary);
  font-size: 13px;
  cursor: pointer;
  transition: all 0.2s;
}

.tab-btn.active {
  background: linear-gradient(90deg, #6366f1, #8b5cf6);
  border-color: transparent;
  color: #fff;
  box-shadow: 0 4px 12px rgba(99, 102, 241, 0.25);
}

.chat-layout {
  display: grid;
  grid-template-columns: 1fr 300px;
  gap: 14px;
  height: calc(100vh - 230px);
}

.chat-card {
  display: flex;
  flex-direction: column;
  padding: 0;
  overflow: hidden;
  min-height: 420px;
}

.chat-body {
  flex: 1;
  overflow-y: auto;
  padding: 20px;
  background: #fbfcfe;
}

.message {
  display: flex;
  gap: 10px;
  margin-bottom: 18px;
}

.message.user {
  flex-direction: row-reverse;
}

.avatar {
  width: 32px;
  height: 32px;
  border-radius: 9px;
  background: #eef2ff;
  color: #6366f1;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.message.user .avatar {
  background: #f1f5f9;
  color: #64748b;
}

.bubble-wrap {
  max-width: 76%;
}

.bubble {
  background: #fff;
  border: 1px solid var(--border);
  padding: 10px 14px;
  border-radius: 12px;
  white-space: pre-line;
  line-height: 1.7;
  font-size: 13px;
  color: #374151;
}

.message.user .bubble {
  background: #6366f1;
  border-color: #6366f1;
  color: #fff;
}

.bubble.thinking {
  color: #9ca3af;
  font-style: italic;
}

.source-tag {
  display: inline-block;
  margin-top: 6px;
  font-size: 11px;
  color: #9ca3af;
}

.answer-table {
  margin-top: 8px;
  width: 100%;
  font-size: 12px;
}

.draft-card {
  margin-top: 10px;
  padding: 12px;
  border: 1px solid #dbeafe;
  background: #f8fbff;
  border-radius: 8px;
}

.draft-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 13px;
  font-weight: 600;
  margin-bottom: 8px;
}

.draft-head b {
  color: #ef4444;
  font-size: 16px;
}

.draft-warn {
  margin-top: 8px;
  font-size: 12px;
  color: #b45309;
}

.draft-warn p {
  margin: 2px 0;
}

.draft-actions {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 10px;
}

.draft-hint {
  font-size: 11px;
  color: #9ca3af;
}

.quick-prompts {
  padding: 10px 20px;
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  border-top: 1px solid var(--border);
  background: #fff;
}

.prompt-tag {
  cursor: pointer;
}

.chat-input {
  display: flex;
  gap: 10px;
  padding: 12px 20px 18px;
  background: #fff;
  align-items: flex-end;
}

.side-card {
  height: fit-content;
}

.side-title {
  font-size: 14px;
  margin: 0 0 14px;
}

.ability {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.ability-item {
  display: flex;
  gap: 10px;
}

.ability-item .el-icon {
  color: #6366f1;
  font-size: 18px;
  margin-top: 2px;
}

.ability-item strong {
  display: block;
  font-size: 13px;
  margin-bottom: 3px;
}

.ability-item p {
  margin: 0;
  font-size: 12px;
  color: var(--text-secondary);
  line-height: 1.55;
}

.panel-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 16px;
  gap: 12px;
  flex-wrap: wrap;
}

.panel-head h3 {
  margin: 0 0 4px;
  font-size: 15px;
}

.panel-actions {
  display: flex;
  gap: 8px;
  align-items: center;
}

.hint {
  color: var(--text-secondary);
  font-size: 12px;
  margin: 0;
}

.insight-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 14px;
  min-height: 120px;
}

.insight-card {
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 16px;
  background: #fbfcfe;
}

.insight-top {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 10px;
}

.insight-type {
  font-size: 11px;
  color: #9ca3af;
}

.insight-card strong {
  display: block;
  font-size: 13px;
  margin-bottom: 6px;
}

.insight-card p {
  margin: 0 0 10px;
  font-size: 12px;
  color: var(--text-secondary);
  line-height: 1.6;
}

.insight-action {
  font-size: 12px;
  color: #6366f1;
  display: flex;
  align-items: center;
  gap: 4px;
}

.suggest {
  color: #ef4444;
}

.forecast-summary {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
  margin-bottom: 12px;
}

.fs-item {
  background: #f8fafc;
  border-radius: 10px;
  padding: 12px 14px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.fs-item span {
  font-size: 12px;
  color: var(--text-secondary);
}

.fs-item strong {
  font-size: 18px;
}

.fs-item strong.up {
  color: #10b981;
}

.fs-item strong.down {
  color: #ef4444;
}

.forecast-chart {
  height: 330px;
}

.forecast-note {
  margin-top: 10px;
}

@media (max-width: 1200px) {
  .chat-layout {
    grid-template-columns: 1fr;
  }
  .insight-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
