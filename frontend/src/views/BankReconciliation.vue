<script setup>
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { bankApi } from '../api'
import { saveBlob } from '../utils/download'

const loading = ref(false)
const importing = ref(false)
const statements = ref([])
const matches = ref([])
const statusFilter = ref('')
const activeTab = ref('matches')

const money = (v) =>
  '¥' + Number(v || 0).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })

const statusLabel = { unmatched: '未匹配', matched: '已对账', ignored: '已忽略' }
const statusTag = { unmatched: 'warning', matched: 'success', ignored: 'info' }

async function loadStatements() {
  loading.value = true
  try {
    const res = await bankApi.statements({ status: statusFilter.value || undefined, page_size: 200 })
    statements.value = res.items || []
  } finally {
    loading.value = false
  }
}

async function loadMatches() {
  const rows = await bankApi.matches({ status: 'suggested' })
  matches.value = rows || []
}

async function refresh() {
  await Promise.all([loadStatements(), loadMatches()])
}

async function downloadTemplate() {
  const blob = await bankApi.templateUrl()
  saveBlob(blob, '银行流水导入模板.xlsx')
}

async function onFile(file) {
  importing.value = true
  try {
    const res = await bankApi.import(file.raw)
    ElMessage.success(
      `导入完成：新增 ${res.created} 条，重复跳过 ${res.skipped} 条${
        res.errors?.length ? `，失败 ${res.errors.length} 条` : ''
      }`
    )
    await refresh()
  } finally {
    importing.value = false
  }
}

async function applyMatch(row) {
  await ElMessageBox.confirm(
    `按建议把 ${money(row.amount)} 核销到「${row.partner_name}」的未结单据并生成收/付款单？`,
    '确认对账',
    { type: 'warning' }
  )
  const res = await bankApi.applyMatch(row.id)
  ElMessage.success(`已生成 ${res.payment_no} 并完成核销`)
  await refresh()
}

async function dismissMatch(row) {
  await ElMessageBox.confirm('忽略这条建议？流水会标记为「已忽略」，不会生成收付款单。', '忽略建议', {
    type: 'info',
  })
  await bankApi.dismissMatch(row.id)
  ElMessage.success('已忽略')
  await refresh()
}

onMounted(refresh)
</script>

<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h1 class="page-title">银行对账</h1>
        <p class="page-subtitle">
          导入网银流水后自动匹配客户回款与供应商付款：按单号、金额、组合与先进先出给出核销建议，确认后才生成收/付款单。
        </p>
      </div>
      <div class="head-actions">
        <el-button @click="downloadTemplate"><el-icon><Download /></el-icon>&nbsp;下载模板</el-button>
        <el-upload :show-file-list="false" :auto-upload="false" accept=".xlsx" :on-change="onFile">
          <el-button type="primary" :loading="importing">
            <el-icon><Upload /></el-icon>&nbsp;导入银行流水
          </el-button>
        </el-upload>
        <el-button @click="refresh"><el-icon><Refresh /></el-icon>&nbsp;刷新</el-button>
      </div>
    </div>

    <el-tabs v-model="activeTab" class="card" @tab-change="refresh">
      <el-tab-pane label="对账建议" name="matches">
        <el-table :data="matches" size="small" border>
          <el-table-column prop="partner_name" label="往来单位" min-width="160" />
          <el-table-column label="类型" width="90" align="center">
            <template #default="{ row }">
              <el-tag size="small" :type="row.partner_type === 'customer' ? 'success' : 'warning'" effect="plain">
                {{ row.partner_type === 'customer' ? '客户回款' : '供应商付款' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="金额" width="130" align="right">
            <template #default="{ row }">{{ money(row.amount) }}</template>
          </el-table-column>
          <el-table-column label="置信度" width="100" align="center">
            <template #default="{ row }">
              <el-tag size="small" :type="row.confidence === 'high' ? 'success' : row.confidence === 'medium' ? 'warning' : 'info'">
                {{ row.confidence === 'high' ? '高' : row.confidence === 'medium' ? '中' : '低' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="strategy" label="匹配策略" width="140" />
          <el-table-column label="操作" width="160" align="center">
            <template #default="{ row }">
              <el-button link type="primary" size="small" @click="applyMatch(row)">确认对账</el-button>
              <el-button link type="info" size="small" @click="dismissMatch(row)">忽略</el-button>
            </template>
          </el-table-column>
          <template #empty><el-empty description="暂无待处理的对账建议，先导入银行流水" /></template>
        </el-table>
      </el-tab-pane>

      <el-tab-pane label="银行流水" name="statements">
        <div class="panel-head">
          <el-radio-group v-model="statusFilter" size="small" @change="loadStatements">
            <el-radio-button label="">全部</el-radio-button>
            <el-radio-button label="unmatched">未匹配</el-radio-button>
            <el-radio-button label="matched">已对账</el-radio-button>
            <el-radio-button label="ignored">已忽略</el-radio-button>
          </el-radio-group>
        </div>
        <el-table v-loading="loading" :data="statements" size="small" border>
          <el-table-column prop="trans_date" label="交易日期" width="110" />
          <el-table-column label="方向" width="80" align="center">
            <template #default="{ row }">
              <el-tag size="small" :type="row.direction === 'in' ? 'success' : 'warning'" effect="plain">
                {{ row.direction === 'in' ? '收入' : '支出' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="金额" width="130" align="right">
            <template #default="{ row }">{{ money(row.amount) }}</template>
          </el-table-column>
          <el-table-column prop="counterparty" label="对方户名" min-width="160" />
          <el-table-column prop="ref_no" label="流水号" width="150" />
          <el-table-column prop="summary" label="摘要" min-width="140" />
          <el-table-column label="状态" width="100" align="center">
            <template #default="{ row }">
              <el-tag size="small" :type="statusTag[row.status] || 'info'">
                {{ statusLabel[row.status] || row.status }}
              </el-tag>
            </template>
          </el-table-column>
          <template #empty><el-empty description="还没有导入银行流水" /></template>
        </el-table>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<style scoped>
.head-actions {
  display: flex;
  align-items: center;
  gap: 10px;
}
.panel-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 10px;
}
</style>
