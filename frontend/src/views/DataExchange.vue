<script setup>
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { exchangeApi } from '../api'
import { saveBlob } from '../utils/download'

const datasets = [
  {
    key: 'products',
    label: '商品',
    title: '商品与期初库存',
    icon: 'Goods',
    perm: 'product:manage',
    desc: 'SKU 重复自动跳过；期初库存写入库存流水，保证账实一致',
  },
  {
    key: 'customers',
    label: '客户',
    title: '客户与期初应收',
    icon: 'User',
    perm: 'customer:manage',
    desc: '同名客户跳过建档，期初应收按导入值覆盖，不重复累加',
  },
  {
    key: 'suppliers',
    label: '供应商',
    title: '供应商与期初应付',
    icon: 'Van',
    perm: 'supplier:manage',
    desc: '同名供应商跳过建档，期初应付按导入值覆盖，不重复累加',
  },
]

const overview = ref({
  products: 0,
  customers: 0,
  suppliers: 0,
  opening_receivable: 0,
  opening_payable: 0,
  warehouse_ready: true,
})
const loadingOverview = ref(false)
const importing = reactive({})
const results = reactive({})
const uploadRefs = reactive({})
const previewing = reactive({})
const previews = reactive({})
const pendingFiles = reactive({})

const logs = ref([])
const logsLoading = ref(false)

const resultMap = {
  success: { text: '成功', type: 'success' },
  failed: { text: '失败', type: 'warning' },
  denied: { text: '已拦截', type: 'danger' },
}

async function loadOverview() {
  loadingOverview.value = true
  try {
    overview.value = await exchangeApi.overview()
  } finally {
    loadingOverview.value = false
  }
}

async function loadLogs() {
  logsLoading.value = true
  try {
    const data = await exchangeApi.logs(10)
    logs.value = data.items || []
  } finally {
    logsLoading.value = false
  }
}

function refresh() {
  loadOverview()
  loadLogs()
}

function timeText(value) {
  return value?.replace('T', ' ').slice(0, 19) || ''
}

function money(value) {
  return `¥${Number(value || 0).toFixed(2)}`
}

async function downloadTemplate(item) {
  try {
    const blob = await exchangeApi.templateUrl(item.key)
    saveBlob(blob, `${item.label}期初导入模板.xlsx`)
    loadLogs()
  } catch {
    /* 错误提示由请求拦截器统一处理 */
  }
}

async function onFile(item, uploadFile) {
  if (!uploadFile?.raw) return
  importing[item.key] = true
  try {
    results[item.key] = await exchangeApi.import(item.key, uploadFile.raw)
    const result = results[item.key]
    ElMessage.success(
      `导入完成：新增 ${result.created} 条，更新期初 ${result.updated} 条，跳过 ${result.skipped} 条`
    )
    await loadOverview()
  } catch {
    results[item.key] = null
  } finally {
    importing[item.key] = false
    uploadRefs[item.key]?.clearFiles()
    loadLogs()
  }
}

async function onPreview(item, uploadFile) {
  if (!uploadFile?.raw) return
  previewing[item.key] = true
  try {
    pendingFiles[item.key] = uploadFile.raw
    previews[item.key] = await exchangeApi.preview(item.key, uploadFile.raw)
    const pv = previews[item.key]
    if (pv.error_count) {
      ElMessage.warning(`校验发现 ${pv.error_count} 行错误，请下载错误报告修改后重试`)
    } else {
      ElMessage.success('校验通过，可执行导入')
    }
  } catch {
    previews[item.key] = null
    pendingFiles[item.key] = null
  } finally {
    previewing[item.key] = false
  }
}

async function downloadErrorReport(item) {
  const file = pendingFiles[item.key]
  if (!file) {
    ElMessage.warning('请先执行一次导入前校验')
    return
  }
  try {
    const blob = await exchangeApi.errorReport(item.key, file)
    saveBlob(blob, `${item.label}导入错误报告.xlsx`)
  } catch {
    /* 错误提示由请求拦截器统一处理 */
  }
}

// ── 期末导出 ──────────────────────────────────────────────
const sheets = [
  '概览',
  '商品主数据',
  '客户主数据',
  '供应商主数据',
  '库存余额',
  '库存流水',
  '销售订单',
  '销售订单明细',
  '采购订单',
  '采购订单明细',
  '销售退货',
  '采购退货',
  '收款记录',
  '付款记录',
  '应收账龄',
  '应付账龄',
  '期初余额',
]

const range = ref([])
const quarterOffset = ref(0)
const exporting = ref(false)

function ymd(date) {
  const y = date.getFullYear()
  const m = String(date.getMonth() + 1).padStart(2, '0')
  const d = String(date.getDate()).padStart(2, '0')
  return `${y}-${m}-${d}`
}

function setQuarter(offset) {
  const now = new Date()
  const startMonth = Math.floor(now.getMonth() / 3) * 3 + offset * 3
  const start = new Date(now.getFullYear(), startMonth, 1)
  const end = new Date(start.getFullYear(), start.getMonth() + 3, 0)
  quarterOffset.value = offset
  range.value = [ymd(start), ymd(end)]
}

async function exportData() {
  if (!range.value?.length) {
    ElMessage.warning('请先选择导出区间')
    return
  }
  exporting.value = true
  try {
    const blob = await exchangeApi.export({ start: range.value[0], end: range.value[1] })
    saveBlob(blob, `数据导出_${range.value[0]}_${range.value[1]}.xlsx`)
    ElMessage.success('导出完成，文件已开始下载')
  } catch {
    /* 错误提示由请求拦截器统一处理 */
  } finally {
    exporting.value = false
    loadLogs()
  }
}

onMounted(() => {
  setQuarter(0)
  refresh()
})
</script>

<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h1 class="page-title">数据交换</h1>
        <p class="page-subtitle">期初导入历史底账，期末一键导出全部业务数据</p>
      </div>
      <el-button :loading="loadingOverview || logsLoading" @click="refresh">
        <el-icon><Refresh /></el-icon>&nbsp;刷新
      </el-button>
    </div>

    <el-alert
      v-if="!overview.warehouse_ready"
      type="warning"
      show-icon
      :closable="false"
      class="notice"
      title="还没有可用的仓库或库位，商品期初库存暂不能导入"
      description="请先到「系统 → 仓库与库位」新建仓库并添加库位，再执行商品期初导入。"
    />

    <div class="stat-strip">
      <div class="stat">
        <span>商品</span>
        <b>{{ overview.products }}</b>
      </div>
      <div class="stat">
        <span>客户</span>
        <b>{{ overview.customers }}</b>
      </div>
      <div class="stat">
        <span>供应商</span>
        <b>{{ overview.suppliers }}</b>
      </div>
      <div class="stat">
        <span>期初应收</span>
        <b class="warn">{{ money(overview.opening_receivable) }}</b>
      </div>
      <div class="stat">
        <span>期初应付</span>
        <b class="warn">{{ money(overview.opening_payable) }}</b>
      </div>
    </div>

    <el-row :gutter="16">
      <el-col :xs="24" :lg="14">
        <div class="card panel">
          <div class="panel-head">
            <div>
              <h2>期初导入</h2>
              <p>客户首次上线，或季度开始启用新账套时使用</p>
            </div>
            <el-tag type="warning" effect="plain" size="small">一次性带入</el-tag>
          </div>

          <div class="order-line">
            建议顺序：仓库与库位 → 商品 → 客户 → 供应商
          </div>

          <div v-for="item in datasets" :key="item.key" class="dataset">
            <div class="dataset-icon">
              <el-icon><component :is="item.icon" /></el-icon>
            </div>
            <div class="dataset-main">
              <div class="dataset-title">{{ item.title }}</div>
              <div class="dataset-desc">{{ item.desc }}</div>
              <div class="dataset-actions">
                <el-button size="small" v-perm="item.perm" @click="downloadTemplate(item)">
                  <el-icon><Download /></el-icon>&nbsp;下载模板
                </el-button>
                <el-upload
                  :ref="(el) => (uploadRefs[item.key] = el)"
                  :show-file-list="false"
                  :auto-upload="false"
                  accept=".xlsx"
                  :on-change="(file) => onFile(item, file)"
                >
                  <el-button size="small" type="primary" :loading="importing[item.key]" v-perm="item.perm">
                    <el-icon><Upload /></el-icon>&nbsp;选择文件导入
                  </el-button>
                </el-upload>
                <el-upload
                  :show-file-list="false"
                  :auto-upload="false"
                  accept=".xlsx"
                  :on-change="(file) => onPreview(item, file)"
                >
                  <el-button size="small" :loading="previewing[item.key]" v-perm="item.perm">
                    <el-icon><Search /></el-icon>&nbsp;导入前校验
                  </el-button>
                </el-upload>
              </div>

              <div v-if="results[item.key]" class="result">
                <p class="result-line">
                  新增 <b class="ok">{{ results[item.key].created }}</b> 条
                  <template v-if="results[item.key].updated">，更新期初 <b>{{ results[item.key].updated }}</b> 条</template>
                  <template v-if="results[item.key].skipped">，跳过 {{ results[item.key].skipped }} 条</template>
                  <template v-if="results[item.key].error_count">
                    <span class="err">
                      ，失败 {{ results[item.key].error_count - results[item.key].skipped }} 条
                    </span>
                  </template>
                </p>
                <el-table
                  v-if="results[item.key].errors.length"
                  :data="results[item.key].errors"
                  size="small"
                  max-height="180"
                >
                  <el-table-column prop="row" label="行号" width="70" />
                  <el-table-column prop="reason" label="原因" min-width="180" show-overflow-tooltip />
                </el-table>
              </div>

              <div v-if="previews[item.key]" class="result">
                <p class="result-line">
                  校验：共 {{ previews[item.key].total }} 行
                  <template v-if="previews[item.key].error_count">
                    ，<span class="err">错误 {{ previews[item.key].error_count }} 行</span>
                  </template>
                  <template v-else>
                    ，<span class="ok">全部通过，可直接导入</span>
                  </template>
                </p>
                <el-button
                  v-if="previews[item.key].error_count"
                  size="small"
                  type="warning"
                  plain
                  @click="downloadErrorReport(item)"
                >
                  <el-icon><Download /></el-icon>&nbsp;下载错误报告
                </el-button>
                <el-table
                  v-if="previews[item.key].errors.length"
                  :data="previews[item.key].errors"
                  size="small"
                  max-height="180"
                  class="preview-table"
                >
                  <el-table-column prop="row" label="行号" width="70" />
                  <el-table-column prop="reason" label="原因" min-width="180" show-overflow-tooltip />
                </el-table>
              </div>
            </div>
          </div>
        </div>
      </el-col>

      <el-col :xs="24" :lg="10">
        <div class="card panel">
          <div class="panel-head">
            <div>
              <h2>期末导出</h2>
              <p>季度结束或需要交账时，导出为一个多 Sheet Excel</p>
            </div>
            <el-tag type="success" effect="plain" size="small">一键归档</el-tag>
          </div>

          <div class="quick">
            <el-button size="small" :type="quarterOffset === 0 ? 'primary' : ''" plain @click="setQuarter(0)">
              本季度
            </el-button>
            <el-button size="small" :type="quarterOffset === -1 ? 'primary' : ''" plain @click="setQuarter(-1)">
              上季度
            </el-button>
            <el-button size="small" :type="quarterOffset === -2 ? 'primary' : ''" plain @click="setQuarter(-2)">
              前两季度
            </el-button>
          </div>

          <el-date-picker
            v-model="range"
            type="daterange"
            value-format="YYYY-MM-DD"
            start-placeholder="开始日期"
            end-placeholder="结束日期"
            class="range"
            @change="quarterOffset = null"
          />

          <div class="sheets">
            <span v-for="sheet in sheets" :key="sheet" class="sheet-chip">{{ sheet }}</span>
          </div>

          <el-button
            type="primary"
            class="export-btn"
            :loading="exporting"
            v-perm="'finance:view'"
            @click="exportData"
          >
            <el-icon><Download /></el-icon>&nbsp;导出本期数据
          </el-button>
          <p class="hint">单次最长 400 天；文件包含 {{ sheets.length }} 个工作表，可直接交给会计归档。</p>
        </div>
      </el-col>
    </el-row>

    <div class="card panel logs-panel">
      <div class="panel-head">
        <div>
          <h2>最近导入导出</h2>
          <p>记录模板下载、期初导入与期末导出，成功和失败都会留痕</p>
        </div>
        <el-button size="small" text :loading="logsLoading" @click="loadLogs">刷新</el-button>
      </div>

      <el-table :data="logs" v-loading="logsLoading" size="small" empty-text="暂无导入导出记录">
        <el-table-column label="时间" width="165">
          <template #default="{ row }">
            <span class="muted">{{ timeText(row.created_at) }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="action" label="操作" width="130" />
        <el-table-column label="结果" width="86">
          <template #default="{ row }">
            <el-tag size="small" :type="(resultMap[row.result] || resultMap.success).type" effect="plain">
              {{ (resultMap[row.result] || resultMap.success).text }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="username" label="操作人" width="110" />
        <el-table-column prop="detail" label="详情" min-width="260" show-overflow-tooltip />
      </el-table>
    </div>
  </div>
</template>

<style scoped>
.notice {
  margin-bottom: 14px;
}

.stat-strip {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 12px;
  margin-bottom: 16px;
}

.stat {
  background: var(--card);
  border: 1px solid var(--border-light);
  border-radius: 10px;
  padding: 12px 14px;
  box-shadow: var(--shadow-card);
}

.stat span {
  display: block;
  font-size: 12px;
  color: var(--text-secondary);
}

.stat b {
  display: block;
  margin-top: 4px;
  font-size: 18px;
  color: var(--text);
}

.stat b.warn {
  color: var(--warning);
}

.panel {
  padding: 16px 18px 18px;
  height: 100%;
}

.logs-panel {
  margin-top: 16px;
  height: auto;
}

.panel-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 10px;
  margin-bottom: 10px;
}

.panel-head h2 {
  font-size: 15px;
  font-weight: 600;
  margin: 0;
}

.panel-head p {
  margin: 4px 0 0;
  font-size: 12px;
  color: var(--text-secondary);
}

.order-line {
  font-size: 12px;
  color: var(--text-secondary);
  background: var(--brand-soft);
  border-radius: 8px;
  padding: 8px 10px;
  margin-bottom: 12px;
}

.dataset {
  display: flex;
  gap: 12px;
  padding: 12px 0;
  border-top: 1px solid var(--border-light);
}

.dataset:first-of-type {
  border-top: none;
}

.dataset-icon {
  width: 36px;
  height: 36px;
  flex: none;
  border-radius: 9px;
  background: var(--brand-soft);
  color: var(--brand);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 17px;
}

.dataset-main {
  flex: 1;
  min-width: 0;
}

.dataset-title {
  font-size: 13.5px;
  font-weight: 600;
}

.dataset-desc {
  font-size: 12px;
  color: var(--text-secondary);
  margin-top: 3px;
  line-height: 1.6;
}

.dataset-actions {
  display: flex;
  gap: 8px;
  margin-top: 9px;
  flex-wrap: wrap;
}

.result {
  margin-top: 10px;
  background: #fafbff;
  border: 1px solid var(--border-light);
  border-radius: 8px;
  padding: 8px 10px;
}

.result-line {
  margin: 0 0 6px;
  font-size: 12px;
  color: var(--text-secondary);
}

.result-line .ok {
  color: var(--success);
}

.result-line .err {
  color: var(--danger);
}

.preview-table {
  margin-top: 8px;
}

.quick {
  display: flex;
  gap: 8px;
  margin: 12px 0 10px;
}

.range {
  width: 100%;
}

.sheets {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin: 12px 0;
}

.sheet-chip {
  font-size: 11px;
  color: var(--text-secondary);
  background: #f4f5fa;
  border: 1px solid var(--border-light);
  border-radius: 999px;
  padding: 3px 9px;
}

.export-btn {
  width: 100%;
}

.hint {
  margin: 8px 0 0;
  font-size: 11.5px;
  color: var(--text-muted);
  line-height: 1.6;
}

@media (max-width: 900px) {
  .stat-strip {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
